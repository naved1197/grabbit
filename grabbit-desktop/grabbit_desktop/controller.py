"""What the setup page and the tray menu can ask the app to do."""

from __future__ import annotations

import logging
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import VERSION, extension, paths, system
from .updater import Updater

log = logging.getLogger("grabbit.desktop")


class Desktop:
    def __init__(self, port: int, state: paths.State, updater: Updater, extension_dir: Path | None = None):
        self.port = port
        self.state = state
        self.updater = updater
        self.extension_dir = extension_dir or extension.choose_folder(state)
        self.token = secrets.token_urlsafe(24)
        self.restart_requested = False
        self.on_quit: Callable[[], None] | None = None  # set by the tray icon
        self.on_change: Callable[[], None] | None = None  # refresh the tray menu
        self._browsers: tuple[float, list[system.Browser]] = (0.0, [])
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self.port}"

    @property
    def setup_url(self) -> str:
        return f"{self.base_url}/desktop/"

    # ------------------------------------------------------------------ state

    def browsers(self) -> list[system.Browser]:
        with self._lock:
            at, found = self._browsers
            if time.monotonic() - at > 30:
                found = system.find_browsers()
                self._browsers = (time.monotonic(), found)
            return found

    def extension_connected(self) -> bool:
        return bool(self.state.get("extension"))

    def desktop_shortcut(self) -> Path | None:
        """The link "Put it on my Desktop" made, if it's still there.

        Only looks at the Desktop after that button was used: macOS asks the person before
        an app may touch their Desktop, and a status check shouldn't be what triggers that.
        """
        saved = self.state.get("desktop_shortcut")
        link = Path(saved) if isinstance(saved, str) and saved else None
        return link if link and extension.is_shortcut_to(link, self.extension_dir) else None

    def status(self) -> dict[str, Any]:
        ext = self.state.get("extension") or {}
        return {
            "app": "grabbit-desktop",
            "version": VERSION,
            "executable": sys.executable,
            "pid": os.getpid(),
            "platform": "mac" if paths.IS_MAC else "windows" if paths.IS_WINDOWS else "other",
            "extension": {"connected": bool(ext), "version": ext.get("version"), "at": ext.get("at")},
            "extension_dir": str(self.extension_dir),
            # "applications": Mac file pickers list it in their sidebar; "hidden": the path must be typed
            "extension_place": "applications" if self.extension_dir.parent == extension.APPLICATIONS else "hidden",
            "desktop_shortcut": {"supported": extension.shortcuts_supported(),
                                 "path": str(link) if (link := self.desktop_shortcut()) else None},
            "browsers": [b.public() for b in self.browsers()],
            "autostart": {"supported": system.autostart_supported(), "enabled": system.autostart_enabled()},
            "ytdlp": {"version": self.updater.active_version, "update_ready": self.updater.pending_version},
        }

    # ------------------------------------------------------------------ actions

    def extension_hello(self, version: str) -> None:
        first = not self.extension_connected()
        self.state.update(extension={"version": version, "at": int(time.time())})
        if first:
            log.info("browser extension connected (version %s)", version or "?")
        self.remove_desktop_shortcut()  # the browser remembers the real folder, not the link
        self._changed()

    def put_on_desktop(self) -> Path:
        """Put a link to the extension folder on the Desktop, where every file picker reaches it.

        Raises OSError, PermissionError if macOS (or the person) said no. The first time,
        this waits while macOS asks whether Grabbit may use the Desktop.
        """
        link = self.desktop_shortcut() or extension.make_shortcut(self.extension_dir, paths.user_desktop())
        self.state.update(desktop_shortcut=str(link))
        log.info("put a link to the extension folder on the Desktop: %s", link)
        return link

    def remove_desktop_shortcut(self) -> None:
        link = self.desktop_shortcut()
        if link:
            try:
                link.unlink()
                log.info("removed the extension link from the Desktop")
            except OSError:
                log.warning("couldn't remove %s", link, exc_info=True)
                return
        if self.state.get("desktop_shortcut"):
            self.state.update(desktop_shortcut=None)

    def open_browser(self, browser_id: str) -> bool:
        browser = next((b for b in self.browsers() if b.id == browser_id), None)
        if not browser:
            return False
        system.open_browser(browser)
        return True

    def reveal_extension(self) -> None:
        system.reveal(self.extension_dir)

    def set_autostart(self, enabled: bool) -> bool:
        result = system.set_autostart(enabled)
        self._changed()
        return result

    def open_setup(self) -> None:
        system.open_url(self.setup_url)

    def open_web(self) -> None:
        system.open_url(self.base_url + "/")

    def quit(self) -> None:
        if self.on_quit:
            self.on_quit()

    def restart(self) -> None:
        self.restart_requested = True
        self.quit()

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                log.debug("tray refresh failed", exc_info=True)
