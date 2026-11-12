"""Turn the visitor's choices into yt-dlp options, and yt-dlp info into a tidy summary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Mode = Literal["video", "audio"]
VIDEO_CONTAINERS = ("mp4", "webm", "mkv")
AUDIO_FORMATS = ("mp3", "m4a", "opus", "best")
QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360", "240", "144")


class DownloadChoice(BaseModel):
    mode: Mode = "video"
    quality: str = Field("best", description="best or a max height such as 1080")
    container: str = "mp4"
    audio_format: str = "mp3"

    @field_validator("quality")
    @classmethod
    def _quality(cls, v: str) -> str:
        v = str(v).lower().removesuffix("p")
        if v not in QUALITIES:
            raise ValueError(f"quality must be one of {', '.join(QUALITIES)}")
        return v

    @field_validator("container")
    @classmethod
    def _container(cls, v: str) -> str:
        v = v.lower()
        if v not in VIDEO_CONTAINERS:
            raise ValueError(f"container must be one of {', '.join(VIDEO_CONTAINERS)}")
        return v

    @field_validator("audio_format")
    @classmethod
    def _audio(cls, v: str) -> str:
        v = v.lower()
        if v not in AUDIO_FORMATS:
            raise ValueError(f"audio_format must be one of {', '.join(AUDIO_FORMATS)}")
        return v


def format_options(choice: DownloadChoice, has_ffmpeg: bool = True) -> dict[str, Any]:
    """yt-dlp options for format selection and post-processing."""
    if not has_ffmpeg:
        # Without ffmpeg nothing can be merged or converted: pick single files.
        if choice.mode == "audio":
            return {"format": "ba[ext=m4a]/ba/b"}
        res = "res" if choice.quality == "best" else f"res:{choice.quality}"
        return {"format": "b", "format_sort": [res, "ext:mp4:m4a"]}

    if choice.mode == "audio":
        pps: list[dict[str, Any]] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": choice.audio_format,
            "preferredquality": "0" if choice.audio_format == "mp3" else "5",
            "nopostoverwrites": False,
        }]
        pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})
        pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
        sort = ["acodec:aac"] if choice.audio_format == "m4a" else []
        if choice.audio_format == "opus":
            sort = ["acodec:opus"]
        return {
            "format": "ba/b",
            "format_sort": sort,
            "writethumbnail": True,
            "postprocessors": pps,
        }

    sort: list[str] = ["res" if choice.quality == "best" else f"res:{choice.quality}"]
    if choice.container == "mp4":
        # Same resolution: prefer MP4/H.264/AAC, which plays everywhere
        # (older phones and Safari can't decode AV1/VP9 well).
        sort += ["ext:mp4:m4a", "vcodec:h264", "acodec:aac"]
        merge = "mp4"
        remux = "mp4"
    elif choice.container == "webm":
        sort += ["ext:webm:webm"]
        merge = "webm/mkv"
        remux = "webm/mkv"
    else:
        merge = "mkv"
        remux = "mkv"
    return {
        "format": "bv*+ba/b",
        "format_sort": sort,
        "merge_output_format": merge,
        "postprocessors": [
            {"key": "FFmpegVideoRemuxer", "preferedformat": remux},
            {"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True},
        ],
    }


# ---------------------------------------------------------------------------
# Info summary for the page
# ---------------------------------------------------------------------------

_HEIGHT_LABELS = {4320: "8K", 2160: "4K", 1440: "2K"}


def _size(fmt: dict[str, Any], duration: float | None) -> int | None:
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if size:
        return int(size)
    tbr = fmt.get("tbr")
    if tbr and duration:
        return int(tbr * 1000 / 8 * duration)
    return None


def _has_video(f: dict[str, Any]) -> bool:
    vcodec = f.get("vcodec")
    if vcodec == "none":
        return False
    return vcodec is not None or bool(f.get("height"))


def _has_audio(f: dict[str, Any]) -> bool:
    """Known to carry audio (unknown codecs don't count)."""
    return f.get("acodec") not in (None, "none")


def best_thumbnail(info: dict[str, Any]) -> str | None:
    # yt-dlp's own pick comes from the site's API, so it exists. The list also
    # holds guessed URLs (e.g. YouTube's maxresdefault) that may 404.
    if info.get("thumbnail"):
        return info["thumbnail"]
    thumbs = [t for t in info.get("thumbnails") or [] if t.get("url")]
    pool = [t for t in thumbs if t.get("width")] or thumbs  # sized = reported by the site
    if not pool:
        return None
    jpgs = [t for t in pool if ".webp" not in t["url"]]  # yt-dlp orders worst -> best
    return (jpgs or pool)[-1]["url"]


def summarize_video(info: dict[str, Any]) -> dict[str, Any]:
    duration = info.get("duration")
    formats = [f for f in info.get("formats") or [] if f.get("url") or f.get("manifest_url")]
    formats = [f for f in formats if not str(f.get("format_id", "")).startswith("sb")]  # storyboards

    audio_only = [f for f in formats if _has_audio(f) and not _has_video(f)]
    best_audio = max(audio_only, key=lambda f: f.get("abr") or f.get("tbr") or 0, default=None)
    audio_size = _size(best_audio, duration) if best_audio else None

    by_height: dict[int, dict[str, Any]] = {}
    for f in formats:
        h = f.get("height")
        if not h or not _has_video(f):
            continue
        size = _size(f, duration)
        if not _has_audio(f) and size and audio_size:
            size += audio_size
        entry = by_height.setdefault(h, {"height": h, "fps": f.get("fps"), "size": size})
        # keep the largest estimate at this height (that's what "best" will pick)
        if size and (entry["size"] is None or size > entry["size"]):
            entry["size"] = size
        if (f.get("fps") or 0) > (entry["fps"] or 0):
            entry["fps"] = f.get("fps")

    qualities = []
    for h in sorted(by_height, reverse=True):
        e = by_height[h]
        label = f"{h}p"
        if e["fps"] and e["fps"] > 30:
            label += str(round(e["fps"]))
        qualities.append({
            "value": str(_snap_height(h)),
            "height": h,
            "label": label,
            "badge": _HEIGHT_LABELS.get(_snap_height(h)) or ("HD" if h >= 720 else None),
            "size": e["size"],
        })
    # One entry per selectable quality value (e.g. 1080 and 1088 both map to 1080).
    seen: set[str] = set()
    qualities = [q for q in qualities if not (q["value"] in seen or seen.add(q["value"]))]

    return {
        "kind": "video",
        "id": info.get("id"),
        "title": info.get("title") or info.get("id") or "Untitled",
        "uploader": info.get("uploader") or info.get("channel") or info.get("uploader_id"),
        "duration": duration,
        "thumbnail": best_thumbnail(info),
        "webpage_url": info.get("webpage_url") or info.get("original_url"),
        "site": info.get("extractor_key") or info.get("extractor"),
        "is_live": bool(info.get("is_live")),
        "qualities": qualities,
        # Unknown codecs (common for direct file links) count as "maybe": only an
        # explicit "none" on every format rules video or audio out.
        "has_video": (any(f.get("vcodec") != "none" for f in formats) or not formats)
                     and info.get("ext") not in ("mp3", "m4a", "opus", "ogg", "wav", "flac", "aac"),
        "has_audio": any(f.get("acodec") != "none" for f in formats) or not formats,
        "audio_size": audio_size,
    }


def _snap_height(h: int) -> int:
    """Map odd heights (e.g. 1088, 1072, 718) onto the standard quality buckets."""
    for q in (4320, 2160, 1440, 1080, 720, 480, 360, 240, 144):
        if h >= q * 0.93:
            return q
    return 144


def summarize_playlist(info: dict[str, Any], limit: int) -> dict[str, Any]:
    entries = []
    for e in info.get("entries") or []:
        if not e:
            continue
        url = e.get("url") or e.get("webpage_url")
        if not url:
            continue
        if not url.startswith("http") and e.get("ie_key") == "Youtube":
            url = f"https://www.youtube.com/watch?v={url}"
        thumbs = e.get("thumbnails") or []
        entries.append({
            "url": url,
            "title": e.get("title") or url,
            "duration": e.get("duration"),
            "thumbnail": thumbs[-1]["url"] if thumbs and thumbs[-1].get("url") else e.get("thumbnail"),
            "uploader": e.get("uploader") or e.get("channel"),
        })
        if len(entries) >= limit:
            break
    count = info.get("playlist_count") or len(info.get("entries") or [])
    return {
        "kind": "playlist",
        "id": info.get("id"),
        "title": info.get("title") or "Playlist",
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail": best_thumbnail(info) or (entries[0]["thumbnail"] if entries else None),
        "webpage_url": info.get("webpage_url"),
        "site": info.get("extractor_key"),
        "count": count,
        "entries": entries,
        "truncated": count > len(entries),
    }
