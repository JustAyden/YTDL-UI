"""Build the standalone executable for the current OS/CPU and checksum it.

    python packaging/build.py            # -> dist/ytdl-ui-<os>-<arch>[.exe] + dist/SHA256SUMS-<os>-<arch>.txt

Run it on each target platform (CI does this for Windows, macOS and Linux on
x64 and arm64). PyInstaller can't cross-compile, so each build needs its own OS.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from ytdlui import __version__  # noqa: E402
from ytdlui.updater import machine, os_name, release_asset_name  # noqa: E402


def write_version_info(path: str) -> None:
    parts = [int(x) for x in __version__.split(".")[:3]] + [0]
    t = tuple(parts[:4])
    path_txt = f"""FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004,
  fileType=0x1, subtype=0x0, date=(0, 0))"""
    content = f"""# UTF-8
VSVersionInfo(
  ffi={path_txt},
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'YTDL-UI'),
      StringStruct('FileDescription', 'YTDL-UI - self-hosted yt-dlp web UI'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'ytdl-ui'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'ytdl-ui.exe'),
      StringStruct('ProductName', 'YTDL-UI'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for buf in iter(lambda: fh.read(1 << 20), b""):
            h.update(buf)
    return h.hexdigest()


def main() -> int:
    asset = os.environ.get("YTDLUI_ASSET_NAME") or release_asset_name()
    stem = asset[:-4] if asset.endswith(".exe") else asset
    if not os.path.exists(os.path.join(HERE, "icon.png")):
        try:
            subprocess.check_call([sys.executable, os.path.join(HERE, "make_icons.py")])
        except Exception as exc:  # Pillow missing: build without an icon
            print(f"(skipping icons: {exc})")
    if os_name() == "windows":
        write_version_info(os.path.join(HERE, "version_info.txt"))

    env = dict(os.environ, YTDLUI_EXE_NAME=stem)
    subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                           "--distpath", os.path.join(ROOT, "dist"),
                           "--workpath", os.path.join(ROOT, "build"),
                           os.path.join(HERE, "ytdl-ui.spec")], env=env, cwd=ROOT)
    out = os.path.join(ROOT, "dist", asset)
    if not os.path.isfile(out):
        raise SystemExit(f"expected build output missing: {out}")
    digest = sha256(out)
    sums = os.path.join(ROOT, "dist", f"SHA256SUMS-{os_name()}-{machine()}.txt")
    with open(sums, "w", encoding="utf-8") as fh:
        fh.write(f"{digest}  {asset}\n")
    print(f"\nBuilt {out}\n  {os.path.getsize(out) / 1e6:.1f} MB  sha256 {digest}")
    print(f"  {platform.system()} {platform.machine()}  Python {platform.python_version()}")
    shutil.rmtree(os.path.join(ROOT, "build"), ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
