"""Turn a link into exactly one thing to download: a video, or photos.

yt-dlp is built for videos. Social posts are messier: an Instagram post can be
a single photo, or a carousel that mixes photos and videos. With a signed-in
account yt-dlp hands those back with *no* video formats, and its download step
then fails with "No video formats found!". This module looks at the raw
extraction first and decides what the visitor actually meant:

* a post with several items -> the item they picked (``item``), or the video
  whose length matches what they were watching (``duration``), or the first video
* a photo, or a post with only photos -> the photo files
* anything else -> processed exactly like before
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yt_dlp
from yt_dlp.utils import YoutubeDLError

from .config import Settings
from .cookies import CookieSet
from .formats import best_thumbnail, summarize_playlist, summarize_video
from .ytdl import InfoCache, UserFacingError, YtdlLogger, base_options, cache_key, describe_error

# Warnings yt-dlp's extractors give for posts that hold photos instead of video.
_PHOTO_WARNING = re.compile(
    r"no video (in|could be found in|found in) this (post|tweet)|there is no video|"
    r"this post (has|contains) no video|no video formats found", re.I)
# Top-level fields that a post's items need for yt-dlp to process them alone.
_INHERIT = ("extractor", "extractor_key", "webpage_url", "webpage_url_basename",
            "webpage_url_domain", "original_url", "http_headers", "channel", "uploader",
            "uploader_id", "timestamp")
_MAX_URL_HOPS = 3
DURATION_TOLERANCE = 1.5  # seconds


@dataclass
class Photo:
    url: str
    width: int | None = None
    height: int | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Resolved:
    """One of ``video`` (processed info, ready for ``process_ie_result``) or ``photos``."""

    title: str
    video: dict[str, Any] | None = None
    photos: list[Photo] = field(default_factory=list)
    thumbnail: str | None = None
    uploader: str | None = None
    duration: float | None = None


# ---------------------------------------------------------------------------
# Raw extraction
# ---------------------------------------------------------------------------


def extract_raw(ydl: yt_dlp.YoutubeDL, url: str, logger: YtdlLogger) -> tuple[dict[str, Any], list[str]]:
    """Extract without processing, so posts without video don't fail.

    Returns the raw result and the warnings yt-dlp gave while extracting.
    Raises ``DownloadError`` like ``extract_info`` does.
    """
    seen = len(logger.warnings)
    ie_key = None
    for _ in range(_MAX_URL_HOPS):
        ydl.params["ignore_no_formats_error"] = True
        try:
            raw = ydl.extract_info(url, download=False, process=False, ie_key=ie_key)
        finally:
            ydl.params["ignore_no_formats_error"] = False
        if not raw:
            raise UserFacingError("No video found at that link.")
        if raw.get("_type") != "url":
            return raw, logger.warnings[seen:]
        url, ie_key = raw["url"], raw.get("ie_key")  # a plain redirect: follow it
    raise UserFacingError("That link redirects too many times.")


def is_post(raw: dict[str, Any]) -> bool:
    """A multi-item post (carousel, story, multi-video tweet), not a real playlist.

    Posts arrive with their items fully extracted. Playlists arrive as links to
    each video, or as a lazy generator (which must not be consumed here).
    """
    entries = raw.get("entries")
    return (
        raw.get("_type") in ("playlist", "multi_video")
        and isinstance(entries, list)
        and 0 < len(entries) <= 100
        and all(isinstance(e, dict) and e.get("_type", "video") == "video" for e in entries)
    )


def is_playlist(raw: dict[str, Any]) -> bool:
    return raw.get("_type") in ("playlist", "multi_video") or raw.get("entries") is not None


def has_video(info: dict[str, Any]) -> bool:
    return bool(info.get("formats") or info.get("url"))


def best_image(info: dict[str, Any]) -> Photo | None:
    """The largest image yt-dlp knows for a photo (its thumbnails *are* the photo)."""
    thumbs = [t for t in info.get("thumbnails") or [] if isinstance(t, dict) and t.get("url")]
    if not thumbs:
        url = info.get("thumbnail")
        return Photo(url) if url else None
    # Prefer the biggest reported size; later entries win ties (yt-dlp lists worst -> best).
    best = max(enumerate(thumbs), key=lambda it: ((it[1].get("width") or 0) * (it[1].get("height") or 0), it[0]))[1]
    return Photo(best["url"], best.get("width"), best.get("height"))


def _photo_or_error(info: dict[str, Any], warnings: list[str]) -> Photo:
    """The photo of a video-less result, or the error yt-dlp would have raised."""
    if info.get("_has_drm"):
        raise UserFacingError("This video is DRM-protected and can't be downloaded.")
    if info.get("live_status") == "is_upcoming":
        raise UserFacingError("This stream hasn't started yet.")
    # A warning that isn't about photos is the real reason (e.g. "Sign in to
    # confirm your age"); yt-dlp only turned it into a warning because we asked.
    reasons = [w for w in warnings if not _PHOTO_WARNING.search(w)]
    photo_hint = any(_PHOTO_WARNING.search(w) for w in warnings)
    if reasons and not photo_hint:
        raise _Reason(reasons[-1])
    photo = best_image(info)
    if not photo:
        raise UserFacingError("There's no video or photo at this link.")
    return photo


class _Reason(YoutubeDLError):
    """yt-dlp's own explanation for a missing video (callers describe it like any yt-dlp error)."""


# ---------------------------------------------------------------------------
# Picking what to download
# ---------------------------------------------------------------------------


def pick_entry(entries: list[dict[str, Any]], item: int | None = None,
               duration: float | None = None) -> int | None:
    """Index of the post item to download, or None for "all the photos".

    ``item`` is the 1-based slide the visitor was on; ``duration`` is the
    length of the video they were watching. A duration hint means they were
    looking at a video, so it never lands on a photo while videos exist.
    """
    videos = [i for i, e in enumerate(entries) if has_video(e)]
    matches: list[int] = []
    if duration and duration > 0:
        timed = [(abs((entries[i].get("duration") or 0) - duration), i) for i in videos if entries[i].get("duration")]
        matches = [i for d, i in sorted(timed) if d <= DURATION_TOLERANCE]

    if item is not None and 1 <= item <= len(entries):
        chosen = item - 1
        if not matches or chosen in matches:
            if not (duration and videos and chosen not in videos):
                return chosen
    if matches:
        return matches[0]
    if videos:
        return videos[0]
    if item is not None and 1 <= item <= len(entries):
        return item - 1  # a photo they picked
    return None


def _entry(raw: dict[str, Any], entry: dict[str, Any], index: int, total: int) -> dict[str, Any]:
    out = {k: raw[k] for k in _INHERIT if raw.get(k) is not None}
    out.update(entry)
    title = entry.get("title") or raw.get("title") or entry.get("id") or "Untitled"
    out["title"] = f"{title} ({index + 1} of {total})" if total > 1 else title
    out.setdefault("id", f"{raw.get('id') or 'post'}-{index + 1}")
    return out


def resolve(ydl: yt_dlp.YoutubeDL, url: str, logger: YtdlLogger, *, mode: str = "video",
            item: int | None = None, duration: float | None = None,
            raw: dict[str, Any] | None = None, warnings: list[str] | None = None) -> Resolved:
    """Decide what to download for ``url`` (or for an already extracted ``raw``).

    Raises ``UserFacingError`` for things that can't be downloaded here, and
    lets yt-dlp's ``YoutubeDLError`` through for the caller to describe.
    """
    if raw is None:
        raw, warnings = extract_raw(ydl, url, logger)
    warnings = warnings or []
    if is_post(raw):
        entries = raw["entries"]
        idx = pick_entry(entries, item, duration)
        if idx is None:  # only photos
            if mode == "audio":
                raise UserFacingError("This post only has photos, so there's no audio to save.")
            photos = [p for p in (best_image(e) for e in entries) if p]
            if not photos:
                raise UserFacingError("There's no video or photo in this post.")
            headers = dict(raw.get("http_headers") or {})
            for p in photos:
                p.headers = headers
            return Resolved(title=photo_title(raw.get("title") or "Post"), photos=photos,
                            thumbnail=photos[0].url, uploader=raw.get("uploader") or raw.get("channel"))
        entry = _entry(raw, entries[idx], idx, len(entries))
        if not has_video(entry):
            if mode == "audio":
                raise UserFacingError("That's a photo, so there's no audio to save.")
            photo = _photo_or_error(entry, [])
            photo.headers = dict(entry.get("http_headers") or {})
            return Resolved(title=photo_title(entry["title"]), photos=[photo], thumbnail=photo.url,
                            uploader=entry.get("uploader") or entry.get("channel"))
        return _video(ydl, entry, mode)

    if is_playlist(raw):
        raise UserFacingError("That link is a playlist. Open it on the home page to pick videos.")

    if raw.get("_type", "video") == "video" and not has_video(raw):
        photo = _photo_or_error(raw, warnings)
        if mode == "audio":
            raise UserFacingError("That's a photo, so there's no audio to save.")
        photo.headers = dict(raw.get("http_headers") or {})
        return Resolved(title=photo_title(raw.get("title") or raw.get("id") or "Photo"), photos=[photo],
                        thumbnail=photo.url, uploader=raw.get("uploader") or raw.get("channel"))

    return _video(ydl, raw, mode)


def photo_title(title: str) -> str:
    # Instagram calls every post "Video by <name>", photos included.
    return re.sub(r"^Video by ", "Photo by ", title)


def _video(ydl: yt_dlp.YoutubeDL, raw: dict[str, Any], mode: str = "video") -> Resolved:
    formats = raw.get("formats") or []
    if mode == "audio" and formats and all(f.get("acodec") == "none" for f in formats):
        raise UserFacingError("This video has no sound, so there's no audio to save.")
    info = ydl.process_ie_result(raw, download=False)
    if not info:
        raise UserFacingError("No video found at that link.")
    if is_playlist(info):
        raise UserFacingError("That link is a playlist. Open it on the home page to pick videos.")
    # Same cleanup yt-dlp does for --load-info-json, so the info can be handed
    # straight back to yt-dlp for the download.
    info = ydl.sanitize_info(info, remove_private_keys=True)
    return Resolved(
        title=info.get("title") or info.get("id") or "Untitled",
        video=info,
        thumbnail=best_thumbnail(info),
        uploader=info.get("uploader") or info.get("channel"),
        duration=info.get("duration"),
    )


# ---------------------------------------------------------------------------
# Summaries for the web page
# ---------------------------------------------------------------------------


def summarize_post(raw: dict[str, Any], url: str) -> dict[str, Any]:
    """A post (or a lone photo) shown like a playlist whose rows are its items."""
    page = raw.get("webpage_url") or url
    post = is_post(raw)
    entries_in = raw["entries"] if post else [raw]
    # Real per-item titles (an album's tracks) are worth showing; Instagram's
    # "Video by name" repeated on every slide is not.
    titles = [e.get("title") for e in entries_in]
    own_titles = post and all(titles) and len(set(titles)) == len(titles)
    entries, videos, photos = [], 0, 0
    for i, e in enumerate(entries_in, 1):
        video = has_video(e)
        videos += video
        photos += not video
        image = best_image(e)
        generic = f"{'Video' if video else 'Photo'} {i}" if len(entries_in) > 1 else ("Video" if video else "Photo")
        entries.append({
            "url": page,
            "item": i if post else None,
            "title": titles[i - 1] if own_titles else generic,
            "duration": e.get("duration") if video else None,
            "thumbnail": image.url if image else None,
            "uploader": None,
            "photo": not video,
        })
    host = (urlsplit(page).hostname or "").removeprefix("www.")
    return {
        "kind": "playlist",
        "post": True,
        "id": raw.get("id"),
        "title": (raw.get("title") if videos else photo_title(raw.get("title") or "")) or (f"Post on {host}" if host else "Post"),
        "uploader": raw.get("uploader") or raw.get("channel"),
        "thumbnail": next((e["thumbnail"] for e in entries if e["thumbnail"]), None),
        "webpage_url": page,
        "site": raw.get("extractor_key"),
        "count": len(entries),
        "videos": videos,
        "photos": photos,
        "entries": entries,
        "truncated": False,
    }


# ---------------------------------------------------------------------------
# Info lookup for the web page (with a short-lived cache so "Download" doesn't
# re-extract)
# ---------------------------------------------------------------------------


def post_key(url: str, cookies: CookieSet | None) -> str:
    return cache_key(url, cookies) + "#post"


def lookup(url: str, settings: Settings, cache: InfoCache, cookies: CookieSet | None = None) -> dict[str, Any]:
    """Return a page-friendly summary of a video, post or playlist link."""
    logger = YtdlLogger("info")
    with tempfile.TemporaryDirectory(prefix="grabbit-info-") as tmp:
        opts = base_options(settings, logger, Path(tmp), cookies)
        opts.update({"skip_download": True, "extract_flat": "in_playlist", "lazy_playlist": False,
                     "playlistend": settings.playlist_limit})
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                raw, warnings = extract_raw(ydl, url, logger)
                if is_post(raw) or (raw.get("_type", "video") == "video" and not has_video(raw)):
                    if not is_post(raw):
                        _photo_or_error(raw, warnings)  # raises unless it really is a photo
                    raw = ydl.sanitize_info(raw)
                    cache.put(raw, *(post_key(u, cookies) for u in (url, raw.get("webpage_url")) if u))
                    return summarize_post(raw, url)
                info = ydl.process_ie_result(raw, download=False)
                if not info:
                    raise UserFacingError("No video found at that link.")
                playlist = is_playlist(info)
                original_url = info.get("original_url")
                # Same cleanup yt-dlp does for --load-info-json, so the cached
                # info can be handed straight back to yt-dlp for the download.
                # (It would also drop playlist entries, so skip it for playlists.)
                info = ydl.sanitize_info(info, remove_private_keys=not playlist)
        except YoutubeDLError as e:
            raise UserFacingError(*describe_error(e, cookies, url)) from None

    if playlist:
        return summarize_playlist(info, settings.playlist_limit)

    cache.put(info, *(cache_key(u, cookies) for u in (url, info.get("webpage_url"), original_url) if u))
    summary = summarize_video(info)
    if info.get("is_live"):
        summary["warning"] = "Live streams can't be downloaded here."
    elif settings.max_duration_min and (info.get("duration") or 0) > settings.max_duration_min * 60:
        summary["warning"] = f"This server only downloads videos up to {settings.max_duration_min} minutes long."
    return summary
