"""Background downloads: a small worker pool, progress tracking, cleanup."""

from __future__ import annotations

import copy
import logging
import os
import re
import subprocess
import secrets
import shutil
import threading
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.networking import Request
from yt_dlp.utils import DownloadCancelled, DownloadError, YoutubeDLError, sanitize_filename

from .config import Settings
from .formats import DownloadChoice, best_thumbnail, format_options
from .compat import ConvertError, apple_codec, convert_for_apple, needs_conversion, probe
from .cookies import CookieSet
from .resolve import Resolved, post_key, resolve
from .ytdl import (
    InfoCache,
    UserFacingError,
    YtdlLogger,
    base_options,
    cache_key,
    describe_error,
    ffmpeg_path,
    friendly_error,
)

log = logging.getLogger("grabbit.jobs")

MARKER = ".grabbit-job"
ACTIVE = ("queued", "starting", "downloading", "processing")
DONE = ("finished", "error", "canceled")
_TEMP_SUFFIXES = (".part", ".ytdl", ".temp", ".tmp", ".frag", ".json", ".txt")
# "Best" on a long video picks a Mac-friendly stream over converting one (slow).
LONG_CONVERT_SECONDS = 20 * 60
_IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
                "image/heic": "heic", "image/avif": "avif"}

_PP_STAGES = {
    "Merger": "Merging video and audio",
    "ExtractAudio": "Converting audio",
    "VideoRemuxer": "Preparing file",
    "VideoConvertor": "Converting video",
    "Metadata": "Adding title and chapters",
    "EmbedThumbnail": "Adding cover art",
    "ThumbnailsConvertor": "Preparing cover art",
    "MoveFiles": "Finishing up",
    "FixupM3u8": "Fixing up file",
    "FixupM4a": "Fixing up file",
    "FixupTimestamp": "Fixing up file",
    "FixupDuplicateMoov": "Fixing up file",
}


class JobLimitError(Exception):
    pass


def _image_ext(content_type: str | None, url: str) -> str:
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind in _IMAGE_TYPES:
        return _IMAGE_TYPES[kind]
    suffix = Path(urlsplit(url).path).suffix.lower().lstrip(".")
    return suffix if suffix in _IMAGE_TYPES.values() else "jpg"


@dataclass
class Job:
    id: str
    url: str
    choice: DownloadChoice
    ip: str
    dir: Path
    title: str | None = None
    thumbnail: str | None = None
    uploader: str | None = None
    duration: float | None = None
    item: int | None = None  # which item of a multi-item post (1-based)
    status: str = "queued"
    stage: str = "Waiting in line"
    progress: float | None = None  # 0..100
    speed: float | None = None  # bytes/s
    eta: int | None = None  # seconds
    downloaded: int | None = None
    total: int | None = None
    filename: str | None = None
    filepath: Path | None = None
    filesize: int | None = None
    error: str | None = None
    error_code: str | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    # The visitor's cookies, only until the job ends. Never sent back out.
    cookies: CookieSet | None = field(default=None, repr=False)
    # progress bookkeeping
    _parts: list[str] = field(default_factory=list)
    _part_sizes: list[int | None] = field(default_factory=list)
    _part_done: dict[int, int] = field(default_factory=dict)

    def public(self, queue_position: int | None = None, ttl_min: int = 60) -> dict[str, Any]:
        expires = self.finished + ttl_min * 60 if self.finished and self.status == "finished" else None
        return {
            "id": self.id,
            "url": self.url,
            "mode": self.choice.mode,
            "quality": self.choice.quality,
            "format": self.choice.audio_format if self.choice.mode == "audio" else self.choice.container,
            "title": self.title,
            "thumbnail": self.thumbnail,
            "uploader": self.uploader,
            "duration": self.duration,
            "item": self.item,
            "status": self.status,
            "stage": self.stage,
            "progress": None if self.progress is None else round(self.progress, 1),
            "speed": self.speed,
            "eta": self.eta,
            "downloaded": self.downloaded,
            "total": self.total,
            "filename": self.filename,
            "filesize": self.filesize,
            "error": self.error,
            "error_code": self.error_code,
            "queue_position": queue_position,
            "created": self.created,
            "expires": expires,
        }


class JobManager:
    def __init__(self, settings: Settings, cache: InfoCache):
        self.settings = settings
        self.cache = cache
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=settings.max_concurrent, thread_name_prefix="dl")
        self._stop = threading.Event()
        self._janitor = threading.Thread(target=self._janitor_loop, name="janitor", daemon=True)
        settings.download_dir.mkdir(parents=True, exist_ok=True)
        self._remove_leftovers()

    # ------------------------------------------------------------------ API

    def start(self) -> None:
        self._janitor.start()

    def shutdown(self) -> None:
        self._stop.set()
        for job in list(self.jobs.values()):
            job.cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def submit(self, url: str, choice: DownloadChoice, ip: str, meta: dict[str, Any] | None = None,
               cookies: CookieSet | None = None, item: int | None = None) -> Job:
        with self._lock:
            active = sum(1 for j in self.jobs.values() if j.ip == ip and j.status in ACTIVE)
            if active >= self.settings.max_jobs_per_ip:
                raise JobLimitError(
                    f"You already have {active} downloads in progress. Wait for one to finish."
                )
            job_id = secrets.token_urlsafe(12)
            job = Job(id=job_id, url=url, choice=choice, ip=ip, dir=self.settings.download_dir / job_id,
                      cookies=cookies, item=item)
            if meta:
                job.title = meta.get("title")
                job.thumbnail = meta.get("thumbnail")
                job.uploader = meta.get("uploader")
                job.duration = meta.get("duration")
            self.jobs[job_id] = job
        # One pool task per job; each task runs whichever queued job is
        # fairest next, so one visitor's long queue can't starve others.
        self._pool.submit(self._run_next)
        return job

    def _run_next(self) -> None:
        with self._lock:
            queued = [j for j in self.jobs.values() if j.status == "queued" and not j.cancel.is_set()]
            if not queued:
                return
            running = Counter(
                j.ip for j in self.jobs.values() if j.status in ACTIVE and j.status != "queued"
            )
            job = min(queued, key=lambda j: (running[j.ip], j.created))
            job.status = "starting"
        self._run(job)

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if not job:
            return None
        job.cancel.set()
        if job.status == "queued":
            self._finish(job, "canceled", stage="Canceled")
        return job

    def remove(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        job.cancel.set()
        if job.status in DONE or job.status == "queued":
            with self._lock:
                self.jobs.pop(job_id, None)
            shutil.rmtree(job.dir, ignore_errors=True)
        return True

    def queue_position(self, job: Job) -> int | None:
        if job.status != "queued":
            return None
        queued = sorted((j for j in self.jobs.values() if j.status == "queued"), key=lambda j: j.created)
        running = sum(1 for j in self.jobs.values() if j.status in ACTIVE and j.status != "queued")
        free = max(self.settings.max_concurrent - running, 0)
        idx = next((i for i, j in enumerate(queued) if j.id == job.id), 0)
        return max(idx - free + 1, 0) or None

    def public(self, job: Job) -> dict[str, Any]:
        return job.public(self.queue_position(job), self.settings.file_ttl_min)

    def stats(self) -> dict[str, int]:
        jobs = list(self.jobs.values())
        return {
            "active": sum(1 for j in jobs if j.status in ACTIVE and j.status != "queued"),
            "queued": sum(1 for j in jobs if j.status == "queued"),
        }

    # -------------------------------------------------------------- worker

    def _run(self, job: Job) -> None:
        if job.cancel.is_set() or job.status in DONE:
            return
        job.stage = "Getting video details"
        job.progress = None
        logger = YtdlLogger(f"job.{job.id[:6]}")
        try:
            job.dir.mkdir(parents=True, exist_ok=True)
            (job.dir / MARKER).touch()
            opts = self._options(job, logger)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.add_post_processor(_BeforeDownload(self, job), when="before_dl")
                if (self.settings.apple_compat and job.choice.mode == "video"
                        and job.choice.container == "mp4" and ffmpeg_path()):
                    ydl.add_post_processor(_AppleCompat(job), when="post_process")
                media, from_cache = self._extract(ydl, job, logger)
                if job.cancel.is_set():
                    raise DownloadCancelled()
                if media.photos:
                    path = self._save_photos(job, ydl, media)
                else:
                    path = self._download_video(job, ydl, logger, media, from_cache)
            job.filepath = path
            job.filename = path.name
            job.filesize = path.stat().st_size
            job.progress = 100.0
            job.speed = None
            job.eta = None
            self._finish(job, "finished", stage="Ready to save")
        except DownloadCancelled:
            self._finish(job, "canceled", stage="Canceled")
        except UserFacingError as e:
            self._finish(job, "error", stage="Failed", error=str(e), code=e.code)
        except YoutubeDLError as e:  # DownloadError, or an ExtractorError from format selection
            if job.cancel.is_set():
                self._finish(job, "canceled", stage="Canceled")
            else:
                message, code = describe_error(e, job.cookies, job.url)
                self._finish(job, "error", stage="Failed", error=message, code=code)
        except Exception as e:  # noqa: BLE001 - never let a worker die silently
            log.exception("job %s crashed", job.id)
            self._finish(job, "error", stage="Failed", error=friendly_error(e))

    def _options(self, job: Job, logger: YtdlLogger) -> dict[str, Any]:
        opts = base_options(self.settings, logger, job.dir, job.cookies)
        opts.update(format_options(job.choice, has_ffmpeg=bool(ffmpeg_path())))
        opts.update({
            "paths": {"home": str(job.dir), "temp": str(job.dir)},
            "outtmpl": {"default": "%(title).150B.%(ext)s", "thumbnail": "%(title).150B.%(ext)s"},
            "progress_hooks": [lambda d: self._on_progress(job, d)],
            "postprocessor_hooks": [lambda d: self._on_postprocess(job, d)],
            "max_filesize": self.settings.max_filesize_bytes,
            "overwrites": True,
        })
        if ffmpeg_path():
            opts["ffmpeg_location"] = ffmpeg_path()
        return opts

    def _extract(self, ydl: yt_dlp.YoutubeDL, job: Job, logger: YtdlLogger,
                 use_cache: bool = True) -> tuple[Resolved, bool]:
        """What to download, and whether it came from the page's cached lookup."""
        mode, hint = job.choice.mode, job.duration  # the length of what they were watching, if known
        post = self.cache.get(post_key(job.url, job.cookies)) if use_cache else None
        cached = self.cache.get(cache_key(job.url, job.cookies)) if use_cache and not post else None
        try:
            if post:
                media = resolve(ydl, job.url, logger, mode=mode, item=job.item, duration=hint,
                                raw=copy.deepcopy(post))
                from_cache = bool(media.video)
            elif cached and cached.get("formats"):
                info = copy.deepcopy(cached)
                media = Resolved(title=info.get("title") or "Untitled", video=info, thumbnail=best_thumbnail(info),
                                 uploader=info.get("uploader") or info.get("channel"), duration=info.get("duration"))
                from_cache = True
            else:
                media = resolve(ydl, job.url, logger, mode=mode, item=job.item, duration=hint)
                from_cache = False
        except YoutubeDLError as e:
            raise UserFacingError(*describe_error(e, job.cookies, job.url)) from None
        job.title = job.title or media.title
        job.uploader = job.uploader or media.uploader
        job.duration = job.duration or media.duration
        job.thumbnail = job.thumbnail or media.thumbnail
        return media, from_cache

    def _download_video(self, job: Job, ydl: yt_dlp.YoutubeDL, logger: YtdlLogger, media: Resolved,
                        from_cache: bool) -> Path:
        assert media.video is not None
        self._precheck(job, media.video)
        if job.cancel.is_set():
            raise DownloadCancelled()
        if self.settings.apple_compat:  # only worth it when the file would otherwise be converted
            self._prefer_apple_friendly(job, ydl, media.video)
        job.status = "downloading"
        job.stage = "Starting download"
        try:
            result = ydl.process_ie_result(media.video, download=True)
        except DownloadError:
            if job.cancel.is_set() or not from_cache:
                raise
            # Cached stream URLs can expire: extract again and retry once.
            log.info("job %s: retrying with a fresh extraction", job.id)
            self.cache.drop(cache_key(job.url, job.cookies))
            self.cache.drop(post_key(job.url, job.cookies))
            job._parts.clear()
            job._part_done.clear()
            media, _ = self._extract(ydl, job, logger, use_cache=False)
            if not media.video:
                raise UserFacingError("This link no longer has the same video. Look it up again.") from None
            result = ydl.process_ie_result(media.video, download=True)
        if job.cancel.is_set():
            raise DownloadCancelled()
        path = self._final_file(job, result)
        if not path:
            hint = logger.errors[-1] if logger.errors else ""
            if "max-filesize" in hint or "larger than max" in " ".join(logger.warnings + logger.errors):
                raise UserFacingError(self._size_message())
            if hint:
                raise UserFacingError(*describe_error(hint, job.cookies, job.url))
            raise UserFacingError("The download didn't produce a file.")
        return path

    @staticmethod
    def _prefer_apple_friendly(job: Job, ydl: yt_dlp.YoutubeDL, info: dict[str, Any]) -> None:
        """For "best" MP4s of long videos, take a stream Apple devices play over converting one.

        (Short videos keep the best stream; _AppleCompat converts it if needed.)
        """
        choice = job.choice
        if choice.mode != "video" or choice.container != "mp4" or choice.quality != "best":
            return
        if (info.get("duration") or 0) <= LONG_CONVERT_SECONDS or apple_codec(info.get("vcodec")):
            return
        if any(apple_codec(f.get("vcodec")) for f in info.get("formats") or []):
            ydl.params["format_sort"] = ["vcodec:h264", "res", "ext:mp4:m4a", "acodec:aac"]

    def _save_photos(self, job: Job, ydl: yt_dlp.YoutubeDL, media: Resolved) -> Path:
        """Save a post's photo(s): one image file, or a .zip when there are several."""
        n = len(media.photos)
        limit = self.settings.max_filesize_bytes
        base = re.sub(r'[\\/:*?"<>|]+', "_", sanitize_filename(media.title))[:150].strip(" .") or "photo"
        job.status = "downloading"
        files: list[Path] = []
        done = 0
        for i, photo in enumerate(media.photos, 1):
            job.stage = "Downloading photo" if n == 1 else f"Downloading photos ({i} of {n})"
            job.progress = (i - 1) / n * 100
            try:
                resp = ydl.urlopen(Request(photo.url, headers=photo.headers))
            except YoutubeDLError as e:
                raise UserFacingError(*describe_error(e, job.cookies, job.url)) from None
            ext = _image_ext(resp.headers.get("Content-Type"), photo.url)
            path = job.dir / (f"{base}.{ext}" if n == 1 else f"{base} {i}.{ext}")
            with resp, path.open("wb") as out:
                while chunk := resp.read(256 * 1024):
                    if job.cancel.is_set():
                        raise DownloadCancelled()
                    done += len(chunk)
                    if limit and done > limit:
                        raise UserFacingError(self._size_message())
                    out.write(chunk)
            job.downloaded = done
            files.append(path)
        if n == 1:
            return files[0]
        job.status = "processing"
        job.stage = "Packing photos"
        archive = job.dir / f"{base}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as z:  # photos are already compressed
            for p in files:
                z.write(p, p.name)
        for p in files:
            p.unlink()
        return archive

    def _precheck(self, job: Job, info: dict[str, Any]) -> None:
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            raise UserFacingError("Live streams can't be downloaded here.")
        limit = self.settings.max_duration_min
        if limit and (info.get("duration") or 0) > limit * 60:
            raise UserFacingError(f"This server only downloads videos up to {limit} minutes long.")

    def _size_message(self) -> str:
        mb = self.settings.max_filesize_mb
        size = f"{mb / 1024:.0f} GB" if mb >= 1024 else f"{mb} MB"
        return f"That file is larger than this server's {size} limit. Try a lower quality."

    def _final_file(self, job: Job, result: dict[str, Any] | None) -> Path | None:
        candidates: list[Path] = []
        for d in (result or {}).get("requested_downloads") or []:
            fp = d.get("filepath")
            if fp:
                candidates.append(Path(fp))
        for p in reversed(candidates):
            if p.is_file() and p.parent == job.dir:
                return p
        files = [
            p for p in job.dir.iterdir()
            if p.is_file() and p.name != MARKER and not p.name.endswith(_TEMP_SUFFIXES)
            and p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".vtt", ".srt")
        ]
        return max(files, key=lambda p: p.stat().st_size) if files else None

    # ------------------------------------------------------------- hooks

    def _on_progress(self, job: Job, d: dict[str, Any]) -> None:
        if job.cancel.is_set():
            raise DownloadCancelled()
        info = d.get("info_dict") or {}
        key = str(info.get("format_id") or d.get("filename"))
        if key not in job._parts:
            job._parts.append(key)
            job._part_sizes.append(None)
        idx = job._parts.index(key)
        count = max(len(job._part_sizes), idx + 1, 1)

        status = d.get("status")
        if status == "downloading":
            job.status = "downloading"
            vcodec, acodec = info.get("vcodec"), info.get("acodec")
            if count > 1:
                job.stage = "Downloading audio" if vcodec == "none" else "Downloading video"
                job.stage += f" ({idx + 1} of {count})"
            else:
                job.stage = "Downloading"
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            if total:
                frac = min(done / total, 1.0)
            elif d.get("fragment_count"):
                frac = min((d.get("fragment_index") or 0) / d["fragment_count"], 1.0)
            else:
                frac = None
            sizes = job._part_sizes
            if frac is not None:
                if len(sizes) == count and all(sizes):
                    whole = sum(sizes)  # type: ignore[arg-type]
                    before = sum(sizes[:idx])  # type: ignore[arg-type]
                    overall = (before + frac * sizes[idx]) / whole  # type: ignore[operator]
                else:
                    overall = (idx + frac) / count
                job.progress = min(overall * 100, 99.5)
            job.speed = d.get("speed")
            job.eta = d.get("eta")
            prev = sum(v for k, v in job._part_done.items() if k != idx)
            job.downloaded = prev + done
            known = [s for s in sizes if s]
            job.total = sum(known) if len(known) == count else (prev + total if total else None)
        elif status == "finished":
            job._part_done[idx] = d.get("total_bytes") or d.get("downloaded_bytes") or 0

    def _on_postprocess(self, job: Job, d: dict[str, Any]) -> None:
        if d.get("status") != "started":
            return
        stage = _PP_STAGES.get(str(d.get("postprocessor") or ""))
        if not stage:
            return
        job.status = "processing"
        job.stage = stage
        job.speed = None
        job.eta = None

    # -------------------------------------------------------- lifecycle

    def _finish(self, job: Job, status: str, stage: str, error: str | None = None,
                code: str | None = None) -> None:
        # Forget the visitor's cookies the moment the job is over.
        job.cookies = None
        (job.dir / "cookies.txt").unlink(missing_ok=True)
        job.status = status
        job.stage = stage
        job.error = error
        job.error_code = code
        job.finished = time.time()
        job.speed = None
        job.eta = None
        if status != "finished":
            shutil.rmtree(job.dir, ignore_errors=True)
        log.info("job %s %s%s", job.id, status, f": {error}" if error else "")

    def _janitor_loop(self) -> None:
        while not self._stop.wait(30):
            try:
                self._sweep()
            except Exception:  # noqa: BLE001
                log.exception("janitor failed")

    def _sweep(self) -> None:
        now = time.time()
        ttl = self.settings.file_ttl_min * 60
        expired = [j for j in self.jobs.values() if j.finished and now - j.finished > ttl]
        for job in expired:
            shutil.rmtree(job.dir, ignore_errors=True)
            with self._lock:
                self.jobs.pop(job.id, None)
        # Disk usage guard for long-forgotten folders (e.g. after a crash).
        for child in self.settings.download_dir.iterdir():
            if child.is_dir() and (child / MARKER).exists() and child.name not in self.jobs:
                if now - child.stat().st_mtime > ttl:
                    shutil.rmtree(child, ignore_errors=True)

    def _remove_leftovers(self) -> None:
        for child in self.settings.download_dir.iterdir():
            if child.is_dir() and (child / MARKER).exists():
                shutil.rmtree(child, ignore_errors=True)


class _BeforeDownload(PostProcessor):
    """Runs after yt-dlp picks formats, before any bytes are fetched.

    Records which streams will be downloaded (for an accurate overall progress
    bar) and refuses files over the size limit up front.
    """

    def __init__(self, manager: JobManager, job: Job):
        super().__init__()
        self.manager = manager
        self.job = job

    def run(self, info: dict[str, Any]):
        formats = info.get("requested_formats") or [info]
        duration = info.get("duration")
        sizes: list[int | None] = []
        for f in formats:
            size = f.get("filesize") or f.get("filesize_approx")
            if not size and f.get("tbr") and duration:
                size = int(f["tbr"] * 1000 / 8 * duration)
            sizes.append(int(size) if size else None)
        self.job._parts = [str(f.get("format_id")) for f in formats]
        self.job._part_sizes = sizes
        limit = self.manager.settings.max_filesize_bytes
        if limit and all(sizes) and sum(sizes) > limit:  # type: ignore[arg-type]
            raise UserFacingError(self.manager._size_message())
        return [], info


class _AppleCompat(PostProcessor):
    """Re-encode MP4s that Apple devices won't play (VP9/AV1 video, Opus audio) to H.264/AAC."""

    STAGE = "Converting so it plays on Mac & iPhone"

    def __init__(self, job: Job):
        super().__init__()
        self.job = job

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        path = Path(info.get("filepath") or "")
        job = self.job
        if path.suffix.lower() != ".mp4" or not path.is_file():
            return [], info
        try:
            if not needs_conversion(probe(path)):
                return [], info
        except (subprocess.CalledProcessError, OSError, ValueError):
            return [], info
        job.status = "processing"
        job.stage = self.STAGE
        job.speed = job.eta = None
        tmp = path.with_name(path.name + ".part")  # a temp name the janitor and _final_file ignore
        try:
            convert_for_apple(path, tmp, cancel=job.cancel,
                              on_progress=lambda f: setattr(job, "stage", f"{self.STAGE} · {f:.0%}"))
        except InterruptedError:
            raise DownloadCancelled() from None
        except ConvertError as e:
            # The original still works in VLC/IINA/browsers: keep it rather than fail.
            log.warning("job %s: couldn't convert for Apple devices: %s", job.id, e)
            tmp.unlink(missing_ok=True)
            return [], info
        os.replace(tmp, path)
        return [], info
