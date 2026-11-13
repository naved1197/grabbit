"""Where things live, both in the packaged app and when running from source."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

FROZEN = bool(getattr(sys, "frozen", False))
IS_MAC = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"

# grabbit-desktop/ when running from source
_PROJECT = Path(__file__).resolve().parent.parent
_REPO = _PROJECT.parent

DEFAULT_PORT = 8000  # the browser extension's default server is http://localhost:8000
EXTENSION_FOLDER = "Grabbit Extension"


def port() -> int:
    try:
        return int(os.environ.get("GRABBIT_DESKTOP_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def resources() -> Path:
    """Folder holding bundled files (PyInstaller's unpack dir, or this project)."""
    return Path(getattr(sys, "_MEIPASS", _PROJECT))


def web_source() -> Path:
    """Folder that contains the server's `app` package."""
    return resources() if FROZEN else _REPO / "grabbit-web"


def extension_source() -> Path:
    return resources() / "extension" if FROZEN else _REPO / "grabbit-extension"


def setup_page_dir() -> Path:
    return resources() / "grabbit_desktop" / "setup" if FROZEN else Path(__file__).parent / "setup"


def tray_icon_file() -> Path:
    name = "tray-mac.png" if IS_MAC else "tray.png"
    return resources() / "icons" / name if FROZEN else _PROJECT / "packaging" / "icons" / name


def tools_bin() -> Path | None:
    """ffmpeg, ffprobe and deno shipped with the app (from packaging/fetch_tools.py)."""
    folder = resources() / "bin" if FROZEN else _PROJECT / "build" / "tools" / "bin"
    return folder if folder.is_dir() else None


def data_dir() -> Path:
    """Per-user data: the extension folder, staged downloads, yt-dlp updates, logs."""
    override = os.environ.get("GRABBIT_DESKTOP_HOME")
    if override:
        base = Path(override)
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support" / "Grabbit"
    elif IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Grabbit"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "grabbit"
    base.mkdir(parents=True, exist_ok=True)
    return base


def extension_dir() -> Path:
    # The extension folder inside the data folder: used on Windows, in test runs, and on
    # Macs where Grabbit 1.0 already put it. The packaged Mac app otherwise puts it next to
    # itself in Applications; see extension.choose_folder, which also keeps the choice.
    return data_dir() / EXTENSION_FOLDER


def user_desktop() -> Path:
    """The person's Desktop folder (a Desktop inside the test home for test runs)."""
    override = os.environ.get("GRABBIT_DESKTOP_HOME")
    return Path(override) / "Desktop" if override else Path.home() / "Desktop"


def downloads_dir() -> Path:
    return data_dir() / "downloads"


def updates_dir() -> Path:
    return data_dir() / "updates"


def log_file() -> Path:
    return data_dir() / "grabbit.log"


class State:
    """Small JSON file for things the app must remember between runs."""

    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "state.json"
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.load().get(key, default)

    def update(self, **values: Any) -> None:
        with self._lock:
            data = self.load()
            data.update(values)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), "utf-8")
            os.replace(tmp, self.path)
