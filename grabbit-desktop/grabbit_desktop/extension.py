"""Keeps the browser extension folder (the one people load into Chrome) up to date."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("grabbit.desktop")

DEFAULT_SERVER = b"http://localhost:8000"
# Files that contain the extension's default server address.
_HAS_SERVER_DEFAULT = {"background.js", "popup.js", "popup.html"}


def _skip(rel: Path) -> bool:
    return any(part.startswith(".") or part == "__pycache__" for part in rel.parts)


def bundled_files(src: Path, port: int = 8000) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if not path.is_file() or _skip(rel):
            continue
        data = path.read_bytes()
        if port != 8000 and rel.as_posix() in _HAS_SERVER_DEFAULT:
            data = data.replace(DEFAULT_SERVER, f"http://localhost:{port}".encode())
        files[rel.as_posix()] = data
    return files


def sync(src: Path, dst: Path, port: int = 8000) -> bool:
    """Make `dst` an exact copy of the bundled extension. Returns True if anything changed.

    Files are replaced in place, never by deleting the folder, because the browser
    remembers the extension by this folder's path. The browser picks the new files
    up the next time it starts (or when the extension is reloaded).
    """
    if not (src / "manifest.json").is_file():
        log.error("bundled extension missing at %s", src)
        return False
    wanted = bundled_files(src, port)
    dst.mkdir(parents=True, exist_ok=True)
    changed = False
    for rel, data in wanted.items():
        target = dst / rel
        try:
            if target.is_file() and target.read_bytes() == data:
                continue
        except OSError:
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".grabbit-tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        changed = True
    for path in sorted(dst.rglob("*"), reverse=True):
        rel = path.relative_to(dst)
        if rel.as_posix() in wanted or _skip(rel):
            continue
        try:
            if path.is_file():
                path.unlink()
                changed = True
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            log.warning("could not remove old extension file %s", path)
    if changed:
        log.info("extension folder updated: %s", dst)
    return changed
