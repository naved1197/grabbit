"""Grabbit's download engine for the Android app.

The app starts this with the Python that youtubedl-android bundles and talks to it
over newline-delimited JSON on stdin/stdout. All the real work is grabbit-web's own
engine (``app.resolve``, ``app.jobs`` …), so the phone downloads exactly like the
website and the desktop app do.

Requests   {"id": 1, "op": "lookup", "url": "…", "cookies": "…"}
Replies    {"id": 1, "ok": true, "result": {…}}  or  {"id": 1, "ok": false, "error": "…", "code": "…"}
Events     {"event": "jobs", "jobs": [{…job…, "path": "/…/file.mp4"}]}   (only jobs that changed)

Ops: hello, lookup, submit, batch, cancel, remove, jobs, ping.
Logs go to stderr. When stdin closes (the app went away) the engine kills its
ffmpeg/QuickJS children and exits.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable

PROTOCOL = 1
LOOKUP_TIMEOUT = 90
EVENT_INTERVAL = 0.4
DONE_MARKER = ".grabbit-done"  # written next to a finished file, so a restarted engine can hand it over

# Phone-sized defaults; the app can override any of them through the environment.
_DEFAULTS = {
    "GRABBIT_MAX_CONCURRENT": "2",
    "GRABBIT_MAX_JOBS_PER_IP": "1000",
    "GRABBIT_MAX_FILESIZE_MB": "0",
    "GRABBIT_MAX_DURATION_MIN": "0",
    "GRABBIT_FILE_TTL_MIN": "1440",
    "GRABBIT_PLAYLIST_LIMIT": "100",
    "GRABBIT_ALLOW_USER_COOKIES": "true",
    "GRABBIT_ALLOW_PRIVATE_URLS": "true",
    "GRABBIT_APPLE_COMPAT": "false",
}
CLIENT = "android"  # JobManager's per-visitor bookkeeping key

log = logging.getLogger("grabbit.engine")


class Protocol:
    """The request/reply channel, on private copies of fds 0 and 1.

    fd 0 becomes /dev/null and fd 1 becomes stderr, so the ffmpeg/ffprobe/QuickJS
    processes yt-dlp starts (and any stray print) can never read or corrupt it.
    """

    def __init__(self) -> None:
        rfd, wfd = os.dup(0), os.dup(1)
        null = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null, 0)
        os.close(null)
        os.dup2(2, 1)
        self.reader = os.fdopen(rfd, "r", encoding="utf-8", errors="replace")
        self.writer = os.fdopen(wfd, "w", encoding="utf-8", buffering=1)
        self.lock = threading.Lock()

    def send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"), default=str)
        with self.lock:
            try:
                self.writer.write(line + "\n")
                self.writer.flush()
            except (BrokenPipeError, OSError):
                _shutdown()


class Engine:
    def __init__(self, proto: Protocol) -> None:
        for key, value in _DEFAULTS.items():
            os.environ.setdefault(key, value)

        import yt_dlp

        from app.config import load_settings
        from app.jobs import MARKER, JobManager
        from app.ytdl import InfoCache, ffmpeg_path, js_runtime

        self.proto = proto
        self.settings = load_settings()
        self.settings.download_dir.mkdir(parents=True, exist_ok=True)
        self.recovered = self._recover(self.settings.download_dir, MARKER)  # before JobManager wipes leftovers
        self.cache = InfoCache(size=16)  # YouTube info is big; a phone doesn't need 256 of them
        self.manager = JobManager(self.settings, self.cache)
        self.manager.start()
        self.versions = {
            "protocol": PROTOCOL,
            "ytdlp": yt_dlp.version.__version__,
            "python": sys.version.split()[0],
            "ffmpeg": bool(ffmpeg_path()),
            "js_runtime": js_runtime(),
        }
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="req")
        self.lookups = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lookup")
        self._sent: dict[str, str] = {}
        self._done: set[str] = set()
        threading.Thread(target=self._events, name="events", daemon=True).start()
        log.info("engine ready: %s, downloads in %s", self.versions, self.settings.download_dir)

    # ------------------------------------------------------------ recovery

    @staticmethod
    def _recover(root: Path, marker: str) -> list[dict[str, Any]]:
        """Finished files a previous engine didn't get to hand over (it was killed)."""
        out_dir = root.parent / "recovered"
        found = []
        for child in sorted(root.iterdir()) if root.is_dir() else []:
            done = child / DONE_MARKER
            if not (child.is_dir() and (child / marker).exists() and done.is_file()):
                continue
            name = done.read_text(encoding="utf-8").strip()
            src = child / name
            if name and src.is_file():
                out_dir.mkdir(parents=True, exist_ok=True)
                dst = out_dir / f"{child.name}-{name}"
                shutil.move(str(src), dst)
                found.append({"job": child.name, "name": name, "path": str(dst), "size": dst.stat().st_size})
        return found

    # -------------------------------------------------------------- events

    def _jobs(self) -> list[Any]:
        for _ in range(5):
            try:
                return list(self.manager.jobs.values())
            except RuntimeError:  # changed while copying
                time.sleep(0.01)
        return []

    def snapshot(self, job: Any) -> dict[str, Any]:
        data = self.manager.public(job)
        data["path"] = str(job.filepath) if job.status == "finished" and job.filepath else None
        return data

    def _events(self) -> None:
        while True:
            time.sleep(EVENT_INTERVAL)
            try:
                changed = []
                for job in self._jobs():
                    data = self.snapshot(job)
                    if data["status"] == "finished" and job.id not in self._done and job.filepath:
                        self._done.add(job.id)
                        try:
                            (job.dir / DONE_MARKER).write_text(job.filepath.name, encoding="utf-8")
                        except OSError:
                            pass
                    key = json.dumps(data, sort_keys=True, default=str)
                    if self._sent.get(job.id) != key:
                        self._sent[job.id] = key
                        changed.append(data)
                if changed:
                    self.proto.send({"event": "jobs", "jobs": changed})
            except Exception:  # noqa: BLE001 - the event loop must keep going
                log.exception("event loop")

    # ------------------------------------------------------------ requests

    def handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("not JSON: %.200s", raw)
            return
        rid, op = msg.get("id"), msg.get("op")
        handler: Callable[[dict[str, Any]], Any] | None = getattr(self, f"op_{op}", None)
        if handler is None:
            self.proto.send({"id": rid, "ok": False, "error": f"Unknown op {op!r}", "code": "bad_request"})
            return

        def run() -> None:
            self.proto.send(self._reply(rid, handler, msg))

        self.pool.submit(run)

    def _reply(self, rid: Any, handler: Callable[[dict[str, Any]], Any], msg: dict[str, Any]) -> dict[str, Any]:
        from app.cookies import BadCookies
        from app.security import BadURL
        from app.ytdl import UserFacingError, friendly_error

        try:
            return {"id": rid, "ok": True, "result": handler(msg)}
        except UserFacingError as e:
            return {"id": rid, "ok": False, "error": str(e), "code": e.code}
        except BadCookies as e:
            return {"id": rid, "ok": False, "error": str(e), "code": "bad_cookies"}
        except BadURL as e:
            return {"id": rid, "ok": False, "error": str(e), "code": "bad_url"}
        except _Failure as e:
            return {"id": rid, "ok": False, "error": str(e), "code": e.code}
        except Exception as e:  # noqa: BLE001
            log.exception("op %s failed", msg.get("op"))
            return {"id": rid, "ok": False, "error": friendly_error(e), "code": "internal"}

    # Helpers ---------------------------------------------------------------

    @staticmethod
    def _url(raw: Any) -> str:
        from app.security import normalize_url

        return normalize_url(str(raw or ""))

    def _cookies(self, raw: Any):
        from app.cookies import parse_cookies

        return parse_cookies(raw) if isinstance(raw, str) and raw.strip() else None

    @staticmethod
    def _choice(raw: Any):
        from app.formats import DownloadChoice

        fields = ("mode", "quality", "container", "audio_format")
        try:
            return DownloadChoice(**{k: v for k, v in (raw or {}).items() if k in fields and v is not None})
        except (TypeError, ValueError) as e:
            raise _Failure(str(e), "bad_choice") from None

    @staticmethod
    def _meta(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        meta = {k: raw.get(k) for k in ("title", "thumbnail", "uploader", "duration")}
        return meta if any(v is not None for v in meta.values()) else None

    def _job(self, msg: dict[str, Any]) -> Any:
        job = self.manager.get(str(msg.get("job") or ""))
        if job is None:
            raise _Failure("That download isn't running any more.", "not_found")
        return job

    # Ops -----------------------------------------------------------------

    def op_ping(self, msg: dict[str, Any]) -> str:
        return "pong"

    def op_hello(self, msg: dict[str, Any]) -> dict[str, Any]:
        return {**self.versions, "recovered": self.recovered}

    def op_lookup(self, msg: dict[str, Any]) -> dict[str, Any]:
        from app.resolve import lookup

        url, jar = self._url(msg.get("url")), self._cookies(msg.get("cookies"))
        future = self.lookups.submit(lookup, url, self.settings, self.cache, jar)
        try:
            return future.result(timeout=LOOKUP_TIMEOUT)
        except FutureTimeout:
            raise _Failure("The site took too long to answer. Try again.", "timeout") from None

    def op_submit(self, msg: dict[str, Any]) -> dict[str, Any]:
        url, jar, choice = self._url(msg.get("url")), self._cookies(msg.get("cookies")), self._choice(msg.get("choice"))
        item = msg.get("item")
        job = self.manager.submit(url, choice, CLIENT, self._meta(msg.get("meta")), jar,
                                  item=int(item) if item else None)
        return self.snapshot(job)

    def op_batch(self, msg: dict[str, Any]) -> dict[str, Any]:
        from app.cookies import BadCookies
        from app.security import BadURL

        jar, choice = self._cookies(msg.get("cookies")), self._choice(msg.get("choice"))
        created, errors = [], []
        for entry in (msg.get("items") or [])[: self.settings.playlist_limit]:
            try:
                url = self._url(entry.get("url"))
                item = entry.get("item")
                job = self.manager.submit(url, choice, CLIENT, self._meta(entry.get("meta")), jar,
                                          item=int(item) if item else None)
                created.append(self.snapshot(job))
            except (BadURL, BadCookies, ValueError) as e:
                errors.append({"url": entry.get("url"), "error": str(e)})
        return {"jobs": created, "errors": errors}

    def op_cancel(self, msg: dict[str, Any]) -> dict[str, Any]:
        job = self.manager.cancel(self._job(msg).id)
        return self.snapshot(job)

    def op_remove(self, msg: dict[str, Any]) -> dict[str, Any]:
        job_id = str(msg.get("job") or "")
        removed = self.manager.remove(job_id)
        self._sent.pop(job_id, None)
        self._done.discard(job_id)
        return {"removed": removed}

    def op_jobs(self, msg: dict[str, Any]) -> list[dict[str, Any]]:
        return [self.snapshot(j) for j in self._jobs()]


class _Failure(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _shutdown(*_: Any) -> None:
    """Take every child (ffmpeg, QuickJS) down with us."""
    logging.shutdown()
    try:
        if os.getpgrp() == os.getpid():  # only when we lead our own group (never the app's or a test runner's)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)  # children started earlier keep the default: they stop
            os.killpg(os.getpgrp(), signal.SIGTERM)
    finally:
        os._exit(0)


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=os.environ.get("GRABBIT_LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        os.setpgrp()  # our own process group, so _shutdown only takes our children
    except OSError:
        pass
    proto = Protocol()
    try:
        engine = Engine(proto)
    except Exception as e:  # noqa: BLE001 - tell the app why, then stop
        log.exception("engine failed to start")
        proto.send({"event": "fatal", "error": f"{type(e).__name__}: {e}"})
        _shutdown()
        return
    proto.send({"event": "ready", **engine.versions})
    for line in proto.reader:
        if line.strip():
            engine.handle(line)
    log.info("app closed the pipe; exiting")
    _shutdown()


if __name__ == "__main__":
    main()
