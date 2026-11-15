"""Server-side download jobs: a bounded queue, real progress, cancel and cleanup.

Downloads run in the background instead of inside one long HTTP request, so they
survive proxies with short timeouts (Cloudflare cuts requests at 100 s), report
real progress, and the finished file is handed to the browser's own download
manager (no buffering whole videos in page memory).
"""

from __future__ import annotations

import logging
import os
import queue
import secrets
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .config import Settings
from .ytdlp import (IS_WINDOWS, Request, YtDlp, classify_error, error_lines, parse_ytdlp_version,
                    processing_args, selection_args)

log = logging.getLogger("ytdlui")

ACTIVE = ("queued", "running")
_CREATE_NO_WINDOW = 0x08000000

PP_LABELS = {
    "Merger": "Merging video and audio",
    "ExtractAudio": "Converting audio",
    "EmbedThumbnail": "Embedding cover art",
    "FFmpegMetadata": "Writing metadata",
    "Metadata": "Writing metadata",
    "ThumbnailsConvertor": "Preparing cover art",
    "FFmpegThumbnailsConvertor": "Preparing cover art",
    "VideoRemuxer": "Remuxing",
    "VideoConvertor": "Converting video",
    "FixupM3u8": "Fixing container",
    "FixupM4a": "Fixing container",
    "FixupStretched": "Fixing container",
    "FixupDuplicateMoov": "Fixing container",
    "MoveFiles": "Finishing up",
}


class JobRejected(Exception):
    def __init__(self, message: str, status: int = 429):
        super().__init__(message)
        self.status = status


def _na(v: str) -> float | None:
    v = (v or "").strip()
    if not v or v == "NA" or v == "None":
        return None
    try:
        return float(v)
    except ValueError:
        return None


@dataclass
class Job:
    req: Request
    owner: str
    title: str = ""
    id: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    status: str = "queued"          # queued | running | done | error | cancelled
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    progress: float | None = 0.0
    speed: float | None = None
    eta: float | None = None
    stage: str = "Waiting in queue"
    item_index: int = 0             # 1-based position within this job (playlists)
    item_count: int = 1
    item_title: str = ""
    files: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    warnings: int = 0
    removed: bool = False           # user deleted it; drop as soon as it stops
    work_dir: str = ""
    log: deque = field(default_factory=lambda: deque(maxlen=80))
    cancel_event: threading.Event = field(default_factory=threading.Event)
    proc: subprocess.Popen | None = None

    # progress bookkeeping
    _item_total: float | None = None
    _streams: int = 1
    _stream_done_bytes: float = 0.0
    _stream_idx: int = 0
    _items_done: int = 0

    def public(self, ttl: int, queue_pos: int | None = None) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "title": self.title or self.item_title or "",
            "kind": self.req.kind,
            "format": self.req.format,
            "quality": self.req.quality_label,
            "playlist": self.req.playlist,
            "progress": None if self.progress is None else round(self.progress, 4),
            "speed": self.speed,
            "eta": self.eta,
            "stage": self.stage,
            "item_index": self.item_index,
            "item_count": self.item_count,
            "item_title": self.item_title,
            "files": [{"index": i, "name": f["name"], "size": f["size"]} for i, f in enumerate(self.files)],
            "error": self.error,
            "skipped": self.warnings,
            "created": self.created,
            "finished": self.finished,
            "expires": (self.finished + ttl) if self.finished and self.status == "done" else None,
            "queue_position": queue_pos,
        }


class JobManager:
    def __init__(self, settings: Settings, ytdlp: YtDlp):
        self.s = settings
        self.ytdlp = ytdlp
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[str] = queue.Queue()
        self._order: list[str] = []          # queued ids, for queue positions
        self._stop = threading.Event()
        self.completed_total = 0
        self.failed_total = 0
        self._wipe_tmp()
        self._workers = [threading.Thread(target=self._worker, name=f"job-worker-{i}", daemon=True)
                         for i in range(max(1, settings.max_concurrent_jobs))]
        for t in self._workers:
            t.start()
        threading.Thread(target=self._janitor, name="job-janitor", daemon=True).start()

    # ── public API ──────────────────────────────────────────────────────
    def submit(self, req: Request, owner: str, title: str = "") -> Job:
        with self._lock:
            active = [j for j in self._jobs.values() if j.status in ACTIVE]
            mine = [j for j in active if j.owner == owner]
            if self.s.max_jobs_per_client > 0 and len(mine) >= self.s.max_jobs_per_client:
                raise JobRejected(f"You already have {len(mine)} downloads in progress. "
                                  "Wait for one to finish.")
            queued = [j for j in active if j.status == "queued"]
            if self.s.max_queue > 0 and len(queued) >= self.s.max_queue:
                raise JobRejected("The server is busy right now. Try again in a few minutes.", 503)
            job = Job(req=req, owner=owner, title=title)
            if req.playlist and req.items:
                job.item_count = len(req.items)
            self._jobs[job.id] = job
            self._order.append(job.id)
        self._queue.put(job.id)
        log.info("Job %s queued: %s %s from %s", job.id[:8], req.format, req.quality_label,
                 req.url if self.s.log_urls else (urlparse(req.url).hostname or "?"))
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None or job.removed else job

    def queue_position(self, job: Job) -> int | None:
        with self._lock:
            if job.status != "queued":
                return None
            try:
                return self._order.index(job.id) + 1
            except ValueError:
                return None

    def public(self, job: Job) -> dict[str, Any]:
        return job.public(self.s.job_ttl_minutes * 60, self.queue_position(job))

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job:
            return False
        if job.status in ACTIVE:
            job.cancel_event.set()
            if job.status == "queued":
                self._finish(job, "cancelled", stage="Cancelled")
            elif job.proc:
                _kill_tree(job.proc)
        return True

    def remove(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job:
            return False
        self.cancel(job_id)
        job.removed = True
        if job.status not in ACTIVE:
            self._delete(job)
        return True

    def all_jobs(self) -> list[Job]:
        with self._lock:
            return sorted((j for j in self._jobs.values() if not j.removed), key=lambda j: j.created, reverse=True)

    def counts(self) -> dict[str, int]:
        with self._lock:
            jobs = list(self._jobs.values())
        return {
            "running": sum(j.status == "running" for j in jobs),
            "queued": sum(j.status == "queued" for j in jobs),
            "finished": sum(j.status in ("done", "error", "cancelled") for j in jobs),
            "completed_total": self.completed_total,
            "failed_total": self.failed_total,
        }

    def busy(self) -> bool:
        c = self.counts()
        return c["running"] >= self.s.max_concurrent_jobs or c["queued"] > 0

    def idle(self) -> bool:
        c = self.counts()
        return c["running"] == 0 and c["queued"] == 0

    def purge_finished(self) -> int:
        n = 0
        for job in self.all_jobs():
            if job.status not in ACTIVE:
                self._delete(job)
                n += 1
        return n

    def tmp_usage(self) -> int:
        total = 0
        for root, _dirs, files in os.walk(self.s.tmp_path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total

    def shutdown(self) -> None:
        self._stop.set()
        for job in self.all_jobs():
            if job.status == "running" and job.proc:
                _kill_tree(job.proc)

    # ── internals ───────────────────────────────────────────────────────
    def _wipe_tmp(self) -> None:
        os.makedirs(self.s.tmp_path, exist_ok=True)
        for name in os.listdir(self.s.tmp_path):
            p = os.path.join(self.s.tmp_path, name)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _delete(self, job: Job) -> None:
        with self._lock:
            self._jobs.pop(job.id, None)
            if job.id in self._order:
                self._order.remove(job.id)
        if job.work_dir:
            shutil.rmtree(job.work_dir, ignore_errors=True)

    def _finish(self, job: Job, status: str, *, stage: str = "", error: str | None = None) -> None:
        with self._lock:
            job.status = status
            job.finished = time.time()
            job.speed = job.eta = None
            if stage:
                job.stage = stage
            job.error = error
            if job.id in self._order:
                self._order.remove(job.id)
        if status == "done":
            self.completed_total += 1
        elif status == "error":
            self.failed_total += 1
        if status != "done" and job.work_dir:
            shutil.rmtree(job.work_dir, ignore_errors=True)
            job.files = []
        if job.removed:
            self._delete(job)

    def _janitor(self) -> None:
        while not self._stop.wait(30):
            ttl = self.s.job_ttl_minutes * 60
            now = time.time()
            for job in self.all_jobs():
                if job.status not in ACTIVE and job.finished and now - job.finished > ttl:
                    self._delete(job)

    def _free_space_ok(self) -> bool:
        need = self.s.min_free_space_mb * 1024 * 1024
        if need <= 0:
            return True
        try:
            if shutil.disk_usage(self.s.tmp_path).free >= need:
                return True
        except OSError:
            return True
        # Make room by dropping the oldest finished downloads first.
        for job in sorted(self.all_jobs(), key=lambda j: j.finished or 0):
            if job.status not in ACTIVE:
                self._delete(job)
                if shutil.disk_usage(self.s.tmp_path).free >= need:
                    return True
        return False

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            job = self.get(job_id)
            if not job or job.status != "queued" or job.cancel_event.is_set():
                continue
            try:
                self._run(job)
            except Exception as exc:  # never let a worker thread die
                log.exception("Job %s crashed", job.id[:8])
                self._finish(job, "error", stage="Failed", error=f"Internal error: {exc}")

    def build_command(self, job: Job) -> list[str]:
        req = job.req
        if req.playlist:
            tmpl = "%(playlist_index)03d - %(title).170B.%(ext)s"
        else:
            tmpl = "%(title).180B.%(ext)s"
        args = self.ytdlp.base_args() + [
            "--newline", "--progress", "--no-simulate", "--no-mtime", "--windows-filenames",
            "-o", os.path.join(job.work_dir, tmpl),
            "--print", "before_dl:@@I %(playlist_index)s|%(n_entries,playlist_count)s|"
                       "%(filesize,filesize_approx)s|%(format_id)s|%(title)s",
            "--print", "after_move:@@F %(filepath)s",
            "--progress-template",
            "download:@@P %(progress.status)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
            "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s|"
            "%(progress.fragment_index)s|%(progress.fragment_count)s",
            "--progress-template", "postprocess:@@PP %(progress.status)s|%(progress.postprocessor)s",
        ]
        v = parse_ytdlp_version(self.ytdlp.version() or "")
        if v and v >= (2024, 8, 1):
            args += ["--progress-delta", "0.5"]
        if self.s.max_filesize_mb > 0:
            args += ["--max-filesize", f"{self.s.max_filesize_mb}M"]
        if req.playlist:
            args += ["--yes-playlist", "--ignore-errors"]
            if req.items:
                args += ["-I", ",".join(str(i) for i in req.items)]
            else:
                args += ["-I", f"1:{self.s.max_playlist_items}"]
        else:
            args += ["--no-playlist"]
        args += selection_args(req) + processing_args(req)
        args += ["--", req.url]
        return args

    def _run(self, job: Job) -> None:
        if not self._free_space_ok():
            self._finish(job, "error", stage="Failed",
                         error="The server is low on disk space. Try again later.")
            return
        job.work_dir = os.path.join(self.s.tmp_path, job.id)
        os.makedirs(job.work_dir, exist_ok=True)
        with self._lock:
            job.status = "running"
            job.started = time.time()
            job.stage = "Starting"
            if job.id in self._order:
                self._order.remove(job.id)

        try:
            cmd = self.build_command(job)
        except FileNotFoundError:
            self._finish(job, "error", stage="Failed", error="yt-dlp isn't installed on the server.")
            return

        popen_kw: dict[str, Any] = {}
        if IS_WINDOWS:
            popen_kw["creationflags"] = _CREATE_NO_WINDOW
        else:
            popen_kw["start_new_session"] = True
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                    errors="replace", bufsize=1, env=self.ytdlp.env(), **popen_kw)
        except OSError as exc:
            self._finish(job, "error", stage="Failed", error=f"Couldn't start yt-dlp: {exc}")
            return
        job.proc = proc

        timeout = self.s.job_timeout_minutes * 60
        timer = threading.Timer(timeout, lambda: (job.log.append("[timeout]"), _kill_tree(proc))) if timeout > 0 else None
        if timer:
            timer.daemon = True
            timer.start()

        produced: list[str] = []
        output_tail: deque[str] = deque(maxlen=200)
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                if line.startswith("@@"):
                    self._handle_marker(job, line, produced)
                else:
                    output_tail.append(line)
                    job.log.append(line[:500])
                    if line.startswith("ERROR:") and job.req.playlist:
                        job.warnings += 1
            proc.wait()
        finally:
            if timer:
                timer.cancel()
            job.proc = None

        if job.cancel_event.is_set():
            self._finish(job, "cancelled", stage="Cancelled")
            log.info("Job %s cancelled", job.id[:8])
            return

        files = self._collect_files(job, produced)
        out = "\n".join(output_tail)
        if not files:
            if "[timeout]" in job.log:
                msg = "The download took too long and was stopped."
            else:
                msg = classify_error(out)
            log.info("Job %s failed: %s", job.id[:8], " | ".join(error_lines(out))[:400] or out[-300:])
            self._finish(job, "error", stage="Failed", error=msg)
            return

        job.files = files
        job.progress = 1.0
        if job.req.playlist:
            job.warnings = max(job.warnings, job.item_count - len(files)) if job.item_count else job.warnings
        stage = "Ready"
        if job.req.playlist and job.warnings:
            stage = f"Ready — {job.warnings} item(s) skipped"
        self._finish(job, "done", stage=stage)
        log.info("Job %s done: %d file(s), %.1f MB", job.id[:8], len(files),
                 sum(f["size"] for f in files) / 1e6)

    def _handle_marker(self, job: Job, line: str, produced: list[str]) -> None:
        tag, _, rest = line.partition(" ")
        if tag == "@@I":
            parts = rest.split("|", 4)
            if len(parts) < 5:
                return
            idx, count, size, fmt_id, title = parts
            if job.req.playlist:
                if job.item_index:
                    job._items_done += 1
                job.item_index = job._items_done + 1
                if not job.req.items:
                    n = _na(count)
                    if n:
                        job.item_count = int(min(n, self.s.max_playlist_items))
            job.item_title = title
            if not job.title and not job.req.playlist:
                job.title = title
            job._item_total = _na(size)
            job._streams = max(1, fmt_id.count("+") + 1)
            job._stream_idx = 0
            job._stream_done_bytes = 0.0
            job.stage = self._dl_stage(job)
            self._update_progress(job, 0.0)
        elif tag == "@@P":
            p = rest.split("|")
            if len(p) < 8:
                return
            status = p[0]
            done = _na(p[1]) or 0.0
            total = _na(p[2]) or _na(p[3])
            frag_i, frag_n = _na(p[6]), _na(p[7])
            job.speed = _na(p[4])
            job.eta = _na(p[5])
            if status == "finished":
                job._stream_done_bytes += total or done
                job._stream_idx += 1
                frac_stream = 1.0
            elif total:
                frac_stream = min(1.0, done / total)
            elif frag_i and frag_n:
                frac_stream = min(1.0, frag_i / frag_n)
            else:
                frac_stream = 0.0
            job.stage = self._dl_stage(job)
            if job._item_total and status != "finished":
                item_frac = (job._stream_done_bytes + done) / job._item_total
            elif job._item_total:
                item_frac = job._stream_done_bytes / job._item_total
            else:
                item_frac = (min(job._stream_idx, job._streams - 1) + frac_stream) / job._streams \
                    if status != "finished" else job._stream_idx / job._streams
            self._update_progress(job, min(item_frac, 0.98))
        elif tag == "@@PP":
            status, _, pp = rest.partition("|")
            if status == "started":
                job.stage = PP_LABELS.get(pp.strip(), "Processing")
                job.speed = job.eta = None
                self._update_progress(job, 0.98)
        elif tag == "@@F":
            path = rest.strip()
            if path:
                produced.append(path)

    def _dl_stage(self, job: Job) -> str:
        prefix = f"Item {job.item_index} of {job.item_count} · " if job.req.playlist else ""
        if job._streams > 1:
            which = "video" if job._stream_idx == 0 else "audio"
            return f"{prefix}Downloading {which} ({min(job._stream_idx + 1, job._streams)}/{job._streams})"
        return f"{prefix}Downloading"

    def _update_progress(self, job: Job, item_frac: float) -> None:
        item_frac = max(0.0, min(1.0, item_frac))
        if job.req.playlist and job.item_count:
            job.progress = min(0.99, (job._items_done + item_frac) / job.item_count)
        else:
            job.progress = item_frac

    def _collect_files(self, job: Job, produced: list[str]) -> list[dict[str, Any]]:
        root = os.path.realpath(job.work_dir)
        seen: list[str] = []
        for p in produced:
            rp = os.path.realpath(p)
            if os.path.commonpath([root, rp]) == root and os.path.isfile(rp) and rp not in seen:
                seen.append(rp)
        if not seen:  # older yt-dlp builds: fall back to scanning the folder
            for name in sorted(os.listdir(job.work_dir)):
                rp = os.path.join(root, name)
                if os.path.isfile(rp) and not name.endswith((".part", ".ytdl", ".temp", ".tmp")):
                    seen.append(rp)
        seen.sort(key=lambda p: os.path.basename(p))
        return [{"name": os.path.basename(p), "path": p, "size": os.path.getsize(p)} for p in seen]


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=_CREATE_NO_WINDOW, timeout=15)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass
