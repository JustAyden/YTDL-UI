"""Update system.

* **yt-dlp** — checked every ``ytdlp_update_hours`` and upgraded while no downloads
  are running: with pip when yt-dlp lives in a writable (virtual) environment,
  otherwise by fetching the official release binary into ``data/bin`` (SHA-256
  verified against the release's SHA2-256SUMS). A missing yt-dlp is installed the
  same way.
* **ffmpeg** — optional one-time install of a static build into ``data/bin``.
* **YTDL-UI itself** — GitHub releases are checked every ``update_check_hours``.
  ``apply_app_update()`` knows three install styles: git checkout (``git pull
  --ff-only``), extracted source archive (download + copy over, keeping config and
  data), and the standalone executables (download the new binary, verify, swap,
  restart). Docker images are updated by pulling a new image instead.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from typing import Any, Callable

from . import __version__
from .config import APP_DIR, Settings
from .ytdlp import FROZEN, IS_WINDOWS, YtDlp, managed_ytdlp_path, parse_ytdlp_version

log = logging.getLogger("ytdlui")

UA = f"YTDL-UI/{__version__} (+https://github.com/JustAyden/YTDL-UI)"
PROTECTED = {"config.ini", "data", ".venv", "venv", ".git", "cookies.txt", ".tmp"}


# ── helpers ──────────────────────────────────────────────────────────────────
def parse_version(s: str) -> tuple[int, int, int] | None:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", s or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def http_get(url: str, timeout: int = 30, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https URLs)
        return r.read()


def http_json(url: str, timeout: int = 20) -> Any:
    return json.loads(http_get(url, timeout, "application/vnd.github+json, application/json"))


def download(url: str, dest: str, progress: Callable[[int, int], None] | None = None,
             timeout: int = 60) -> str:
    """Download to ``dest`` and return its SHA-256."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as out:  # noqa: S310
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            buf = r.read(1024 * 1024)
            if not buf:
                break
            out.write(buf)
            h.update(buf)
            done += len(buf)
            if progress:
                progress(done, total)
    return h.hexdigest()


def parse_sums(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[parts[-1].lstrip("*").split("/")[-1]] = parts[0].lower()
    return out


def machine() -> str:
    m = platform.machine().lower()
    if m in ("amd64", "x86_64", "x64"):
        return "x64"
    if m in ("arm64", "aarch64", "armv8l", "armv8b"):
        return "arm64"
    if m.startswith("armv7"):
        return "armv7"
    if m in ("i386", "i686", "x86"):
        return "x86"
    return m


def os_name() -> str:
    s = platform.system()
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(s, s.lower())


def is_docker() -> bool:
    return os.path.exists("/.dockerenv") or os.environ.get("YTDLUI_DOCKER") == "1"


def pip_can_write() -> bool:
    if FROZEN:
        return False
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    try:
        import yt_dlp  # noqa: F401
        target = os.path.dirname(os.path.dirname(sys.modules["yt_dlp"].__file__ or ""))
    except Exception:
        target = ""
    return bool(target) and os.access(target, os.W_OK) and (in_venv or is_docker())


# ── yt-dlp ───────────────────────────────────────────────────────────────────
def ytdlp_asset_name() -> str:
    o, m = os_name(), machine()
    if o == "windows":
        return {"x64": "yt-dlp.exe", "arm64": "yt-dlp_arm64.exe", "x86": "yt-dlp_x86.exe"}.get(m, "yt-dlp.exe")
    if o == "macos":
        return "yt-dlp_macos"
    if o == "linux":
        musl = "musl" in (platform.libc_ver()[0] or "") or os.path.exists("/etc/alpine-release")
        if m == "x64":
            return "yt-dlp_musllinux" if musl else "yt-dlp_linux"
        if m == "arm64":
            return "yt-dlp_musllinux_aarch64" if musl else "yt-dlp_linux_aarch64"
    return "yt-dlp"  # platform-independent zipapp, needs Python 3


class YtdlpUpdater:
    def __init__(self, settings: Settings, ytdlp: YtDlp, is_idle: Callable[[], bool]):
        self.s = settings
        self.ytdlp = ytdlp
        self.is_idle = is_idle
        self.latest: str | None = None
        self.checked_at: float | None = None
        self.last_result: str = ""
        self.busy = threading.Lock()

    @property
    def repo(self) -> str:
        return "yt-dlp/yt-dlp-nightly-builds" if self.s.ytdlp_channel == "nightly" else "yt-dlp/yt-dlp"

    def check(self) -> dict[str, Any]:
        try:
            rel = http_json(f"https://api.github.com/repos/{self.repo}/releases/latest")
            self.latest = rel.get("tag_name")
            self.checked_at = time.time()
        except (OSError, ValueError, urllib.error.URLError) as exc:
            log.info("yt-dlp update check failed: %s", exc)
        return self.status()

    def status(self) -> dict[str, Any]:
        cur = self.ytdlp.version()
        cv, lv = parse_ytdlp_version(cur or ""), parse_ytdlp_version(self.latest or "")
        return {
            "current": cur,
            "latest": self.latest,
            "update_available": bool(cv and lv and lv[:3] > cv[:3]) or (cur is None and bool(self.latest)),
            "kind": self.ytdlp.tools.ytdlp_kind,
            "location": self.ytdlp.tools.ytdlp_location,
            "channel": self.s.ytdlp_channel,
            "checked_at": self.checked_at,
            "auto": self.s.ytdlp_auto_update,
            "last_result": self.last_result,
            "method": self.method(),
        }

    def method(self) -> str:
        kind = self.ytdlp.tools.ytdlp_kind
        if kind == "custom":
            return "manual"
        if kind == "pip" and pip_can_write():
            return "pip"
        return "release-binary"

    def update(self, force: bool = False) -> dict[str, Any]:
        if not self.busy.acquire(blocking=False):
            return {"ok": False, "message": "An update is already running."}
        try:
            before = self.ytdlp.version()
            method = self.method()
            if method == "manual":
                msg = "yt-dlp path is set manually (ytdlp_path); update it yourself."
                self.last_result = msg
                return {"ok": False, "message": msg}
            if not force:
                st = self.check()
                if not st["update_available"]:
                    self.last_result = f"Already up to date ({before})."
                    return {"ok": True, "message": self.last_result, "changed": False}
            if method == "pip":
                self._pip_upgrade()
            else:
                self._install_release_binary()
            self.ytdlp.refresh()
            after = self.ytdlp.version()
            self.last_result = f"yt-dlp {before or 'not installed'} → {after}"
            log.info("Updated %s", self.last_result)
            return {"ok": True, "message": self.last_result, "changed": before != after}
        except Exception as exc:  # report, don't crash the server
            self.last_result = f"yt-dlp update failed: {exc}"
            log.warning(self.last_result)
            return {"ok": False, "message": self.last_result}
        finally:
            self.busy.release()

    def _pip_upgrade(self) -> None:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check",
               "--no-input", "yt-dlp[default]"]
        if self.s.ytdlp_channel == "nightly":
            cmd.insert(4, "--pre")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=self.ytdlp.env())
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1][:300])

    def _install_release_binary(self) -> None:
        rel = http_json(f"https://api.github.com/repos/{self.repo}/releases/latest")
        assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
        name = ytdlp_asset_name()
        if name not in assets:
            name = "yt-dlp"
        sums = parse_sums(http_get(assets["SHA2-256SUMS"]).decode())
        expected = sums.get(name)
        if not expected:
            raise RuntimeError(f"No checksum published for {name}")
        dest = managed_ytdlp_path(self.s) if name != "yt-dlp" or IS_WINDOWS else \
            os.path.join(self.s.data_path, "bin", "yt-dlp.pyz")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".download"
        got = download(assets[name], tmp, timeout=120)
        if got != expected:
            os.remove(tmp)
            raise RuntimeError("Checksum mismatch on downloaded yt-dlp — not installed.")
        os.chmod(tmp, os.stat(tmp).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        _replace(tmp, dest)

    def loop(self, stop: threading.Event) -> None:
        if self.ytdlp.tools.ytdlp_kind == "missing":
            log.info("yt-dlp not found — installing the official release binary…")
            self.update(force=True)
        if stop.wait(90):
            return
        while True:
            if self.s.ytdlp_auto_update:
                st = self.check()
                if st["update_available"]:
                    waited = 0
                    while not self.is_idle() and waited < 1800:
                        if stop.wait(30):
                            return
                        waited += 30
                    if self.is_idle():
                        self.update(force=True)
            if stop.wait(max(1, self.s.ytdlp_update_hours) * 3600):
                return


# ── ffmpeg ───────────────────────────────────────────────────────────────────
def ffmpeg_asset() -> str | None:
    o, m = os_name(), machine()
    if o == "windows":
        return {"x64": "ffmpeg-master-latest-win64-gpl.zip",
                "arm64": "ffmpeg-master-latest-winarm64-gpl.zip",
                "x86": "ffmpeg-master-latest-win32-gpl.zip"}.get(m)
    if o == "linux":
        return {"x64": "ffmpeg-master-latest-linux64-gpl.tar.xz",
                "arm64": "ffmpeg-master-latest-linuxarm64-gpl.tar.xz"}.get(m)
    return None


def install_ffmpeg(settings: Settings, progress: Callable[[int, int], None] | None = None) -> str:
    asset = ffmpeg_asset()
    if not asset:
        if os_name() == "macos":
            raise RuntimeError("Install ffmpeg with Homebrew: brew install ffmpeg")
        raise RuntimeError(f"No prebuilt ffmpeg for {platform.system()} {platform.machine()}; "
                           "install it with your package manager.")
    rel = http_json("https://api.github.com/repos/yt-dlp/FFmpeg-Builds/releases/latest")
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    sums = parse_sums(http_get(assets["checksums.sha256"]).decode())
    bindir = os.path.join(settings.data_path, "bin")
    os.makedirs(bindir, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.data_path) as td:
        archive = os.path.join(td, asset)
        got = download(assets[asset], archive, progress, timeout=120)
        if sums.get(asset) != got:
            raise RuntimeError("Checksum mismatch on downloaded ffmpeg — not installed.")
        wanted = {"ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"}
        if archive.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                for n in zf.namelist():
                    base = n.rsplit("/", 1)[-1]
                    if base in wanted and "/bin/" in n:
                        with zf.open(n) as src, open(os.path.join(bindir, base), "wb") as dst:
                            shutil.copyfileobj(src, dst)
        else:
            with tarfile.open(archive) as tf:
                for m in tf.getmembers():
                    base = m.name.rsplit("/", 1)[-1]
                    if base in wanted and "/bin/" in m.name and m.isfile():
                        src = tf.extractfile(m)
                        assert src is not None
                        dst_path = os.path.join(bindir, base)
                        with open(dst_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        os.chmod(dst_path, 0o755)
    path = os.path.join(bindir, "ffmpeg.exe" if IS_WINDOWS else "ffmpeg")
    if not os.path.isfile(path):
        raise RuntimeError("ffmpeg wasn't found inside the downloaded archive.")
    return path


# ── YTDL-UI ──────────────────────────────────────────────────────────────────
def release_asset_name() -> str:
    o, m = os_name(), machine()
    return f"ytdl-ui-{o}-{m}" + (".exe" if o == "windows" else "")


def install_method() -> str:
    if is_docker():
        return "docker"
    if FROZEN:
        return "binary"
    if os.path.isdir(os.path.join(APP_DIR, ".git")) and shutil.which("git"):
        return "git"
    return "archive"


class AppUpdater:
    def __init__(self, settings: Settings):
        self.s = settings
        self.state_path = os.path.join(settings.data_path, "update-state.json")
        self.latest: dict[str, Any] | None = None
        self.checked_at: float | None = None
        self.error: str | None = None
        self.busy = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                st = json.load(fh)
            self.latest, self.checked_at = st.get("latest"), st.get("checked_at")
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            with open(self.state_path, "w", encoding="utf-8") as fh:
                json.dump({"latest": self.latest, "checked_at": self.checked_at}, fh)
        except OSError:
            pass

    def check(self) -> dict[str, Any]:
        try:
            rel = http_json(f"https://api.github.com/repos/{self.s.update_repo}/releases/latest")
            self.latest = {
                "tag": rel.get("tag_name"),
                "name": rel.get("name") or rel.get("tag_name"),
                "url": rel.get("html_url"),
                "published": rel.get("published_at"),
                "notes": (rel.get("body") or "")[:4000],
                "zipball": rel.get("zipball_url"),
                "assets": {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])},
            }
            self.checked_at = time.time()
            self.error = None
            self._save()
            st = self.status()
            if st["update_available"]:
                log.info("YTDL-UI %s is available (running %s): %s",
                         st["latest"], __version__, self.latest.get("url"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            self.error = str(exc)
            log.info("Update check failed: %s", exc)
        return self.status()

    def status(self) -> dict[str, Any]:
        latest_tag = (self.latest or {}).get("tag")
        cur, new = parse_version(__version__), parse_version(latest_tag or "")
        method = install_method()
        return {
            "current": __version__,
            "latest": latest_tag,
            "latest_name": (self.latest or {}).get("name"),
            "url": (self.latest or {}).get("url"),
            "notes": (self.latest or {}).get("notes", ""),
            "published": (self.latest or {}).get("published"),
            "update_available": bool(cur and new and new > cur),
            "checked_at": self.checked_at,
            "error": self.error,
            "method": method,
            "can_apply": self.s.allow_web_update and method != "docker",
        }

    def loop(self, stop: threading.Event) -> None:
        if stop.wait(20):
            return
        while self.s.update_check:
            self.check()
            if stop.wait(max(1, self.s.update_check_hours) * 3600):
                return

    # ── applying ────────────────────────────────────────────────────────
    def apply(self) -> dict[str, Any]:
        """Install the latest release. Returns {"ok", "message", "restart"}."""
        if not self.busy.acquire(blocking=False):
            return {"ok": False, "message": "An update is already running."}
        try:
            method = install_method()
            if method == "docker":
                return {"ok": False, "message": "Running in Docker: pull the new image and recreate "
                                                "the container (docker compose pull && docker compose up -d)."}
            st = self.check()
            if method != "git" and not st["update_available"]:
                return {"ok": True, "message": f"Already on the latest version ({__version__}).", "restart": False}
            if method == "git":
                msg = self._apply_git()
            elif method == "binary":
                msg = self._apply_binary()
            else:
                msg = self._apply_archive()
            if method != "binary":
                self._pip_requirements()
            log.info("Self-update: %s", msg)
            return {"ok": True, "message": msg, "restart": True}
        except Exception as exc:
            log.warning("Self-update failed: %s", exc)
            return {"ok": False, "message": f"Update failed: {exc}"}
        finally:
            self.busy.release()

    def _git(self, *args: str) -> str:
        r = subprocess.run(["git", "-C", APP_DIR, *args], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[:400])
        return r.stdout.strip()

    def _apply_git(self) -> str:
        dirty = self._git("status", "--porcelain", "--untracked-files=no")
        if dirty:
            raise RuntimeError("The app folder has local code changes; commit or stash them first.")
        before = self._git("rev-parse", "--short", "HEAD")
        self._git("pull", "--ff-only")
        after = self._git("rev-parse", "--short", "HEAD")
        if before == after:
            return f"Already up to date (git {after})."
        return f"Updated git checkout {before} → {after}."

    def _apply_archive(self) -> str:
        if not self.latest or not self.latest.get("zipball"):
            raise RuntimeError("No release archive found.")
        data = http_get(self.latest["zipball"], timeout=120)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            root = names[0].split("/", 1)[0] + "/"
            for n in names:
                rel = n[len(root):]
                if not rel or n.endswith("/"):
                    continue
                top = rel.split("/", 1)[0]
                if top in PROTECTED or ".." in rel.split("/"):
                    continue
                dest = os.path.join(APP_DIR, *rel.split("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(n) as src, open(dest + ".new", "wb") as out:
                    shutil.copyfileobj(src, out)
                _replace(dest + ".new", dest)
        return f"Installed {self.latest.get('tag')} from the release archive."

    def _apply_binary(self) -> str:
        assert self.latest
        assets = self.latest.get("assets") or {}
        name = release_asset_name()
        if name not in assets:
            raise RuntimeError(f"This release has no build for {os_name()} {machine()} ({name}).")
        sums_name = next((n for n in assets if n.upper().startswith("SHA256SUMS")), None)
        if not sums_name:
            raise RuntimeError("Release has no SHA256SUMS file; refusing to install unverified binary.")
        expected = parse_sums(http_get(assets[sums_name]).decode()).get(name)
        exe = sys.executable
        tmp = exe + ".new"
        got = download(assets[name], tmp, timeout=120)
        if got != expected:
            os.remove(tmp)
            raise RuntimeError("Checksum mismatch — update not installed.")
        os.chmod(tmp, os.stat(exe).st_mode | stat.S_IXUSR)
        old = exe + ".old"
        if os.path.exists(old):
            try:
                os.remove(old)
            except OSError:
                pass
        os.replace(exe, old) if not IS_WINDOWS else os.rename(exe, old)
        os.replace(tmp, exe)
        return f"Installed {self.latest.get('tag')}."

    def _pip_requirements(self) -> None:
        req = os.path.join(APP_DIR, "requirements.txt")
        if not os.path.isfile(req):
            return
        if not pip_can_write():
            log.warning("Can't install updated Python dependencies automatically here; run: "
                        "%s -m pip install -r %s", sys.executable, req)
            return
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                            "--no-input", "-r", req], capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            log.warning("Updating Python dependencies failed: %s", (r.stderr or r.stdout).strip()[-400:])


def _replace(src: str, dst: str) -> None:
    try:
        os.replace(src, dst)
    except PermissionError:
        if IS_WINDOWS and os.path.exists(dst):  # file in use: move it aside first
            old = dst + ".old"
            if os.path.exists(old):
                os.remove(old)
            os.rename(dst, old)
            os.replace(src, dst)
        else:
            raise


def cleanup_old_binaries(settings: Settings) -> None:
    """Remove *.old files left behind by a previous self-update (Windows can't delete in-use files)."""
    candidates = [managed_ytdlp_path(settings) + ".old"]
    if FROZEN:
        candidates.append(sys.executable + ".old")
    for p in candidates:
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def restart_process(delay: float = 1.0, before: Callable[[], None] | None = None) -> None:
    """Re-exec the running server (same arguments) after ``delay`` seconds."""
    def _go() -> None:
        time.sleep(delay)
        if before:
            try:
                before()
            except Exception:
                pass
        log.info("Restarting…")
        logging.shutdown()
        rest = [a for a in sys.argv[1:] if a != "--wait-for-port"]
        if FROZEN:
            args = [sys.executable] + rest
        elif sys.argv[0].endswith("__main__.py"):
            args = [sys.executable, "-m", "ytdlui"] + rest
        else:
            args = [sys.executable, sys.argv[0]] + rest
        if IS_WINDOWS:
            subprocess.Popen(args + ["--wait-for-port"], close_fds=True,
                             creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_PROCESS_GROUP
            os._exit(0)
        os.execv(sys.executable, args + ["--wait-for-port"])
    threading.Thread(target=_go, name="restart", daemon=True).start()
