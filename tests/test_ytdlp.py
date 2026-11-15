import pytest

from ytdlui.config import load_settings
from ytdlui.ytdlp import (Request, audio_selector, classify_error, parse_ytdlp_version, processing_args,
                          safe_filename, selection_args, summarize_info, url_has_single_video, video_selector)


def test_request_parsing():
    r = Request.from_json({"url": "u", "format": "MP4", "quality": "720p"}, 100)
    assert (r.kind, r.format, r.height, r.embed) == ("video", "mp4", 720, False)
    r = Request.from_json({"url": "u", "format": "aac", "quality": "320k"}, 100)
    assert (r.kind, r.format, r.bitrate, r.embed) == ("audio", "m4a", 320, True)   # legacy alias
    r = Request.from_json({"url": "u", "format": "mp3", "quality": "999k"}, 100)
    assert r.bitrate == 192                                                          # unknown → default
    r = Request.from_json({"url": "u", "format": "flac"}, 100)
    assert r.quality_label == "lossless"
    r = Request.from_json({"url": "u", "format": "mkv", "quality": "best", "playlist": True,
                           "items": [3, "1", 3, "x", -2]}, 100)
    assert r.height is None and r.items == [1, 3] and r.playlist
    with pytest.raises(ValueError):
        Request.from_json({"url": "u", "format": "exe"}, 100)
    with pytest.raises(ValueError, match="at most"):
        Request.from_json({"url": "u", "format": "mp3", "items": list(range(1, 12))}, 10)


def test_selectors():
    sel, sort = video_selector("mp4", 1080)
    assert "ext=mp4" in sel and sort.startswith("res:1080") and "h264" in sort
    sel, _ = video_selector("webm", None, http_only=True)
    assert "[protocol^=http][protocol!*=dash]" in sel
    assert audio_selector("m4a").startswith("ba[ext=m4a]")
    assert audio_selector("opus").startswith("ba[acodec=opus]")
    args = selection_args(Request(url="u", kind="audio", format="mp3"))
    assert args[0] == "-f" and "-S" not in args


def test_processing_args():
    a = processing_args(Request(url="u", kind="audio", format="mp3", bitrate=256, embed=True))
    assert a[:3] == ["-x", "--audio-format", "mp3"] and "256K" in a and "--embed-thumbnail" in a
    a = processing_args(Request(url="u", kind="audio", format="wav", embed=True))
    assert "--audio-quality" not in a and "--embed-thumbnail" not in a
    a = processing_args(Request(url="u", kind="video", format="webm", embed=True))
    assert "--embed-thumbnail" not in a and a[:2] == ["--merge-output-format", "webm"]


@pytest.mark.parametrize("stderr, expect", [
    ("ERROR: [youtube] x: Sign in to confirm you're not a bot.", "isn't a bot"),
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "private"),
    ("ERROR: [youtube] x: Sign in to confirm your age.", "age-restricted"),
    ("ERROR: Unsupported URL: https://example.com", "isn't supported"),
    ("ERROR: unable to write data: [Errno 28] No space left on device", "disk space"),
    ("garbage", "Couldn't process"),
])
def test_classify_error(stderr, expect):
    assert expect in classify_error(stderr)


def test_summarize_video_and_playlist():
    s = load_settings(None, {"max_duration_minutes": "2"}, environ={})
    meta = {"id": "a", "title": "T", "duration": 125, "formats": [
        {"height": 1080, "vcodec": "avc1", "acodec": "none", "filesize": 100},
        {"height": 1080, "vcodec": "vp9", "acodec": "none", "filesize": 300},
        {"height": 360, "vcodec": "avc1", "acodec": "mp4a", "filesize_approx": 50},
        {"vcodec": "none", "acodec": "opus", "abr": 160, "filesize": 10},
    ]}
    v = summarize_info(meta, "https://x/watch?v=a", s)
    assert v["type"] == "video" and v["duration_str"] == "2:05" and v["too_long"]
    assert [q["height"] for q in v["video_qualities"]] == [1080, 360]
    assert v["video_qualities"][0]["size"] == 310       # biggest video + best audio
    pl = summarize_info({"_type": "playlist", "title": "P", "entries": [
        {"id": "1", "title": "a", "duration": 60, "url": "https://x/1"},
        {"id": "2", "title": "b", "duration": 9999, "url": "relative-id"},
        None]}, "https://x/watch?v=1&list=P", s)
    assert pl["count"] == 2 and pl["single_available"]
    assert pl["entries"][1]["too_long"] and pl["entries"][1]["url"] == ""


def test_audio_only_source():
    s = load_settings(None, {}, environ={})
    v = summarize_info({"id": "a", "title": "T", "formats": [{"vcodec": "none", "acodec": "mp3", "abr": 128}]}, "u", s)
    assert v["has_video"] is False and v["has_audio"] is True


def test_misc():
    assert url_has_single_video("https://www.youtube.com/watch?v=a&list=b")
    assert not url_has_single_video("https://www.youtube.com/playlist?list=b")
    assert safe_filename('a/b:c*?"<>|d', "mp3") == "abcd.mp3"
    assert safe_filename("", "mp4") == "download.mp4"
    assert len(safe_filename("é" * 300).encode()) <= 180
    assert parse_ytdlp_version("2026.10.26") == (2026, 10, 26)
    assert parse_ytdlp_version("2026.11.14.232655") == (2026, 11, 14, 232655)
    assert parse_ytdlp_version("nope") is None
