"""Make MP4s play on Mac, iPhone and iPad.

Sites often serve their best quality as VP9 or AV1. Those fit in an MP4, but
QuickTime, Finder previews, Photos and iOS refuse to play them. Apple plays
H.264 and HEVC video with AAC/MP3/ALAC/AC-3 audio, so anything else is
re-encoded to H.264 (with the Mac's hardware encoder when there is one).

Also usable on its own, to fix files downloaded earlier. It writes
"<name> (plays on Mac).mp4" next to each file and leaves the original alone:

    uv run python -m app.compat ~/Downloads/*.mp4
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from .ytdl import tool_path

APPLE_AUDIO = {"aac", "mp3", "alac", "ac3", "eac3"}
# Bitrate ceilings for the hardware encoder by picture height (bits/s).
_BITRATES = ((480, 2_500_000), (720, 5_000_000), (1080, 8_000_000), (1440, 16_000_000), (2160, 35_000_000))


class ConvertError(Exception):
    pass


def apple_codec(vcodec: str | None) -> bool:
    """Whether yt-dlp's codec name (avc1.64001F, vp09.00…, av01…) is one Apple devices play."""
    return (vcodec or "").lower().startswith(("avc", "h264", "hvc1", "hev1", "h265", "hevc"))


def ffmpeg() -> str | None:
    return tool_path("ffmpeg", "GRABBIT_FFMPEG")


def ffprobe() -> str | None:
    return tool_path("ffprobe", "GRABBIT_FFPROBE")


def probe(path: Path) -> dict:
    out = subprocess.run(
        [ffprobe() or "ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def _video_stream(info: dict) -> dict | None:
    return next((s for s in info.get("streams") or []
                 if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)


def _video_ok(v: dict | None) -> bool:
    if v is None:
        return True
    # QuickTime only plays HEVC tagged "hvc1", not "hev1".
    return v.get("codec_name") == "h264" or (v.get("codec_name") == "hevc" and v.get("codec_tag_string") == "hvc1")


def needs_conversion(info: dict) -> bool:
    audio = [s.get("codec_name") for s in info.get("streams") or [] if s.get("codec_type") == "audio"]
    return not (_video_ok(_video_stream(info)) and all(a in APPLE_AUDIO for a in audio))


@functools.cache
def _hardware_h264() -> bool:
    try:
        out = subprocess.run([ffmpeg() or "ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    except OSError:
        return False
    return "h264_videotoolbox" in out


def _video_args(info: dict, hardware: bool) -> list[str]:
    v = _video_stream(info) or {}
    if v.get("codec_name") == "h264":
        return ["-c:v", "copy", "-tag:v", "avc1"]
    if v.get("codec_name") == "hevc":
        return ["-c:v", "copy", "-tag:v", "hvc1"]
    if not hardware:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-tag:v", "avc1"]
    # The short side sets the quality class, so portrait 1440x2560 counts as 1440p.
    short = min(int(v.get("width") or 1920), int(v.get("height") or 1080))
    ceiling = next((b for h, b in _BITRATES if short <= h), 60_000_000)
    source = int(v.get("bit_rate") or (info.get("format") or {}).get("bit_rate") or 0)
    # The hardware H.264 encoder needs ~2.5x VP9/AV1's bitrate for the same look.
    target = min(ceiling, max(int(source * 2.5), ceiling // 6)) if source else ceiling // 2
    return ["-c:v", "h264_videotoolbox", "-b:v", str(target), "-allow_sw", "1", "-pix_fmt", "yuv420p", "-tag:v", "avc1"]


def _audio_args(info: dict) -> list[str]:
    audio = [s for s in info.get("streams") or [] if s.get("codec_type") == "audio"]
    if all(s.get("codec_name") in APPLE_AUDIO for s in audio):
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "192k"]


def convert_for_apple(src: Path, dst: Path, *, on_progress: Callable[[float], None] | None = None,
                      cancel: threading.Event | None = None) -> bool:
    """Write an Apple-playable copy of ``src`` to ``dst``. False if ``src`` already plays."""
    info = probe(src)
    if not needs_conversion(info):
        return False
    duration = float((info.get("format") or {}).get("duration") or 0)
    attempts = [True, False] if _hardware_h264() else [False]  # the hardware encoder, then software
    for i, hardware in enumerate(attempts):
        cmd = [
            ffmpeg() or "ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error", "-i", str(src),
            "-map", "0:v:0?", "-map", "0:a?", "-map_metadata", "0", "-map_chapters", "0",
            *_video_args(info, hardware), *_audio_args(info),
            "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-f", "mp4", str(dst),
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.kill()
                proc.wait()
                dst.unlink(missing_ok=True)
                raise InterruptedError("canceled")
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.isdigit() and duration and on_progress:
                on_progress(min(int(value) / 1e6 / duration, 1.0))
        err = proc.stderr.read() if proc.stderr else ""
        if proc.wait() == 0:
            return True
        dst.unlink(missing_ok=True)
        if i == len(attempts) - 1:
            raise ConvertError(err.strip().splitlines()[-1] if err.strip() else "ffmpeg failed")
    raise AssertionError("unreachable")


def main(argv: list[str]) -> int:
    if not argv:
        print('Usage: uv run python -m app.compat FILE.mp4 ...\n'
              'Writes "<name> (plays on Mac).mp4" next to each file that Apple devices can\'t play.')
        return 2
    for name in argv:
        path = Path(name).expanduser()
        if path.stem.endswith("(plays on Mac)"):
            continue
        out = path.with_name(f"{path.stem} (plays on Mac).mp4")
        tmp = path.with_name(out.name + ".converting")
        try:
            changed = convert_for_apple(path, tmp, on_progress=lambda f: print(f"\r{path.name}: {f:4.0%}", end=""))
        except (ConvertError, subprocess.CalledProcessError, OSError, ValueError) as e:
            print(f"{path.name}: couldn't convert ({e})")
            continue
        if not changed:
            print(f"{path.name}: already plays on Apple devices")
            continue
        os.replace(tmp, out)
        print(f"\r{path.name}: saved {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
