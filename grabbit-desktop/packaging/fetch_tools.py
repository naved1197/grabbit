"""Puts ffmpeg, ffprobe and deno for this platform in build/tools/bin (bundled into the app).

    uv run python packaging/fetch_tools.py

ffmpeg builds are pinned and checked against SHA-256 sums recorded here:
  macOS:   Martin Riedl's static builds (signed with his Apple Developer ID), https://ffmpeg.martin-riedl.de
  Windows: gyan.dev "essentials" release build (linked from ffmpeg.org), mirrored on GitHub
To update ffmpeg, change the URLs and hashes below.
deno is copied from the `deno` package that yt-dlp[deno] installs into this project's venv.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import ssl
import stat
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "build" / "tools" / "bin"
CACHE = ROOT / "build" / "tools" / "cache"

RIEDL = "https://ffmpeg.martin-riedl.de/download/macos"
FFMPEG = {
    ("darwin", "arm64"): [
        (f"{RIEDL}/arm64/1789931890_9.0.2/ffmpeg.zip",
         "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924", ["ffmpeg"]),
        (f"{RIEDL}/arm64/1789931890_9.0.2/ffprobe.zip",
         "fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6", ["ffprobe"]),
    ],
    ("darwin", "x86_64"): [
        (f"{RIEDL}/amd64/1789931006_9.0.2/ffmpeg.zip",
         "7c6b4125b191cbf773832dc51f424cf2b6bb7da43007d1e066f95909e47cacd4", ["ffmpeg"]),
        (f"{RIEDL}/amd64/1789931006_9.0.2/ffprobe.zip",
         "2322438ed2f6319a691291b247d09c69dcaa3a982460d1f269a7e1af335cfdfd", ["ffprobe"]),
    ],
    ("win32", "amd64"): [
        ("https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip",
         "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba", ["ffmpeg.exe", "ffprobe.exe"]),
    ],
}


def _download(url: str, sha256: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"{sha256[:16]}-{url.rsplit('/', 1)[-1]}"
    if not dest.is_file() or hashlib.sha256(dest.read_bytes()).hexdigest() != sha256:
        print(f"downloading {url}")
        import certifi  # python.org Python on macOS has no CA store of its own

        req = urllib.request.Request(url, headers={"User-Agent": "grabbit-desktop-build"})
        ctx = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(req, timeout=600, context=ctx) as res, open(dest.with_suffix(".part"), "wb") as f:
            shutil.copyfileobj(res, f)
        dest.with_suffix(".part").replace(dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    if digest != sha256:
        dest.unlink()
        sys.exit(f"checksum mismatch for {url}\n  expected {sha256}\n  got      {digest}")
    return dest


def _extract(archive: Path, names: list[str]) -> None:
    with zipfile.ZipFile(archive) as z:
        for name in names:
            member = next((m for m in z.namelist() if m.rsplit("/", 1)[-1] == name), None)
            if not member:
                sys.exit(f"{name} not found in {archive.name}")
            out = BIN / name
            with z.open(member) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            print(f"  {out.relative_to(ROOT)}")


def main() -> None:
    key = (sys.platform, platform.machine().lower())
    if key not in FFMPEG:
        sys.exit(f"no ffmpeg build listed for {key}")
    BIN.mkdir(parents=True, exist_ok=True)
    for url, sha256, names in FFMPEG[key]:
        _extract(_download(url, sha256), names)

    from deno import find_deno_bin  # installed by yt-dlp[deno]

    deno = Path(find_deno_bin())
    shutil.copy2(deno, BIN / deno.name)
    print(f"  {(BIN / deno.name).relative_to(ROOT)}  (from {deno})")

    if os.name != "nt":
        for tool in ("ffmpeg", "ffprobe", "deno"):
            os.access(BIN / tool, os.X_OK) or sys.exit(f"{tool} is not executable")


if __name__ == "__main__":
    main()
