"""Server settings, read from environment variables (all optional)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _str(name: str) -> str | None:
    raw = os.environ.get(name, "").strip()
    return raw or None


@dataclass(frozen=True)
class Settings:
    # Where finished files wait until the visitor saves them.
    download_dir: Path
    # How many downloads run at the same time (server-wide).
    max_concurrent: int
    # How many unfinished downloads one visitor (IP address) may have.
    max_jobs_per_ip: int
    # Largest file the server will fetch, in MB (0 = no limit).
    max_filesize_mb: int
    # Longest video the server will fetch, in minutes (0 = no limit).
    max_duration_min: int
    # Minutes a finished file is kept before it is deleted.
    file_ttl_min: int
    # Most playlist items shown / downloadable from one playlist.
    playlist_limit: int
    # API requests per minute per IP for "look up" and "download".
    rate_limit_per_min: int
    # Netscape cookies.txt passed to yt-dlp for every visitor without their own.
    cookies_file: str | None
    # Read cookies straight from a browser on the server, e.g. "chrome" or
    # "firefox:Profile 1". Personal/self-hosted use only: every visitor's
    # downloads would use that browser's signed-in accounts.
    cookies_from_browser: str | None
    # Let visitors add their own cookies (kept in their browser, per request).
    allow_user_cookies: bool
    # Proxy for all yt-dlp traffic, e.g. socks5://user:pass@host:1080
    proxy: str | None
    # Allow links that point at private/local network addresses.
    allow_private_urls: bool
    # Use X-Forwarded-For for the visitor's IP (set when behind a reverse proxy).
    trust_proxy_headers: bool
    # Re-encode MP4s that Apple devices can't play (VP9/AV1/Opus) to H.264/AAC.
    # The Android app turns this off: phones play those, and software encoding is slow there.
    apple_compat: bool = True

    @property
    def max_filesize_bytes(self) -> int | None:
        return self.max_filesize_mb * 1024 * 1024 if self.max_filesize_mb > 0 else None


def load_settings() -> Settings:
    download_dir = Path(os.environ.get("GRABBIT_DOWNLOAD_DIR") or Path.cwd() / "data" / "downloads")
    return Settings(
        download_dir=download_dir.resolve(),
        max_concurrent=max(1, _int("GRABBIT_MAX_CONCURRENT", 3)),
        max_jobs_per_ip=max(1, _int("GRABBIT_MAX_JOBS_PER_IP", 20)),
        max_filesize_mb=max(0, _int("GRABBIT_MAX_FILESIZE_MB", 4096)),
        max_duration_min=max(0, _int("GRABBIT_MAX_DURATION_MIN", 240)),
        file_ttl_min=max(1, _int("GRABBIT_FILE_TTL_MIN", 60)),
        playlist_limit=max(1, _int("GRABBIT_PLAYLIST_LIMIT", 50)),
        rate_limit_per_min=max(1, _int("GRABBIT_RATE_LIMIT_PER_MIN", 30)),
        cookies_file=_str("GRABBIT_COOKIES_FILE"),
        cookies_from_browser=_str("GRABBIT_COOKIES_FROM_BROWSER"),
        allow_user_cookies=_bool("GRABBIT_ALLOW_USER_COOKIES", True),
        proxy=_str("GRABBIT_PROXY"),
        allow_private_urls=_bool("GRABBIT_ALLOW_PRIVATE_URLS", False),
        trust_proxy_headers=_bool("GRABBIT_TRUST_PROXY_HEADERS", False),
        apple_compat=_bool("GRABBIT_APPLE_COMPAT", True),
    )
