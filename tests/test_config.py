import pytest

from ytdlui.config import FIELDS, ConfigError, load_settings, render_example


def test_defaults():
    s = load_settings(None, {}, environ={})
    assert s.host == "127.0.0.1" and s.port == 5000
    assert s.processing_mode == "choice" and s.server_downloads_enabled and s.browser_processing_enabled


def test_precedence(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[server]\nport = 6000\nhost = 0.0.0.0\n[processing]\nprocessing_mode = browser\n")
    s = load_settings(str(ini), {}, environ={"YTDLUI_PORT": "7000"})
    assert s.host == "0.0.0.0"           # from file
    assert s.port == 7000                # env beats file
    s = load_settings(str(ini), {"port": "8000"}, environ={"YTDLUI_PORT": "7000"})
    assert s.port == 8000                # CLI beats env
    assert s.processing_mode == "browser"


def test_browser_only_mode_disables_server_downloads():
    s = load_settings(None, {"processing_mode": "browser", "fallback_to_server": "false"}, environ={})
    assert not s.server_downloads_enabled
    s = load_settings(None, {"processing_mode": "browser"}, environ={})
    assert s.server_downloads_enabled      # fallback keeps the server path available


def test_validation(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(None, {"port": "abc"}, environ={})
    with pytest.raises(ConfigError):
        load_settings(None, {"processing_mode": "gpu"}, environ={})
    with pytest.raises(ConfigError):
        load_settings(None, {"nope": "1"}, environ={})
    bad = tmp_path / "c.ini"
    bad.write_text("[x]\nunknown_key = 1\n")
    with pytest.raises(ConfigError, match="unknown"):
        load_settings(str(bad), {}, environ={})
    with pytest.raises(ConfigError, match="not found"):
        load_settings(str(tmp_path / "missing.ini"), {}, environ={})


def test_lists_and_bools():
    s = load_settings(None, {"allowed_sites": "youtube.com, youtu.be ,", "relay": "off"}, environ={})
    assert s.allowed_sites == ["youtube.com", "youtu.be"]
    assert s.relay is False


def test_password_with_hash_is_kept(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[security]\npassword = hunter2 #not-a-comment\n")
    assert load_settings(str(ini), {}, environ={}).password == "hunter2 #not-a-comment"


def test_example_config_covers_every_field():
    text = render_example()
    for f in FIELDS:
        assert f"# {f.name} = " in text


def test_output_never_crashes_on_legacy_code_pages():
    import io

    from ytdlui.cli import make_output_safe
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    make_output_safe(stream)
    print("yt-dlp 2025.04.30 → 2026.10.26  ➜  http://localhost:5000", file=stream, flush=True)
    assert b"2026.10.26" in raw.getvalue()
