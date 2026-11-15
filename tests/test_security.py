import pytest

import ytdlui.security as sec
from ytdlui.security import RateLimiter, URLRejected, check_url, host_matches


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(sec, "resolve_host", lambda host, port: ["142.250.80.46"])


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x", "http://localhost:8080/", "http://10.1.2.3/", "http://192.168.0.1/",
    "http://172.16.5.4/", "http://169.254.169.254/latest/meta-data/", "http://100.64.1.1/",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://0.0.0.0/", "http://2130706433/",
    "http://0x7f000001/", "http://router.lan/", "http://printer.local/", "http://224.0.0.1/",
])
def test_private_targets_rejected(url, monkeypatch):
    # Numeric hosts such as 2130706433 resolve to 127.0.0.1 via getaddrinfo.
    monkeypatch.setattr(sec, "resolve_host", lambda host, port: ["127.0.0.1"])
    with pytest.raises(URLRejected):
        check_url(url)


def test_dns_pointing_inside_is_rejected(monkeypatch):
    monkeypatch.setattr(sec, "resolve_host", lambda host, port: ["93.184.216.34", "10.0.0.5"])
    with pytest.raises(URLRejected, match="private"):
        check_url("https://sneaky.example.com/video")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/a", "javascript:alert(1)",
                                 "https://user:pw@example.com/", "https://exa mple.com/", ""])
def test_bad_urls(url, public_dns):
    with pytest.raises(URLRejected):
        check_url(url)


def test_good_url_and_scheme_added(public_dns):
    assert check_url("https://www.youtube.com/watch?v=abc") == "https://www.youtube.com/watch?v=abc"
    assert check_url("youtu.be/abc") == "https://youtu.be/abc"


def test_private_allowed_when_configured(monkeypatch):
    monkeypatch.setattr(sec, "resolve_host", lambda host, port: ["192.168.1.10"])
    assert check_url("http://nas.example/v.mp4", allow_private=True)


def test_allowlist(public_dns):
    sites = ["youtube.com", "youtu.be"]
    assert check_url("https://m.youtube.com/watch?v=x", allowed_sites=sites)
    with pytest.raises(URLRejected, match="only allows"):
        check_url("https://vimeo.com/1", allowed_sites=sites)
    assert host_matches("music.youtube.com", ["*.youtube.com"])
    assert not host_matches("evilyoutube.com", ["youtube.com"])


def test_rate_limiter():
    rl = RateLimiter(window=60)
    assert all(rl.hit("info", "1.2.3.4", 3) for _ in range(3))
    assert not rl.hit("info", "1.2.3.4", 3)
    assert rl.hit("info", "5.6.7.8", 3)        # other clients unaffected
    assert rl.hit("download", "1.2.3.4", 3)    # other buckets unaffected
    assert rl.retry_after("info", "1.2.3.4") >= 1
    assert rl.hit("x", "y", 0)                 # 0 = unlimited
