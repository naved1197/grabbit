"""Menu bar (macOS) / system tray (Windows) icon."""

from __future__ import annotations

import io
import logging
from collections.abc import Callable

import pystray
from PIL import Image

from . import paths, system
from .controller import Desktop

log = logging.getLogger("grabbit.desktop")


def _mac_icon_class() -> type[pystray.Icon]:
    """pystray draws a blurry 22-pixel icon on Retina screens and can't tint it.

    This draws the 2x image at menu-bar size as a "template" image, so macOS
    colours it to match light/dark menu bars like other menu bar icons.
    """
    import AppKit
    import Foundation
    from pystray._darwin import Icon as DarwinIcon

    class MacIcon(DarwinIcon):
        def _assert_image(self):
            if self._icon_image is not None:
                return
            buf = io.BytesIO()
            self._icon.save(buf, "png")
            data = Foundation.NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
            image = AppKit.NSImage.alloc().initWithData_(data)
            points = 18.0
            image.setSize_(Foundation.NSMakeSize(points, points))
            image.setTemplate_(True)
            self._icon_image = image
            self._status_item.button().setImage_(image)

    return MacIcon


def _mac_on_reopen(callback: Callable[[], None]) -> None:
    """Opening Grabbit.app while it's already running shows the setup page."""
    import AppKit
    import Foundation

    class Delegate(Foundation.NSObject):
        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, flag):
            callback()
            return True

    delegate = Delegate.alloc().init()
    AppKit.NSApplication.sharedApplication().setDelegate_(delegate)
    _mac_on_reopen.delegate = delegate  # keep a reference


def call_on_main(fn: Callable[[], None]) -> None:
    """pystray's stop/update must happen on the UI thread on macOS."""
    if paths.IS_MAC:
        from PyObjCTools import AppHelper
        AppHelper.callAfter(fn)
    else:
        fn()


def run(desktop: Desktop, on_ready: Callable[[], None] | None = None) -> None:
    """Show the icon and block until the person quits (or the app restarts)."""
    image = Image.open(paths.tray_icon_file())
    image.load()

    def status_text(_item) -> str:
        if desktop.updater.pending_version:
            return "Grabbit is running · update ready"
        return "Grabbit is running" if desktop.extension_connected() else "Grabbit is running · set up the extension"

    def toggle_autostart(_icon, _item) -> None:
        desktop.set_autostart(not system.autostart_enabled())

    items = [
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open Grabbit", lambda: desktop.open_web(), default=True),
        pystray.MenuItem("Set Up Browser Extension…", lambda: desktop.open_setup()),
        pystray.MenuItem("Show Extension Folder", lambda: desktop.reveal_extension()),
    ]
    if system.autostart_supported():
        items.append(pystray.MenuItem("Start Grabbit at Login", toggle_autostart,
                                      checked=lambda _item: system.autostart_enabled()))
    items += [
        pystray.MenuItem(lambda _i: f"Restart to Use yt-dlp {desktop.updater.pending_version}",
                         lambda: desktop.restart(), visible=lambda _i: bool(desktop.updater.pending_version)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Grabbit", lambda: desktop.quit()),
    ]

    icon_cls = _mac_icon_class() if paths.IS_MAC else pystray.Icon
    icon = icon_cls("Grabbit", image, "Grabbit", pystray.Menu(*items))

    desktop.on_quit = lambda: call_on_main(icon.stop)
    desktop.on_change = lambda: call_on_main(icon.update_menu)
    if paths.IS_MAC:
        _mac_on_reopen(desktop.open_setup)

    def setup(ic: pystray.Icon) -> None:
        ic.visible = True
        if on_ready:
            on_ready()

    icon.run(setup=setup)
