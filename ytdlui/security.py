"""URL validation (SSRF protection), rate limiting and small auth helpers."""

from __future__ import annotations

import hmac
import ipaddress
import socket
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

MAX_URL_LENGTH = 2048


class URLRejected(ValueError):
    """The URL must not be fetched. ``str(exc)`` is safe to show to users."""


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None:
            ip = mapped
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def host_matches(host: str, domains: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for d in domains:
        d = d.lower().strip().lstrip(".").rstrip(".")
        if d.startswith("*."):
            d = d[2:]
        if host == d or host.endswith("." + d):
            return True
    return False


def resolve_host(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def check_url(url: str, *, allowed_sites: list[str] | None = None,
              allow_private: bool = False, resolve: bool = True) -> str:
    """Validate a user-supplied URL and return it normalised.

    Rejects non-http(s) schemes, credentials in the URL, hosts outside the optional
    allow-list and — unless ``allow_private`` — any host that resolves to a loopback,
    private, link-local, CGNAT, multicast or reserved address (this catches tricks like
    ``http://2130706433/``, ``http://[::ffff:127.0.0.1]/`` or DNS names that point at
    internal services, which a hostname regex can't).
    """
    url = (url or "").strip()
    if not url:
        raise URLRejected("Paste a link first.")
    if len(url) > MAX_URL_LENGTH:
        raise URLRejected("That link is too long.")
    if any(c.isspace() for c in url) or any(ord(c) < 32 for c in url):
        raise URLRejected("That doesn't look like a valid link.")
    if "://" not in url:
        url = "https://" + url
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        raise URLRejected("That doesn't look like a valid link.") from None
    if parsed.scheme not in ("http", "https"):
        raise URLRejected("Only http:// and https:// links are supported.")
    if parsed.username or parsed.password:
        raise URLRejected("Links with embedded credentials aren't allowed.")
    host = (parsed.hostname or "").rstrip(".")
    if not host:
        raise URLRejected("That link has no website in it.")

    if allowed_sites and not host_matches(host, allowed_sites):
        raise URLRejected("This server only allows downloads from: " + ", ".join(allowed_sites))

    if not allow_private:
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            literal = None
        if literal is not None:
            if not _is_public_ip(literal):
                raise URLRejected("Links to private or local addresses aren't allowed.")
        else:
            if host.lower() in ("localhost",) or host.lower().endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
                raise URLRejected("Links to private or local addresses aren't allowed.")
            if resolve:
                try:
                    addrs = resolve_host(host, port or (443 if parsed.scheme == "https" else 80))
                except (socket.gaierror, UnicodeError, OSError):
                    raise URLRejected(f"Couldn't find the website '{host}'.") from None
                for a in addrs:
                    try:
                        ip = ipaddress.ip_address(a.split("%", 1)[0])
                    except ValueError:
                        raise URLRejected("Links to private or local addresses aren't allowed.") from None
                    if not _is_public_ip(ip):
                        raise URLRejected("Links to private or local addresses aren't allowed.")
    return url


class RateLimiter:
    """Sliding-window limiter keyed by (bucket, client). Memory is pruned as it goes."""

    def __init__(self, window: float = 60.0):
        self.window = window
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_prune = time.monotonic()

    def hit(self, bucket: str, client: str, limit: int) -> bool:
        """Record a request. Returns True if it is allowed, False if over the limit."""
        if limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            if now - self._last_prune > self.window:
                self._prune(now)
            q = self._hits[(bucket, client)]
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def retry_after(self, bucket: str, client: str) -> int:
        with self._lock:
            q = self._hits.get((bucket, client))
            if not q:
                return 1
            return max(1, int(self.window - (time.monotonic() - q[0])) + 1)

    def _prune(self, now: float) -> None:
        for key in [k for k, q in self._hits.items() if not q or now - q[-1] >= self.window]:
            del self._hits[key]
        self._last_prune = now


def safe_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
