"""Server-wide bandwidth cap.

Everything the server streams to users itself — finished files, playlist ZIPs,
relayed browser-mode streams, the self-hosted ffmpeg.wasm core — draws from one
shared token bucket, so the total never exceeds ``bandwidth_limit``. Server-side
yt-dlp downloads run in separate processes; they get ``--limit-rate`` set to the
cap divided by ``max_concurrent_jobs`` (see jobs.py), so they can't exceed it
either, even when every download slot is busy.
"""

from __future__ import annotations

import threading
import time
from typing import Iterable, Iterator

MB = 1000 * 1000   # decimal MB, matching how the UI shows sizes and speeds


class Bandwidth:
    def __init__(self, limit_mb: float):
        self.rate = max(0.0, float(limit_mb)) * MB          # bytes per second; 0 = unlimited
        # Allow short bursts of up to half a second's worth (never less than 256 KB).
        self.capacity = max(self.rate * 0.5, 256 * 1024)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()
        self.bytes_total = 0
        self._window: list[tuple[float, int]] = []          # (timestamp, bytes) for the live rate

    def set_limit(self, limit_mb: float) -> None:
        with self._lock:
            self.rate = max(0.0, float(limit_mb)) * MB
            self.capacity = max(self.rate * 0.5, 256 * 1024)
            self._tokens = min(self._tokens, self.capacity)

    @property
    def enabled(self) -> bool:
        return self.rate > 0

    def consume(self, n: int) -> None:
        """Account for ``n`` bytes, sleeping as long as needed to stay under the cap."""
        if n <= 0:
            return
        with self._lock:
            now = time.monotonic()
            self.bytes_total += n
            self._window.append((now, n))
            if len(self._window) > 4096 or (self._window and now - self._window[0][0] > 5):
                self._window = [(t, b) for t, b in self._window if now - t <= 5]
            if not self.enabled:
                return
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
            self._last = now
            self._tokens -= n                    # may go negative: a debt that callers wait off
            wait = -self._tokens / self.rate if self._tokens < 0 else 0.0
        if wait > 0:
            time.sleep(wait)

    def current_rate(self) -> float:
        """Bytes/second over the last few seconds."""
        with self._lock:
            now = time.monotonic()
            recent = [(t, b) for t, b in self._window if now - t <= 3]
        if not recent:
            return 0.0
        span = max(1.0, now - recent[0][0])
        return sum(b for _, b in recent) / span

    def wrap(self, iterable: Iterable[bytes], chunk: int = 64 * 1024) -> Iterator[bytes]:
        """Yield the same bytes as ``iterable``, paced to the cap. Closes the source when done."""
        try:
            for data in iterable:
                if not data:
                    continue
                for i in range(0, len(data), chunk):
                    piece = data[i:i + chunk]
                    self.consume(len(piece))
                    yield piece
        finally:
            close = getattr(iterable, "close", None)
            if close:
                close()


def ytdlp_rate(limit_mb: float, slots: int) -> int | None:
    """Per-process ``--limit-rate`` (bytes/s) so all concurrent yt-dlp downloads fit the cap."""
    if limit_mb <= 0:
        return None
    return max(64 * 1024, int(limit_mb * MB / max(1, slots)))
