"""Download engine: job queue, yt-dlp execution, and post-download sidecars."""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import (
    DownloadCancelled,
    DownloadError,
    ExistingVideoReached,
    MaxDownloadsReached,
    RejectedVideoReached,
)

from . import romanise as romanise_mod
from . import transcripts
from .argv import build_argv
from .config import Config
from .sidecar import write_summary


class State(StrEnum):
    QUEUED = "queued"
    PROBING = "probing"
    RUNNING = "running"
    POST = "post"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = {State.DONE, State.FAILED, State.CANCELLED}

# yt-dlp signals *planned* early exits by raising subclasses of
# DownloadCancelled. They mean "finished on purpose", not "the user aborted",
# so they must not be reported as a cancellation.
PLANNED_STOP = (ExistingVideoReached, MaxDownloadsReached, RejectedVideoReached)
STOP_REASONS = {
    ExistingVideoReached: "stopped at an already-archived video",
    MaxDownloadsReached: "reached the download limit",
    RejectedVideoReached: "stopped at a filtered-out video",
}

_ids = itertools.count(1)


@dataclass
class Job:
    url: str
    config: Config
    id: int = field(default_factory=lambda: next(_ids))
    title: str = ""
    state: State = State.QUEUED
    percent: float = 0.0
    speed: str = ""
    eta: str = ""
    size: str = ""
    detail: str = ""
    error: str = ""
    files: list[Path] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    items_done: int = 0
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _processed: set[str] = field(default_factory=set, repr=False)

    @property
    def label(self) -> str:
        return self.title or self.url

    @property
    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        return (self.finished_at or time.monotonic()) - self.started_at

    def cancel(self) -> None:
        self._cancel.set()


@dataclass
class Event:
    kind: str  # "state" | "progress" | "log" | "file" | "meta"
    job: Job
    message: str = ""
    level: str = "info"  # info | warning | error


Sink = Callable[[Event], None]


def _fmt_bytes(value: Any) -> str:
    if not isinstance(value, (int, float)) or value <= 0:
        return ""
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return ""


def _fmt_eta(value: Any) -> str:
    if not isinstance(value, (int, float)) or value <= 0:
        return ""
    total = int(value)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class _Logger:
    """Adapts yt-dlp's logger interface onto the event sink."""

    def __init__(self, job: Job, sink: Sink, verbose: bool) -> None:
        self.job, self.sink, self.verbose = job, sink, verbose

    def debug(self, msg: str) -> None:
        if msg.startswith("[debug] "):
            if self.verbose:
                self.sink(Event("log", self.job, msg))
            return
        self.info(msg)

    def info(self, msg: str) -> None:
        self.sink(Event("log", self.job, msg))

    def warning(self, msg: str) -> None:
        self.sink(Event("log", self.job, msg, level="warning"))

    def error(self, msg: str) -> None:
        self.sink(Event("log", self.job, msg, level="error"))


def build_ydl_opts(cfg: Config, urls: Iterable[str] = ()) -> dict[str, Any]:
    """Run the config through yt-dlp's own CLI parser."""
    parsed = yt_dlp.parse_options(build_argv(cfg, list(urls)))
    return dict(parsed.ydl_opts)


def probe(url: str, cfg: Config, *, process: bool = False) -> dict[str, Any]:
    """Fetch metadata without downloading. Used by the wizard and `ytkit info`."""
    opts = build_ydl_opts(cfg)
    opts.update(quiet=True, no_warnings=True, noprogress=True, skip_download=True)
    opts["extract_flat"] = "in_playlist"
    for key in ("writeinfojson", "writedescription", "writethumbnail", "writesubtitles",
                "writeautomaticsub", "logger", "progress_hooks", "postprocessors"):
        opts.pop(key, None)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.sanitize_info(ydl.extract_info(url, download=False, process=process))


class _RomanisePP(PostProcessor):
    """Rewrites path-forming fields before yt-dlp computes any filename.

    Must run at ``pre_process``: by the time the download starts, the output
    template has already been rendered.
    """

    def run(self, info: dict[str, Any]) -> tuple[list, dict[str, Any]]:
        return [], romanise_mod.romanise_info(info)


class _SidecarPP(PostProcessor):
    """Writes ytkit's sidecars for each entry the moment yt-dlp finishes it.

    Registered as an ``after_video`` postprocessor, which fires per entry even
    when nothing is downloaded (metadata-only runs). Doing the work here rather
    than after ``extract_info`` returns means a playlist that stops early —
    archive hit, download cap, filter — still keeps sidecars for what it did
    complete, instead of losing them to the raised exception.
    """

    def __init__(self, engine: Engine, job: Job) -> None:
        super().__init__()
        self.engine, self.job = engine, job

    def run(self, info: dict[str, Any]) -> tuple[list, dict[str, Any]]:
        try:
            self.engine._postprocess_entry(self.job, self._downloader, info)
        except Exception as exc:  # noqa: BLE001 - never break the download
            self.engine.sink(
                Event("log", self.job, f"sidecar failed: {exc}", level="warning")
            )
        return [], info


class Engine:
    """Runs jobs on a small thread pool, reporting progress through a sink."""

    def __init__(self, sink: Sink, *, workers: int = 2) -> None:
        self.sink = sink
        self.jobs: dict[int, Job] = {}
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="ytkit")
        self._futures: dict[int, Future] = {}
        self._lock = threading.Lock()
        self._closed = False

    # -- lifecycle ------------------------------------------------------------

    def submit(self, job: Job) -> Job:
        with self._lock:
            self.jobs[job.id] = job
            closed = self._closed
        if closed:
            # Shutdown races a late submission (e.g. a keypress during quit);
            # record the job as cancelled rather than raising at the caller.
            self._finish(job, State.CANCELLED, "engine is shutting down")
            return job
        self._emit_state(job, State.QUEUED)
        self._futures[job.id] = self._pool.submit(self._run, job)
        return job

    def submit_urls(self, urls: Iterable[str], cfg: Config) -> list[Job]:
        return [self.submit(Job(url=u, config=cfg.copy())) for u in urls]

    def cancel(self, job_id: int) -> None:
        job = self.jobs.get(job_id)
        if job is None or job.state in TERMINAL:
            return
        job.cancel()
        future = self._futures.get(job_id)
        if future is not None and future.cancel():  # never started
            self._finish(job, State.CANCELLED)

    def forget(self, job_id: int) -> bool:
        """Drop a finished job from the record. Running jobs are kept."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None or job.state not in TERMINAL:
                return False
            del self.jobs[job_id]
            self._futures.pop(job_id, None)
        return True

    def cancel_all(self) -> None:
        for job_id in list(self.jobs):
            self.cancel(job_id)

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.cancel_all()
        self._pool.shutdown(wait=wait, cancel_futures=True)

    @property
    def active(self) -> int:
        return sum(1 for j in self.jobs.values() if j.state not in TERMINAL)

    # -- execution ------------------------------------------------------------

    def _emit_state(self, job: Job, state: State, message: str = "") -> None:
        job.state = state
        self.sink(Event("state", job, message))

    def _finish(self, job: Job, state: State, message: str = "") -> None:
        job.finished_at = time.monotonic()
        if state is State.DONE:
            job.percent = 100.0
        job.speed = job.eta = ""
        # Postprocessors replace files as they go (a .vtt becomes a .srt, the
        # merged .mp4 replaces its parts), so a path recorded mid-run may be
        # gone by now. Report only what survived.
        job.files = [p for p in job.files if p.exists()]
        self._emit_state(job, state, message)

    def _make_ydl(self, opts: dict[str, Any]) -> yt_dlp.YoutubeDL:
        """Construct the downloader.

        A seam for tests: patching yt_dlp.YoutubeDL at module level would also
        break parse_options, which reaches for the real class.
        """
        return yt_dlp.YoutubeDL(opts)

    def _run(self, job: Job) -> None:
        if job._cancel.is_set():
            self._finish(job, State.CANCELLED)
            return

        job.started_at = time.monotonic()
        cfg = job.config
        opts = build_ydl_opts(cfg, [job.url])
        opts.update(
            quiet=True,
            no_warnings=not cfg.verbose,
            noprogress=True,
            consoletitle=False,
            logger=_Logger(job, self.sink, cfg.verbose),
            progress_hooks=[lambda d: self._on_progress(job, d)],
            postprocessor_hooks=[lambda d: self._on_postprocess(job, d)],
        )

        self._emit_state(job, State.RUNNING)
        stop_reason = ""
        try:
            with self._make_ydl(opts) as ydl:
                # Sidecars are written per entry, as soon as yt-dlp finishes it,
                # so a playlist that stops early still keeps what it completed.
                if cfg.romanise_filenames:
                    ydl.add_post_processor(_RomanisePP(), when="pre_process")
                ydl.add_post_processor(_SidecarPP(self, job), when="after_video")
                try:
                    info = ydl.extract_info(job.url, download=True)
                except PLANNED_STOP as exc:
                    info, stop_reason = None, STOP_REASONS.get(type(exc), "stopped early")
                if info is not None:
                    self._emit_state(job, State.POST)
                    self._postprocess(job, ydl, ydl.sanitize_info(info))
        except DownloadCancelled:
            self._finish(job, State.CANCELLED)
            return
        except DownloadError as exc:
            job.error = str(exc).strip().splitlines()[-1] if str(exc).strip() else "download failed"
            self._finish(job, State.FAILED, job.error)
            return
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            job.error = f"{type(exc).__name__}: {exc}"
            self.sink(Event("log", job, job.error, level="error"))
            self._finish(job, State.FAILED, job.error)
            return

        if job._cancel.is_set():
            self._finish(job, State.CANCELLED)
        else:
            job.detail = stop_reason
            self._finish(job, State.DONE, stop_reason)

    # -- hooks ----------------------------------------------------------------

    def _on_progress(self, job: Job, d: dict[str, Any]) -> None:
        if job._cancel.is_set():
            raise DownloadCancelled("cancelled by user")

        status = d.get("status")
        info = d.get("info_dict") or {}
        if info.get("title") and not job.title:
            job.title = str(info["title"])

        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                job.percent = min(100.0, done * 100.0 / total)
                job.size = _fmt_bytes(total)
            speed = d.get("speed")
            job.speed = f"{_fmt_bytes(speed)}/s" if speed else ""
            job.eta = _fmt_eta(d.get("eta"))
            fragments = d.get("fragment_index")
            if fragments and d.get("fragment_count"):
                job.detail = f"fragment {fragments}/{d['fragment_count']}"
            self.sink(Event("progress", job))
        elif status == "finished":
            job.percent = 100.0
            job.speed = job.eta = ""
            job.detail = "merging / processing"
            filename = d.get("filename")
            if filename:
                self._add_file(job, Path(filename))
            self.sink(Event("progress", job))
        elif status == "error":
            self.sink(Event("log", job, "download error", level="error"))

    def _on_postprocess(self, job: Job, d: dict[str, Any]) -> None:
        if job._cancel.is_set():
            raise DownloadCancelled("cancelled by user")
        if d.get("status") == "started":
            job.detail = str(d.get("postprocessor") or "postprocessing")
            self.sink(Event("progress", job))

    def _add_file(self, job: Job, path: Path) -> None:
        # yt-dlp reports an intended filepath even when nothing is written
        # (skip-download, or a file replaced by a postprocessor), so only
        # record paths that actually exist.
        if path in job.files or not path.exists():
            return
        job.files.append(path)
        self.sink(Event("file", job, str(path)))

    # -- post-download work ---------------------------------------------------

    def _postprocess(self, job: Job, ydl: yt_dlp.YoutubeDL, info: dict[str, Any]) -> None:
        """Backstop for any entry the per-entry hook did not already handle."""
        for entry in _iter_entries(info):
            self._postprocess_entry(job, ydl, entry)

    def _postprocess_entry(self, job: Job, ydl: yt_dlp.YoutubeDL, entry: dict[str, Any]) -> None:
        """Write ytkit's sidecars for a single completed entry, once."""
        marker = str(entry.get("id") or entry.get("webpage_url") or id(entry))
        with self._lock:
            if marker in job._processed:
                return
            job._processed.add(marker)

        cfg = job.config
        job.items_done += 1
        if not job.title:
            job.title = str(entry.get("title") or "")

        base = _entry_basepath(ydl, entry)
        if base is None:
            return

        for download in entry.get("requested_downloads") or []:
            if download.get("filepath"):
                self._add_file(job, Path(download["filepath"]))

        header = f"# {entry.get('title', '')}\n{entry.get('webpage_url', '')}"
        for caption in transcripts.find_captions(base.parent, base.stem):
            self._add_file(job, caption)  # the final, post-conversion track
            if not cfg.transcript_txt:
                continue
            written = transcripts.convert_file(
                caption, timestamps=cfg.transcript_timestamps, header=header
            )
            if written:
                self._add_file(job, written)

        if cfg.write_summary:
            try:
                self._add_file(job, write_summary(entry, base.with_suffix(".summary.md")))
            except OSError as exc:
                self.sink(Event("log", job, f"summary failed: {exc}", level="warning"))

        # yt-dlp writes .info.json, .description and thumbnails without
        # announcing them through any hook, so sweep for them by name.
        for path in _sibling_outputs(base):
            self._add_file(job, path)


def _sibling_outputs(base: Path) -> list[Path]:
    """Every file written for one entry, matched on the exact filename stem.

    Requiring `stem` to be followed by '.' keeps "Talk 1" from claiming the
    files belonging to "Talk 10".
    """
    stem = base.stem
    if not base.parent.is_dir():
        return []
    return sorted(
        p
        for p in base.parent.glob(f"{transcripts.glob_escape(stem)}*")
        if p.is_file() and (p.name == stem or p.name.startswith(f"{stem}."))
    )


def _iter_entries(info: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield leaf video entries from a video, playlist, or nested playlist."""
    if info.get("_type") in ("playlist", "multi_video"):
        for entry in info.get("entries") or []:
            if entry:
                yield from _iter_entries(entry)
    else:
        yield info


def _entry_basepath(ydl: yt_dlp.YoutubeDL, entry: dict[str, Any]) -> Path | None:
    """The on-disk path stem shared by an entry's media and sidecar files."""
    for download in entry.get("requested_downloads") or []:
        if download.get("filepath"):
            return Path(download["filepath"])
    try:
        return Path(ydl.prepare_filename(entry))
    except Exception:  # noqa: BLE001 - template errors must not kill the job
        return None
