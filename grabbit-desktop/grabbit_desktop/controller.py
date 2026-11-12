"""What the setup page and the tray menu can ask the app to do."""

from __future__ import annotations

import logging
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from . import VERSION, paths, system
from .updater import Updater

log = logging.getLogger("grabbit.desktop")


class Desktop:
    def __init__(self, port: int, state: paths.State, updater: Updater):
        self.port = port
        self.state = state
        self.updater = updater
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

    def status(self) -> dict[str, Any]:
        ext = self.state.get("extension") or {}
        return {
            "app": "grabbit-desktop",
            "version": VERSION,
            "executable": sys.executable,
            "pid": os.getpid(),
            "platform": "mac" if paths.IS_MAC else "windows" if paths.IS_WINDOWS else "other",
            "extension": {"connected": bool(ext), "version": ext.get("version"), "at": ext.get("at")},
            "extension_dir": str(paths.extension_dir()),
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
        self._changed()

    def open_browser(self, browser_id: str) -> bool:
        browser = next((b for b in self.browsers() if b.id == browser_id), None)
        if not browser:
            return False
        system.open_browser(browser)
        return True

    def reveal_extension(self) -> None:
        system.reveal(paths.extension_dir())

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
