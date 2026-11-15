import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ytdlui.config import load_settings  # noqa: E402

FAKE = os.path.join(os.path.dirname(__file__), "fake_ytdlp.py")


@pytest.fixture
def settings(tmp_path):
    return load_settings(None, {
        "data_dir": str(tmp_path / "data"),
        "update_check": False,
        "ytdlp_auto_update": False,
        "ffmpeg_auto_install": False,
        "min_free_space_mb": 0,
        "rate_limit": 1000,
        "download_rate_limit": 1000,
    }, environ={})


def make_app(settings):
    from ytdlui.web import create_app
    app = create_app(settings, start_background=False)
    svc = app.extensions["ytdlui"]
    svc.ytdlp.tools.ytdlp_cmd = [sys.executable, FAKE]
    svc.ytdlp.tools.ytdlp_kind = "custom"
    svc.ytdlp.tools.js_runtime = None
    svc.ytdlp._version = None
    app.config["TESTING"] = True
    return app


@pytest.fixture
def app(settings, monkeypatch):
    # Hosts in tests are fake; skip DNS but keep every other URL check.
    import ytdlui.security as sec
    monkeypatch.setattr(sec, "resolve_host", lambda host, port: ["93.184.216.34"])
    return make_app(settings)


@pytest.fixture
def client(app):
    return app.test_client()
