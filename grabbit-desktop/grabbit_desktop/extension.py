"""Keeps the browser extension folder (the one people load into Chrome) up to date."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from . import paths, system

log = logging.getLogger("grabbit.desktop")

APPLICATIONS = Path("/Applications")

DEFAULT_SERVER = b"http://localhost:8000"
# Files that contain the extension's default server address.
_HAS_SERVER_DEFAULT = {"background.js", "popup.js", "popup.html"}


def beside_app() -> Path | None:
    """/Applications/Grabbit Extension, right next to Grabbit.app (packaged Mac app only).

    Every Mac file picker lists Applications in its sidebar, so "Load unpacked" reaches
    the folder in one click. The data folder is hidden inside ~/Library.
    """
    if not paths.IS_MAC or os.environ.get("GRABBIT_DESKTOP_HOME"):  # test runs keep it in their own folder
        return None
    bundle = system.app_bundle()
    if not bundle or bundle.parent != APPLICATIONS or not os.access(APPLICATIONS, os.W_OK):
        return None
    return APPLICATIONS / paths.EXTENSION_FOLDER


def choose_folder(state: paths.State) -> Path:
    """Where the extension folder lives. Picked once, then kept for good:
    browsers remember an unpacked extension by its folder's path."""
    saved = state.get("extension_dir")
    if isinstance(saved, str) and saved:
        return Path(saved)
    in_data = paths.extension_dir()
    # Grabbit 1.0 always used the data folder. If a browser already loaded it from there, stay.
    folder = in_data if state.get("extension") else (beside_app() or in_data)
    state.update(extension_dir=str(folder))
    return folder


def shortcuts_supported() -> bool:
    # When loading an unpacked extension, browsers on macOS follow a link to the real folder
    # (Chromium's UnpackedInstaller runs the path through realpath) and remember that one, so
    # Grabbit keeps updating it and the link can go once it's loaded. On Windows they would
    # remember the link itself, and deleting it would break the extension.
    return paths.IS_MAC


def is_shortcut_to(link: Path, target: Path) -> bool:
    try:
        return link.is_symlink() and link.resolve() == target.resolve()
    except OSError:
        return False


def make_shortcut(target: Path, folder: Path) -> Path:
    """Put a link to `target` in `folder` (the Desktop) and return it.

    Never replaces a real file or folder: picks "Grabbit Extension 2" and so on instead.
    """
    folder.mkdir(parents=True, exist_ok=True)
    for n in range(1, 100):
        link = folder / (target.name if n == 1 else f"{target.name} {n}")
        if is_shortcut_to(link, target):
            return link
        if link.is_symlink() and not link.exists():
            link.unlink()  # a broken link, e.g. to an extension folder that has moved
        if not os.path.lexists(link):
            link.symlink_to(target, target_is_directory=True)
            return link
    raise FileExistsError(folder / target.name)


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
