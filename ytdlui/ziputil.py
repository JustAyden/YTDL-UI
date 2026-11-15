"""Stream a ZIP archive without building it on disk first (no doubled disk usage)."""

from __future__ import annotations

import os
import zipfile
from typing import Iterable, Iterator


class _Sink:
    """Write-only, non-seekable file object that hands written bytes back to us."""

    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def write(self, b: bytes) -> int:
        self._parts.append(bytes(b))
        return len(b)

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        data = b"".join(self._parts)
        self._parts.clear()
        return data


def stream_zip(files: Iterable[tuple[str, str]], chunk: int = 1024 * 1024) -> Iterator[bytes]:
    """Yield a STORED (uncompressed — media doesn't compress) ZIP of ``(path, arcname)``."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:  # type: ignore[arg-type]
        for path, arcname in files:
            info = zipfile.ZipInfo.from_file(path, arcname)
            info.compress_type = zipfile.ZIP_STORED
            with open(path, "rb") as src, zf.open(info, "w", force_zip64=os.path.getsize(path) > 0x7FFFFFFF) as dst:
                while True:
                    buf = src.read(chunk)
                    if not buf:
                        break
                    dst.write(buf)
                    out = sink.take()
                    if out:
                        yield out
            out = sink.take()
            if out:
                yield out
    out = sink.take()
    if out:
        yield out
