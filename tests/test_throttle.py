import threading
import time

import pytest

from tests.conftest import make_app
from ytdlui.config import ConfigError, load_settings
from ytdlui.throttle import MB, Bandwidth, ytdlp_rate


def test_unlimited_is_instant():
    bw = Bandwidth(0)
    assert not bw.enabled
    t = time.monotonic()
    for _ in range(100):
        bw.consume(10 * MB)
    assert time.monotonic() - t < 0.2
    assert bw.bytes_total == 1000 * MB


def test_rate_is_enforced():
    bw = Bandwidth(2)                     # 2 MB/s, burst = 1 MB
    t = time.monotonic()
    for _ in range(32):
        bw.consume(62_500)                # 2 MB total → ~0.5 s after the 1 MB burst
    elapsed = time.monotonic() - t
    assert 0.4 <= elapsed <= 0.9, elapsed
    assert bw.current_rate() > 0


def test_cap_is_shared_between_streams():
    bw = Bandwidth(2)
    def pull():
        for _ in range(16):
            bw.consume(62_500)            # 1 MB per thread, 2 MB together
    threads = [threading.Thread(target=pull) for _ in range(2)]
    t = time.monotonic()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert time.monotonic() - t >= 0.4   # together they still can't beat 2 MB/s


def test_wrap_is_byte_exact_and_closes_source():
    closed = []

    class Source:
        def __iter__(self):
            yield b"a" * 200_000
            yield b""
            yield b"b" * 10

        def close(self):
            closed.append(True)

    out = b"".join(Bandwidth(50).wrap(Source()))
    assert out == b"a" * 200_000 + b"b" * 10
    assert closed == [True]


def test_ytdlp_rate():
    assert ytdlp_rate(10, 2) == 5_000_000
    assert ytdlp_rate(10, 1) == 10_000_000
    assert ytdlp_rate(0, 2) is None
    assert ytdlp_rate(0.01, 50) == 64 * 1024           # never absurdly low


def test_config_values():
    assert load_settings(None, {}, environ={}).bandwidth_limit == 10.0
    assert load_settings(None, {}, environ={"YTDLUI_BANDWIDTH_LIMIT": "2.5"}).bandwidth_limit == 2.5
    assert load_settings(None, {"bandwidth_limit": "0"}, environ={}).bandwidth_limit == 0.0
    for bad in ("-1", "fast", "nan", "inf"):
        with pytest.raises(ConfigError):
            load_settings(None, {"bandwidth_limit": bad}, environ={})


def test_ytdlp_jobs_get_limit_rate(app):
    svc = app.extensions["ytdlui"]
    from ytdlui.jobs import Job
    from ytdlui.ytdlp import Request
    job = Job(req=Request(url="https://example.com/v", format="mp4"), owner="t", work_dir="/tmp/x")
    cmd = svc.jobs.build_command(job)
    assert cmd[cmd.index("--limit-rate") + 1] == "5000000"      # 10 MB/s ÷ 2 job slots
    svc.settings.bandwidth_limit = 0
    assert "--limit-rate" not in svc.jobs.build_command(job)


def test_file_downloads_are_paced(settings, monkeypatch):
    import ytdlui.security as sec
    monkeypatch.setattr(sec, "resolve_host", lambda h, p: ["93.184.216.34"])
    settings.bandwidth_limit = 0.5                            # 500 KB/s, 262 KB burst
    app = make_app(settings)
    c = app.test_client()
    jid = c.post("/api/jobs", json={"url": "https://example.com/watch?v=1", "format": "mp4"}).get_json()["id"]
    for _ in range(200):
        if c.get(f"/api/jobs/{jid}").get_json()["status"] == "done":
            break
        time.sleep(0.05)
    job = app.extensions["ytdlui"].jobs.get(jid)
    payload = bytes(range(256)) * 3125                        # 800 KB
    with open(job.files[0]["path"], "wb") as fh:
        fh.write(payload)
    job.files[0]["size"] = len(payload)
    t = time.monotonic()
    r = c.get(f"/api/jobs/{jid}/file")
    body = r.get_data()
    elapsed = time.monotonic() - t
    assert body == payload
    assert elapsed >= 0.8, elapsed                            # (800 KB − 262 KB burst) / 500 KB/s ≈ 1.07 s
