"""Operating-system glue: dialogs, opening things, finding browsers, start at login."""

from __future__ import annotations

import logging
import os
import plistlib
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from .paths import FROZEN, IS_MAC, IS_WINDOWS

log = logging.getLogger("grabbit.desktop")

LAUNCH_AGENT_LABEL = "com.grabbit.desktop"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Grabbit"


# --------------------------------------------------------------------------- dialogs


def _osa(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _quiet() -> bool:
    return bool(os.environ.get("GRABBIT_DESKTOP_NO_DIALOGS"))  # automated test runs


def alert(message: str, title: str = "Grabbit") -> None:
    """Show a blocking error message (the app has no windows of its own)."""
    log.warning("alert: %s", message)
    if _quiet():
        return
    try:
        if IS_MAC:
            subprocess.run(
                ["osascript", "-e", "activate", "-e",
                 f"display alert {_osa(title)} message {_osa(message)} as critical"],
                check=False, timeout=3600,
            )
        elif IS_WINDOWS:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10 | 0x40000)  # error icon, topmost
        else:
            print(f"{title}: {message}", file=sys.stderr)
    except Exception:
        log.exception("could not show the alert")


def ask(message: str, ok: str, cancel: str, title: str = "Grabbit") -> bool:
    """Yes/no question. Returns True for `ok`."""
    if _quiet():
        return False
    try:
        if IS_MAC:
            proc = subprocess.run(
                ["osascript", "-e", "activate", "-e",
                 f"display dialog {_osa(message)} with title {_osa(title)} "
                 f"buttons {{{_osa(cancel)}, {_osa(ok)}}} default button {_osa(ok)} with icon note"],
                check=False, timeout=3600, capture_output=True,
            )
            return proc.returncode == 0
        if IS_WINDOWS:
            import ctypes
            return ctypes.windll.user32.MessageBoxW(None, message, title, 0x01 | 0x40 | 0x40000) == 1  # OK/Cancel
    except Exception:
        log.exception("could not show the question")
    return False


# --------------------------------------------------------------------------- opening things


def open_url(url: str) -> None:
    try:
        if IS_MAC:
            subprocess.Popen(["/usr/bin/open", url])
        else:
            webbrowser.open(url)
    except Exception:
        log.exception("could not open %s", url)


def reveal(path: Path) -> None:
    """Show a file or folder in Finder / File Explorer, selected."""
    try:
        if IS_MAC:
            subprocess.Popen(["/usr/bin/open", "-R", str(path)])
        elif IS_WINDOWS:
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception:
        log.exception("could not reveal %s", path)


# --------------------------------------------------------------------------- browsers


@dataclass(frozen=True)
class Browser:
    id: str
    name: str
    path: str           # .app bundle on macOS, .exe on Windows
    extensions_url: str  # e.g. chrome://extensions
    dev_mode_hint: str   # where its Developer mode switch is

    def public(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "extensions_url": self.extensions_url,
                "dev_mode_hint": self.dev_mode_hint}


_TOP_RIGHT = "top right of the page"
# id, name, macOS app, Windows exe, extensions page, Developer mode location,
# macOS bundle id, Windows ProgId (for spotting the default browser)
_KNOWN = [
    ("chrome", "Google Chrome", "Google Chrome.app", r"Google\Chrome\Application\chrome.exe",
     "chrome://extensions", _TOP_RIGHT, "com.google.chrome", "ChromeHTML"),
    ("edge", "Microsoft Edge", "Microsoft Edge.app", r"Microsoft\Edge\Application\msedge.exe",
     "edge://extensions", "left side of the page", "com.microsoft.edgemac", "MSEdgeHTM"),
    ("brave", "Brave", "Brave Browser.app", r"BraveSoftware\Brave-Browser\Application\brave.exe",
     "brave://extensions", _TOP_RIGHT, "com.brave.browser", "BraveHTML"),
    ("vivaldi", "Vivaldi", "Vivaldi.app", r"Vivaldi\Application\vivaldi.exe",
     "vivaldi://extensions", _TOP_RIGHT, "com.vivaldi.vivaldi", "VivaldiHTM"),
    ("opera", "Opera", "Opera.app", r"Programs\Opera\opera.exe",
     "opera://extensions", _TOP_RIGHT, "com.operasoftware.opera", "OperaStable"),
    ("arc", "Arc", "Arc.app", "", "arc://extensions", _TOP_RIGHT, "company.thebrowser.browser", ""),
]


def _mac_app(name: str) -> str | None:
    for base in (Path("/Applications"), Path.home() / "Applications"):
        if (base / name).is_dir():
            return str(base / name)
    return None


def _win_exe(rel: str) -> str | None:
    if not rel:
        return None
    roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "ProgramW6432")]
    for root in filter(None, roots):
        p = Path(root) / rel
        if p.is_file():
            return str(p)
    try:  # "App Paths" registry entries, used by installers that pick a custom folder
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                value = winreg.QueryValue(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{Path(rel).name}")
            except OSError:
                continue
            if value and Path(value.strip('"')).is_file():
                return value.strip('"')
    except ImportError:
        pass
    return None


def _default_browser_key() -> str | None:
    """Bundle id (macOS) or ProgId (Windows) of the default web browser."""
    try:
        if IS_MAC:
            plist = Path.home() / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
            with plist.open("rb") as f:
                handlers = plistlib.load(f).get("LSHandlers", [])
            for h in handlers:
                if h.get("LSHandlerURLScheme") in ("https", "http"):
                    return str(h.get("LSHandlerRoleAll", "")).lower() or None
        elif IS_WINDOWS:
            import winreg
            key = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return str(winreg.QueryValueEx(k, "ProgId")[0])
    except Exception:
        return None
    return None


def find_browsers() -> list[Browser]:
    """Installed browsers that can load the extension, the default browser first."""
    found: list[tuple[Browser, str]] = []
    for bid, name, mac_app, win_exe, url, hint, bundle_id, prog_id in _KNOWN:
        path = _mac_app(mac_app) if IS_MAC else _win_exe(win_exe) if IS_WINDOWS else None
        if path:
            found.append((Browser(bid, name, path, url, hint), bundle_id if IS_MAC else prog_id))
    default = (_default_browser_key() or "").lower()
    found.sort(key=lambda bk: 0 if default and bk[1].lower() == default else 1)  # stable: keeps Chrome first otherwise
    return [b for b, _ in found]


def open_browser(browser: Browser) -> None:
    """Bring the browser to the front.

    It can't be sent straight to its Extensions page: browsers ignore chrome://
    addresses handed to them by other programs (and web pages can't link to them),
    so the setup page copies the address for the person to paste.
    """
    try:
        if IS_MAC:
            subprocess.Popen(["/usr/bin/open", "-a", browser.path])
        else:
            subprocess.Popen([browser.path, "--new-window"])
    except Exception:
        log.exception("could not open %s", browser.name)


# --------------------------------------------------------------------------- start at login


def app_bundle() -> Path | None:
    """The Grabbit.app folder when running as a packaged Mac app."""
    if IS_MAC and FROZEN:
        exe = Path(sys.executable).resolve()  # .../Grabbit.app/Contents/MacOS/Grabbit
        if exe.parent.name == "MacOS" and exe.parents[2].suffix == ".app":
            return exe.parents[2]
    return None


def autostart_supported() -> bool:
    if os.environ.get("GRABBIT_DESKTOP_NO_AUTOSTART"):  # test runs: never add a login item
        return False
    return FROZEN and (IS_MAC or IS_WINDOWS)


def _launch_agent() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def launch_agent_plist(bundle: Path) -> dict:
    return {
        "Label": LAUNCH_AGENT_LABEL,
        # `open` starts it like a normal app; -g keeps it in the background.
        "ProgramArguments": ["/usr/bin/open", "-g", "-a", str(bundle), "--args", "--background"],
        "RunAtLoad": True,
        "ProcessType": "Interactive",
    }


def windows_run_command() -> str:
    return f'"{sys.executable}" --background'


def autostart_enabled() -> bool:
    if not autostart_supported():
        return False
    if IS_MAC:
        return _launch_agent().is_file()
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return bool(winreg.QueryValueEx(k, RUN_VALUE)[0])
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    """Turn "start at login" on or off. Returns the new state."""
    if not autostart_supported():
        return False
    try:
        if IS_MAC:
            agent, bundle = _launch_agent(), app_bundle()
            if enabled and bundle:
                agent.parent.mkdir(parents=True, exist_ok=True)
                with agent.open("wb") as f:
                    plistlib.dump(launch_agent_plist(bundle), f)
            elif not enabled:
                agent.unlink(missing_ok=True)
        else:
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                if enabled:
                    winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, windows_run_command())
                else:
                    try:
                        winreg.DeleteValue(k, RUN_VALUE)
                    except FileNotFoundError:
                        pass
    except Exception:
        log.exception("could not change start-at-login")
    return autostart_enabled()


def refresh_autostart() -> None:
    """Point an existing login item at this copy of the app (it may have moved or been updated)."""
    if autostart_enabled():
        set_autostart(True)


# --------------------------------------------------------------------------- macOS: run from Applications


def mac_needs_move() -> bool:
    """True when opened straight from the disk image or a quarantined Downloads folder.

    macOS then runs the app from a random read-only location ("App Translocation"),
    so "start at login" would point nowhere once the disk image is ejected.
    """
    bundle = app_bundle()
    if not bundle:
        return False
    s = str(bundle)
    on_read_only_volume = s.startswith("/Volumes/") and not os.access(bundle.parent, os.W_OK)
    return "/AppTranslocation/" in s or on_read_only_volume


def mac_move_to_applications() -> Path | None:
    """Copy Grabbit.app into /Applications (or ~/Applications) and return the new location."""
    bundle = app_bundle()
    if not bundle:
        return None
    for folder in (Path("/Applications"), Path.home() / "Applications"):
        target = folder / "Grabbit.app"
        try:
            folder.mkdir(exist_ok=True)
            if target.exists():
                shutil.rmtree(target)
            subprocess.run(["/usr/bin/ditto", str(bundle), str(target)], check=True, timeout=600)
            # The person already chose to open this app, so don't make macOS ask again
            # (and re-translocate it) for our own copy. Same as the common "LetsMove" helper.
            subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(target)], check=False, timeout=120)
            return target
        except (OSError, subprocess.SubprocessError):
            log.exception("could not copy Grabbit to %s", folder)
    return None


def relaunch(bundle_or_exe: Path | None = None, extra: list[str] | None = None) -> None:
    """Start a new copy of the app (after an update, a move, or a restart)."""
    args = ["--restarted", *(extra or [])]
    try:
        if IS_MAC and (bundle_or_exe or app_bundle()):
            subprocess.Popen(["/usr/bin/open", "-n", "-a", str(bundle_or_exe or app_bundle()), "--args", *args])
        elif FROZEN:
            flags = (0x00000008 | 0x00000200) if IS_WINDOWS else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([str(bundle_or_exe or sys.executable), *args], creationflags=flags, close_fds=True)
        else:
            subprocess.Popen([sys.executable, "-m", "grabbit_desktop", *args], close_fds=True)
    except Exception:
        log.exception("could not relaunch")
    time.sleep(0.2)
