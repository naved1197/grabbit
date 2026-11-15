"""Shared yt-dlp plumbing: options, the info cache, friendly errors."""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import Settings
from .cookies import CookieSet

log = logging.getLogger("grabbit.ytdl")

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_PREFIX = re.compile(r"^(ERROR:\s*)?(\[[^\]]+\]\s*)?([\w-]+:\s+)?")

LOGIN_REQUIRED = "login_required"


class UserFacingError(Exception):
    """An error whose message can be shown to the visitor as-is."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class YtdlLogger:
    """Collects yt-dlp warnings/errors instead of printing them."""

    def __init__(self, name: str = "yt-dlp"):
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self._log = logging.getLogger(f"grabbit.{name}")

    def debug(self, msg: str) -> None:
        if "max-filesize" in msg:
            self.warnings.append(_ANSI.sub("", msg))
        self._log.debug(msg)

    def info(self, msg: str) -> None:
        self._log.debug(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(_ANSI.sub("", msg))
        self._log.info("warning: %s", msg)

    def error(self, msg: str) -> None:
        self.errors.append(_ANSI.sub("", msg))
        self._log.info("error: %s", msg)


def tool_path(name: str, env: str) -> str | None:
    """A program yt-dlp runs: the GRABBIT_* override if set (the Android app's bundled
    binaries have library names like libffmpeg.so), else whatever is on PATH."""
    override = os.environ.get(env, "").strip()
    if override:
        return override if os.path.isfile(override) else None
    return shutil.which(name)


def ffmpeg_path() -> str | None:
    return tool_path("ffmpeg", "GRABBIT_FFMPEG")


# (yt-dlp runtime name, program, override variable), most preferred first
_JS_RUNTIMES = (("deno", "deno", "GRABBIT_DENO"), ("node", "node", "GRABBIT_NODE"),
                ("bun", "bun", "GRABBIT_BUN"), ("quickjs", "qjs", "GRABBIT_QJS"))


def _find_js_runtime() -> tuple[str, str] | None:
    for runtime, program, env in _JS_RUNTIMES:
        path = tool_path(program, env)
        if path:
            return runtime, path
    return None


def js_runtime() -> str | None:
    found = _find_js_runtime()
    return found[0] if found else None


def base_options(
    settings: Settings, logger: YtdlLogger, workdir: Path, cookies: CookieSet | None = None
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "noprogress": True,
        "logger": logger,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 10,
        "extractor_retries": 2,
        "concurrent_fragment_downloads": 4,
        "windowsfilenames": True,
        "cachedir": str(settings.download_dir.parent / "yt-dlp-cache"),
    }
    # Lookups need it too: without it yt-dlp assumes nothing can be merged (the Android app's
    # ffmpeg isn't on PATH under that name).
    ffmpeg = ffmpeg_path()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    runtime = _find_js_runtime()
    if runtime:
        opts["js_runtimes"] = {runtime[0]: {"path": runtime[1]}}
    if settings.proxy:
        opts["proxy"] = settings.proxy
    # The visitor's own cookies win; otherwise fall back to the server's.
    # Either way yt-dlp gets a private copy (it rewrites the file on exit).
    if cookies is not None:
        opts["cookiefile"] = str(cookies.write(workdir))
    elif settings.cookies_file and Path(settings.cookies_file).is_file():
        workdir.mkdir(parents=True, exist_ok=True)
        copy = workdir / "cookies.txt"
        shutil.copyfile(settings.cookies_file, copy)
        opts["cookiefile"] = str(copy)
    elif settings.cookies_from_browser:
        opts["cookiesfrombrowser"] = tuple(settings.cookies_from_browser.split(":"))
    return opts


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

# (pattern, message) — errors that a signed-in account (cookies) can fix.
_LOGIN = [
    (re.compile(r"not a bot", re.I),
     "YouTube wants to make sure this isn't a bot."),
    (re.compile(r"follow(s|ing)? this account|registered users who follow", re.I),
     "Only signed-in followers of this account can see this."),
    (re.compile(r"rate-limit reached or login required|login required|log ?in required|requires? (a )?log ?in", re.I),
     "This site wants you to be signed in to see this."),
    (re.compile(r"private (video|account|post|profile)|this (video|account|post) is private|private video", re.I),
     "This video is private."),
    (re.compile(r"age[- ]restricted|confirm your age|inappropriate for some users|age verification", re.I),
     "This video is age-restricted."),
    (re.compile(r"members[- ]only|join this channel|for members|subscriber[- ]only", re.I),
     "This video is for channel members or subscribers only."),
    (re.compile(r"registered users|sign in to|log ?in to|logged[- ]in|use --cookies|account (is )?required", re.I),
     "This site wants you to be signed in to see this."),
    (re.compile(r"HTTP Error 401", re.I),
     "This page needs you to be signed in (401 Unauthorized)."),
]

_FRIENDLY = [
    (re.compile(r"unsupported url", re.I),
     "This site isn't supported, or the link doesn't point to a video."),
    (re.compile(r"no video formats found|there is no video in this|no video could be found", re.I),
     "There's no video at this link. It may be a photo or a text post."),
    (re.compile(r"unable to obtain file audio codec|does not contain any (audio|stream)|no audio (stream|track)", re.I),
     "This video has no sound, so there's no audio to save."),
    (re.compile(r"requested format is not available", re.I),
     "That quality isn't available for this video. Pick another one."),
    (re.compile(r"(drm|widevine|playready)", re.I), "This video is DRM-protected and can't be downloaded."),
    (re.compile(r"HTTP Error 404", re.I), "The page wasn't found (404). Check the link."),
    (re.compile(r"HTTP Error 403", re.I), "The site refused the request (403 Forbidden). Try again later."),
    (re.compile(r"HTTP Error 429|too many requests", re.I),
     "The site is rate-limiting this server. Try again in a few minutes."),
    (re.compile(r"(geo|country).*(restrict|block|not available)|not available in your country", re.I),
     "This video isn't available in the server's country."),
    (re.compile(r"timed out|timeout", re.I), "The site took too long to respond. Try again."),
    (re.compile(r"(this )?live (event|stream) will begin|premieres in", re.I),
     "This stream hasn't started yet."),
]


def _site_of(url: str | None) -> str:
    host = (urlsplit(url or "").hostname or "").lower().removeprefix("www.").removeprefix("m.")
    return host or "the site"


def describe_error(exc: BaseException | str, cookies: CookieSet | None = None,
                   url: str | None = None) -> tuple[str, str | None]:
    """A visitor-friendly message and an optional machine-readable code."""
    raw = _ANSI.sub("", str(exc)).strip()
    for pattern, message in _LOGIN:
        if pattern.search(raw):
            site = _site_of(url)
            if cookies is None:
                hint = f" Add cookies from a browser where you're signed in to {site} and try again."
            elif url and not cookies.covers(url):
                hint = (f" The cookies you added are for {', '.join(cookies.domains[:3])}, not {site}."
                        f" Export them again while signed in to {site}.")
            else:
                hint = (" The cookies you added didn't work. They may have expired (sign in again and"
                        " re-export them), or that account can't see this video.")
            return message + hint, LOGIN_REQUIRED
    for pattern, message in _FRIENDLY:
        if pattern.search(raw):
            return message, None
    first = raw.splitlines()[0] if raw else "Something went wrong."
    msg = _PREFIX.sub("", first).strip()
    msg = re.sub(r"[;,.]?\s*please report this issue on .*$", "", msg, flags=re.I)  # yt-dlp's bug-report boilerplate
    msg = re.sub(r"\s*(Use|See)\s+--?\S+.*$", "", msg)  # drop CLI-only advice
    msg = re.sub(r"\s+", " ", msg)
    if not msg:
        msg = "Something went wrong."
    if len(msg) > 280:
        msg = msg[:277] + "..."
    return msg[0].upper() + msg[1:], None


def friendly_error(exc: BaseException | str) -> str:
    return describe_error(exc)[0]


# ---------------------------------------------------------------------------
# Info cache (so "Download" doesn't re-extract what the page just looked up)
# ---------------------------------------------------------------------------


def cache_key(url: str, cookies: CookieSet | None) -> str:
    # Info fetched with someone's cookies must never be served to anyone else.
    return f"{url}#{cookies.fingerprint}" if cookies else url


class InfoCache:
    def __init__(self, ttl: float = 20 * 60, size: int = 256):
        self.ttl = ttl
        self.size = size
        self._data: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            ts, info = item
            if time.monotonic() - ts > self.ttl:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return info

    def drop(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def put(self, info: dict[str, Any], *keys: str) -> None:
        with self._lock:
            now = time.monotonic()
            for key in {k for k in keys if k}:
                self._data[key] = (now, info)
                self._data.move_to_end(key)
            while len(self._data) > self.size:
                self._data.popitem(last=False)
