"""Smoke-test a built executable: versions, bundled yt-dlp, and the web server.

    python packaging/smoke_test.py dist/ytdl-ui-linux-x64
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(exe: str) -> int:
    exe = os.path.abspath(exe)
    work = tempfile.mkdtemp(prefix="ytdlui-smoke-")
    target = os.path.join(work, os.path.basename(exe))
    shutil.copy2(exe, target)
    os.chmod(target, 0o755)

    out = subprocess.run([target, "--version"], capture_output=True, text=True, timeout=120)
    print(out.stdout, out.stderr)
    assert out.returncode == 0 and "YTDL-UI" in out.stdout, "--version failed"

    yt = subprocess.run([target, "--run-ytdlp", "--version"], capture_output=True, text=True, timeout=120)
    print("bundled yt-dlp:", yt.stdout.strip())
    assert yt.returncode == 0 and yt.stdout.strip()[:4].isdigit(), "bundled yt-dlp failed"

    port = free_port()
    proc = subprocess.Popen([target, "--port", str(port), "--set", "update_check=false",
                             "--set", "ytdlp_auto_update=false", "--set", "ffmpeg_auto_install=false"],
                            cwd=work)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                with urllib.request.urlopen(base + "/api/health", timeout=2) as r:
                    health = json.load(r)
                    break
            except OSError:
                time.sleep(1)
        else:
            raise AssertionError("server never became healthy")
        print("health:", health)
        with urllib.request.urlopen(base + "/", timeout=5) as r:
            assert b"Download anything." in r.read()
        with urllib.request.urlopen(base + "/static/js/app.js", timeout=5) as r:
            assert r.status == 200
        with urllib.request.urlopen(base + "/api/config", timeout=5) as r:
            cfg = json.load(r)
            assert cfg["ytdlp_ok"], "server can't find yt-dlp"
        assert os.path.isdir(os.path.join(work, "data")), "data dir not created next to the executable"
        print("smoke test passed")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
