import io
import socket
import zipfile

from ytdlui import updater
from ytdlui.relay import TokenStore, _direct_ok, _valid_range
from ytdlui.updater import AppUpdater, parse_sums, parse_version
from ytdlui.ziputil import stream_zip


def test_parse_version():
    assert parse_version("v1.2.1-rc") == (1, 2, 1)
    assert parse_version("2.0") == (2, 0, 0)
    assert parse_version("hostversion") is None
    assert parse_version("v2.10.0") > parse_version("v2.9.9")


def test_parse_sums():
    text = "a" * 64 + "  yt-dlp.exe\n" + "b" * 64 + " *dist/ytdl-ui-linux-x64\nnot a line\n"
    assert parse_sums(text) == {"yt-dlp.exe": "a" * 64, "ytdl-ui-linux-x64": "b" * 64}


def test_app_update_status(settings, monkeypatch):
    up = AppUpdater(settings)
    monkeypatch.setattr(updater, "http_json", lambda url, timeout=20: {
        "tag_name": "v9.0.0", "name": "big", "html_url": "https://github.com/x", "body": "notes", "assets": []})
    st = up.check()
    assert st["update_available"] and st["latest"] == "v9.0.0" and not st["can_apply"]
    monkeypatch.setattr(updater, "http_json", lambda url, timeout=20: {"tag_name": "v1.2.1-rc", "assets": []})
    assert not up.check()["update_available"]


def test_asset_names(monkeypatch):
    monkeypatch.setattr(updater.platform, "system", lambda: "Windows")
    monkeypatch.setattr(updater.platform, "machine", lambda: "AMD64")
    assert updater.ytdlp_asset_name() == "yt-dlp.exe"
    assert updater.release_asset_name() == "ytdl-ui-windows-x64.exe"
    assert updater.ffmpeg_asset() == "ffmpeg-master-latest-win64-gpl.zip"
    monkeypatch.setattr(updater.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(updater.platform, "machine", lambda: "arm64")
    assert updater.ytdlp_asset_name() == "yt-dlp_macos"
    assert updater.release_asset_name() == "ytdl-ui-macos-arm64"
    assert updater.ffmpeg_asset() is None


def test_stream_zip(tmp_path):
    files = []
    for i, size in enumerate((0, 10, 3_000_000)):
        p = tmp_path / f"f{i}.bin"
        p.write_bytes(bytes(range(256)) * (size // 256) + b"x" * (size % 256))
        files.append((str(p), f"{i:03d} - ünïcode {i}.bin"))
    data = b"".join(stream_zip(files, chunk=65536))
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None
    assert [i.file_size for i in z.infolist()] == [0, 10, 3_000_000]
    assert z.namelist()[1] == "001 - ünïcode 1.bin"


def test_token_store_family_and_expiry():
    store = TokenStore(ttl=60)
    t4 = store.add("https://r1.googlevideo.com/videoplayback?ip=1.2.3.4&x=1", {}, 10)
    t6 = store.add("https://r1.googlevideo.com/videoplayback?ip=2001:db8::1", {}, 10)
    tn = store.add("https://cdn.example.com/a.mp4", {}, None)
    assert store.get(t4).family == socket.AF_INET
    assert store.get(t6).family == socket.AF_INET6
    assert store.get(tn).family == socket.AF_UNSPEC
    store.get(t4).expires = 0
    assert store.get(t4) is None
    assert store.get("nope") is None


def test_direct_hints_and_ranges():
    assert not _direct_ok("https://rr1.googlevideo.com/videoplayback?a=1", {})
    assert not _direct_ok("https://cdn.example.com/v.mp4?ip=1.2.3.4", {})
    assert not _direct_ok("https://cdn.example.com/v.mp4", {"Referer": "https://site"})
    assert _direct_ok("https://cdn.example.com/v.mp4", {"User-Agent": "x", "Accept": "*/*"})
    assert _valid_range("bytes=0-1023") and _valid_range("bytes=100-")
    assert not _valid_range("bytes=0-1,5-9") and not _valid_range("items=1-2")
