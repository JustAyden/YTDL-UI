"""Browser-side processing support.

``resolve()`` asks yt-dlp which media streams to use (``-J``, nothing is
downloaded) and returns a plan the browser executes itself with ffmpeg.wasm.

Many sources don't let a browser fetch their media directly: YouTube stream URLs
are signed for the IP address that looked them up (the server) and send no CORS
headers. For those, ``RelaySession`` streams the bytes through the server —
straight from the socket to the client, never to disk, never through ffmpeg.
Relay tokens only map to URLs yt-dlp produced for this server, so the relay is
not an open proxy.
"""

from __future__ import annotations

import http.client
import ipaddress
import logging
import secrets
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import parse_qs, urljoin, urlparse

from .config import Settings
from .security import URLRejected, check_url
from .ytdlp import (AUDIO_FORMATS, Request, YtDlp, YtDlpError, media_metadata, safe_filename,
                    selection_args)

log = logging.getLogger("ytdlui")

TOKEN_TTL = 6 * 3600
CHUNK = 256 * 1024
# Hosts known to bind media URLs to the requesting IP and/or refuse CORS.
NO_DIRECT_HOSTS = ("googlevideo.com", "youtube.com", "ytimg.com")
SAFE_HEADERS = {"user-agent", "accept", "accept-language", "sec-fetch-mode", "accept-encoding"}
PASS_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag")


class FallbackNeeded(Exception):
    """Browser processing can't handle this request; server processing could."""


@dataclass
class Target:
    url: str
    headers: dict[str, str]
    family: int            # socket.AF_UNSPEC / AF_INET / AF_INET6
    expires: float
    size: int | None
    bytes_sent: int = 0


class TokenStore:
    def __init__(self, ttl: int = TOKEN_TTL, max_items: int = 20000):
        self.ttl = ttl
        self.max_items = max_items
        self._items: dict[str, Target] = {}
        self._lock = threading.Lock()

    def add(self, url: str, headers: dict[str, str], size: int | None) -> str:
        family = socket.AF_UNSPEC
        ip = (parse_qs(urlparse(url).query).get("ip") or [""])[0]
        try:
            family = socket.AF_INET6 if ipaddress.ip_address(ip).version == 6 else socket.AF_INET
        except ValueError:
            pass
        token = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock:
            if len(self._items) >= self.max_items:
                self._prune(now, force=True)
            self._items[token] = Target(url, headers, family, now + self.ttl, size)
        return token

    def get(self, token: str) -> Target | None:
        with self._lock:
            t = self._items.get(token)
            if t and t.expires < time.time():
                del self._items[token]
                return None
            return t

    def _prune(self, now: float, force: bool = False) -> None:
        for k in [k for k, v in self._items.items() if v.expires < now]:
            del self._items[k]
        if force and len(self._items) >= self.max_items:
            for k in sorted(self._items, key=lambda k: self._items[k].expires)[: self.max_items // 10]:
                del self._items[k]

    def __len__(self) -> int:
        return len(self._items)


def _direct_ok(url: str, headers: dict[str, str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in NO_DIRECT_HOSTS):
        return False
    if "ip" in parse_qs(urlparse(url).query):
        return False
    return all(k.lower() in SAFE_HEADERS for k in headers)


def _stream_entry(store: TokenStore, f: dict[str, Any], role: str, relay_on: bool) -> dict[str, Any]:
    url = f.get("url") or ""
    headers = {str(k): str(v) for k, v in (f.get("http_headers") or {}).items()}
    size = f.get("filesize") or f.get("filesize_approx")
    size = int(size) if size else None
    direct = url if _direct_ok(url, headers) else None
    if not direct and not relay_on:
        raise FallbackNeeded("This site doesn't allow direct browser downloads and the relay is off.")
    token = store.add(url, headers, size) if relay_on else None
    chunk = ((f.get("downloader_options") or {}).get("http_chunk_size")) or 8 * 1024 * 1024
    return {
        "role": role,
        "token": token,
        "direct": direct,
        "ext": f.get("ext") or "",
        "vcodec": f.get("vcodec") or "none",
        "acodec": f.get("acodec") or "none",
        "size": size,
        "exact_size": bool(f.get("filesize")),
        "chunk": int(chunk),
        "format_id": f.get("format_id"),
    }


def resolve(ytdlp: YtDlp, store: TokenStore, settings: Settings, req: Request) -> dict[str, Any]:
    try:
        meta = ytdlp.run_json(["--no-playlist"] + selection_args(req, http_only=True), req.url, timeout=90)
    except YtDlpError as exc:
        msg = str(exc)
        if "format isn't available" in msg:
            raise FallbackNeeded("This site only offers streaming formats that need server processing.") from None
        raise
    if meta.get("_type") in ("playlist", "multi_video"):
        raise YtDlpError("Pick a single item from the playlist.")
    if meta.get("is_live"):
        raise FallbackNeeded("Live streams can't be processed in the browser.")
    max_dur = settings.max_duration_minutes * 60
    if max_dur and meta.get("duration") and meta["duration"] > max_dur:
        raise YtDlpError("This video is longer than this server allows.")

    formats = meta.get("requested_formats") or [meta]
    relay_on = settings.relay
    streams = []
    for f in formats:
        if not f.get("url") or not str(f.get("protocol", "https")).startswith("http"):
            raise FallbackNeeded("This site uses a streaming format the browser can't download.")
        has_v = f.get("vcodec") not in (None, "none")
        has_a = f.get("acodec") not in (None, "none")
        role = "av" if has_v and has_a else ("video" if has_v else "audio")
        if f.get("vcodec") is None and f.get("acodec") is None:
            role = "av" if req.kind == "video" else "audio"
        for check in (f["url"],):
            try:
                check_url(check, allow_private=settings.allow_private_urls)
            except URLRejected:
                raise YtDlpError("The site returned a media address this server won't fetch.") from None
        streams.append(_stream_entry(store, f, role, relay_on))

    total = sum(s["size"] or 0 for s in streams)
    limit = settings.max_filesize_mb * 1024 * 1024
    if limit and total > limit:
        raise YtDlpError("This file is larger than this server allows.")

    cover = None
    thumb = meta.get("thumbnail") or ""
    if req.embed and req.kind == "audio" and req.format in ("mp3", "m4a", "flac") and thumb.startswith("https://"):
        try:
            check_url(thumb, allow_private=settings.allow_private_urls)
            cover = {"direct": thumb, "token": store.add(thumb, {}, None) if relay_on else None}
        except URLRejected:
            cover = None

    ext = req.format
    return {
        "title": meta.get("title") or "download",
        "filename": safe_filename(meta.get("title") or "download", ext),
        "kind": req.kind,
        "format": req.format,
        "quality": req.quality_label,
        "bitrate": req.bitrate,
        "lossy": AUDIO_FORMATS.get(req.format, {}).get("lossy", True),
        "duration": meta.get("duration"),
        "streams": streams,
        "total_size": total or None,
        "meta": media_metadata(meta) if req.embed else {},
        "cover": cover,
    }


# ── relay ────────────────────────────────────────────────────────────────────
class _Conn(http.client.HTTPSConnection):
    """HTTPS connection pinned to one address family (IPv4 vs IPv6 matters for IP-locked URLs)."""

    def __init__(self, host: str, port: int | None, family: int, **kw: Any):
        super().__init__(host, port, **kw)
        self._family = family

    def connect(self) -> None:  # noqa: D401
        if self._family == socket.AF_UNSPEC or self._tunnel_host:
            return super().connect()
        infos = socket.getaddrinfo(self.host, self.port, self._family, socket.SOCK_STREAM)
        err: OSError | None = None
        for fam, typ, proto, _cn, addr in infos:
            sock = socket.socket(fam, typ, proto)
            sock.settimeout(self.timeout)
            try:
                sock.connect(addr)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as e:
                err = e
                sock.close()
        raise err or OSError("connect failed")


class _PlainConn(http.client.HTTPConnection):
    def __init__(self, host: str, port: int | None, family: int, **kw: Any):
        super().__init__(host, port, **kw)


class RelayError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Relay:
    def __init__(self, settings: Settings, store: TokenStore):
        self.s = settings
        self.store = store
        self.bytes_total = 0
        self.active = 0
        self._lock = threading.Lock()
        self._ctx = ssl.create_default_context()

    def open(self, token: str, range_header: str | None) -> tuple[int, dict[str, str], Iterator[bytes]]:
        if not self.s.relay:
            raise RelayError(403, "Relay is disabled on this server.")
        target = self.store.get(token)
        if not target:
            raise RelayError(404, "This link expired. Start the download again.")

        url = target.url
        headers = {k: v for k, v in target.headers.items() if k.lower() != "accept-encoding"}
        headers["Accept-Encoding"] = "identity"
        if range_header and _valid_range(range_header):
            headers["Range"] = range_header

        resp = conn = None
        for _hop in range(6):
            try:
                check_url(url, allow_private=self.s.allow_private_urls)
            except URLRejected:
                raise RelayError(403, "Refusing to fetch that address.") from None
            conn, path = self._connect(url, target.family)
            try:
                conn.request("GET", path, headers=headers)
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                conn.close()
                raise RelayError(502, f"Couldn't reach the source: {exc.__class__.__name__}") from None
            if resp.status in (301, 302, 303, 307, 308) and resp.getheader("Location"):
                url = urljoin(url, resp.getheader("Location"))
                resp.read()
                conn.close()
                continue
            break
        else:
            raise RelayError(502, "Too many redirects.")

        assert resp is not None and conn is not None
        if resp.status >= 400:
            body = resp.read(512)
            conn.close()
            log.info("Relay upstream %s for token %s…: %r", resp.status, token[:6], body[:120])
            status = 410 if resp.status in (403, 410) else 502
            raise RelayError(status, f"The source refused the request (HTTP {resp.status}).")

        out_headers = {h: resp.getheader(h) for h in PASS_HEADERS if resp.getheader(h)}
        out_headers["Cache-Control"] = "no-store"

        def body() -> Iterator[bytes]:
            with self._lock:
                self.active += 1
            try:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    with self._lock:
                        self.bytes_total += len(chunk)
                    target.bytes_sent += len(chunk)
                    yield chunk
            finally:
                with self._lock:
                    self.active -= 1
                conn.close()

        return resp.status, out_headers, body()

    def _connect(self, url: str, family: int) -> tuple[http.client.HTTPConnection, str]:
        p = urlparse(url)
        path = p.path or "/"
        if p.query:
            path += "?" + p.query
        timeout = 30
        if self.s.proxy:
            pp = urlparse(self.s.proxy if "://" in self.s.proxy else "http://" + self.s.proxy)
            if pp.scheme not in ("http", "https"):
                raise RelayError(501, "The relay only supports HTTP(S) proxies.")
            conn: http.client.HTTPConnection
            if p.scheme == "https":
                conn = http.client.HTTPSConnection(pp.hostname, pp.port or 3128, timeout=timeout, context=self._ctx)
                hdrs = {}
                if pp.username:
                    import base64
                    cred = f"{pp.username}:{pp.password or ''}".encode()
                    hdrs["Proxy-Authorization"] = "Basic " + base64.b64encode(cred).decode()
                conn.set_tunnel(p.hostname, p.port or 443, headers=hdrs)
                return conn, path
            conn = http.client.HTTPConnection(pp.hostname, pp.port or 3128, timeout=timeout)
            return conn, url
        if p.scheme == "https":
            return _Conn(p.hostname, p.port, family, timeout=timeout, context=self._ctx), path
        return _PlainConn(p.hostname, p.port, family, timeout=timeout), path


def _valid_range(value: str) -> bool:
    import re
    return bool(re.fullmatch(r"bytes=\d{1,15}-\d{0,15}", value.strip()))
