"""Starts Grabbit: the local server, the menu bar / tray icon and first-run setup."""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any

from . import VERSION, extension, paths, system
from .updater import Updater

log = logging.getLogger("grabbit.desktop")

UPDATE_FIRST_CHECK = 90          # seconds after start
UPDATE_INTERVAL = 12 * 3600      # then twice a day
IDLE_CHECK = 300                 # how often to look for a quiet moment to restart
QUIET_FOR = 300                  # no download activity for this long = safe to restart

PORT_BUSY = (
    "Grabbit can't start because another program on this computer is using port {port}.\n\n"
    "Close that program (or restart the computer) and open Grabbit again."
)


# --------------------------------------------------------------------------- process setup


def _fix_streams() -> None:
    # Apps without a console window have no stdout/stderr; some libraries write there anyway.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _setup_logging() -> None:
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(paths.log_file(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    ]
    if not paths.FROZEN:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=os.environ.get("GRABBIT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def _hide_console_windows() -> None:
    """On Windows, stop ffmpeg/deno from flashing a black console window."""
    if not paths.IS_WINDOWS:
        return
    base = subprocess.Popen

    class NoWindowPopen(base):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = NoWindowPopen  # type: ignore[misc]


def _server_environment() -> None:
    """Settings for a personal server on this computer (see ../grabbit-web/README.md)."""
    for key, value in {
        "GRABBIT_DOWNLOAD_DIR": str(paths.downloads_dir()),  # staging only; files end up in Downloads
        "GRABBIT_MAX_DURATION_MIN": "0",
        "GRABBIT_MAX_FILESIZE_MB": "0",
        "GRABBIT_MAX_JOBS_PER_IP": "100",
        "GRABBIT_RATE_LIMIT_PER_MIN": "600",
        "GRABBIT_PLAYLIST_LIMIT": "200",
    }.items():
        os.environ.setdefault(key, value)
    if not paths.FROZEN and str(paths.web_source()) not in sys.path:
        sys.path.append(str(paths.web_source()))  # the server's `app` package, when running from source
    tools = paths.tools_bin()
    if tools:  # bundled ffmpeg, ffprobe and deno come first
        os.environ["PATH"] = str(tools) + os.pathsep + os.environ.get("PATH", "")


# --------------------------------------------------------------------------- one copy at a time


def _instance_file():
    return paths.data_dir() / "instance.json"


def _write_instance(port: int, token: str) -> None:
    path = _instance_file()
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"port": port, "pid": os.getpid(), "version": VERSION, "token": token}, f)
    os.replace(tmp, path)


def _remove_instance() -> None:
    try:
        if json.loads(_instance_file().read_text("utf-8")).get("pid") == os.getpid():
            _instance_file().unlink()
    except (OSError, ValueError):
        pass


def _running(port: int) -> dict[str, Any] | str | None:
    """Status of a Grabbit already on this port, "other" for a different program, or None."""
    from .server import port_in_use

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/desktop/status", timeout=3) as res:
            data = json.loads(res.read())
        if isinstance(data, dict) and data.get("app") == "grabbit-desktop":
            return data
    except Exception:
        pass
    return "other" if port_in_use(port) else None


def _ask_to_quit(port: int) -> None:
    try:
        token = json.loads(_instance_file().read_text("utf-8"))["token"]
        req = urllib.request.Request(f"http://127.0.0.1:{port}/desktop/quit", method="POST",
                                     headers={"X-Grabbit-Token": token})
        urllib.request.urlopen(req, timeout=5).close()
    except Exception:
        log.warning("couldn't ask the running copy of Grabbit to quit", exc_info=True)


# --------------------------------------------------------------------------- yt-dlp updates


def _quiet(now: float) -> bool:
    from app.jobs import ACTIVE
    from app.main import manager

    for job in list(manager.jobs.values()):
        if job.status in ACTIVE:
            return False
        if now - max(job.created, job.finished or 0) < QUIET_FOR:
            return False  # a finished file may still be on its way to the browser
    return True


def _update_loop(desktop) -> None:
    time.sleep(UPDATE_FIRST_CHECK)
    while True:
        try:
            if desktop.updater.check():
                desktop._changed()
        except Exception as e:
            log.warning("yt-dlp update check failed: %s", e)
        deadline = time.monotonic() + UPDATE_INTERVAL
        while time.monotonic() < deadline:
            if desktop.updater.pending_version and _quiet(time.time()):
                log.info("restarting to use yt-dlp %s", desktop.updater.pending_version)
                desktop.restart()
                return
            time.sleep(IDLE_CHECK)


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="Grabbit")
    parser.add_argument("--background", action="store_true", help="started at login: don't open the setup page")
    parser.add_argument("--restarted", action="store_true", help="wait for the previous copy to finish quitting")
    args, _ = parser.parse_known_args(argv)  # macOS can add its own arguments

    _fix_streams()
    _setup_logging()
    _hide_console_windows()
    log.info("Grabbit %s starting (%s)", VERSION, sys.executable)

    if paths.IS_MAC and system.mac_needs_move():
        if system.ask(
            "Grabbit needs to be in your Applications folder so it can start by itself when you log in.",
            ok="Move to Applications", cancel="Quit",
        ):
            target = system.mac_move_to_applications()
            if target:
                system.relaunch(target)
                return 0
            system.alert("Grabbit couldn't copy itself. Drag Grabbit into your Applications folder, "
                         "then open it from there.")
        return 0

    from . import server

    port = paths.port()
    running = _running(port)
    if isinstance(running, dict):
        if running.get("version") == VERSION and running.get("executable") == sys.executable:
            if not args.background:
                system.open_url(f"http://localhost:{port}/desktop/")
            return 0
        log.info("Grabbit %s is already running from %s; taking over", running.get("version"), running.get("executable"))
        _ask_to_quit(port)
    elif running == "other" and not args.restarted:
        system.alert(PORT_BUSY.format(port=port))
        return 1

    sock = server.bind(port, wait=20 if args.restarted or running else 2)
    if sock is None:
        again = _running(port)
        if isinstance(again, dict):  # another copy started at the same moment
            if not args.background:
                system.open_url(f"http://localhost:{port}/desktop/")
            return 0
        system.alert(PORT_BUSY.format(port=port))
        return 1

    _server_environment()
    updater = Updater(paths.updates_dir())
    updater.activate()
    state = paths.State()
    try:
        extension.sync(paths.extension_source(), paths.extension_dir(), port)
    except OSError:
        log.exception("couldn't update the extension folder")

    from .controller import Desktop

    desktop = Desktop(port, state, updater)
    try:
        srv = server.ServerThread(server.create_app(desktop), sock)
        started = srv.start()
    except Exception:
        log.exception("server failed to start")
        started = False
    if not started:
        system.alert(f"Grabbit's download engine didn't start. Details are in:\n{paths.log_file()}")
        return 1
    _write_instance(port, desktop.token)
    log.info("ready at %s (data in %s)", desktop.base_url, paths.data_dir())

    if not state.get("installed_version") and system.autostart_supported():
        system.set_autostart(True)  # on by default; the tray menu and setup page can turn it off
    else:
        system.refresh_autostart()
    state.update(installed_version=VERSION)

    if not args.background:
        desktop.open_setup()
    threading.Thread(target=_update_loop, args=(desktop,), name="ytdlp-updates", daemon=True).start()

    try:
        from . import tray
        tray.run(desktop)
    except Exception:
        log.exception("no menu bar / tray icon; running without one")
        stop = threading.Event()
        desktop.on_quit = stop.set
        try:
            stop.wait()
        except KeyboardInterrupt:
            pass
    finally:
        log.info("shutting down")
        srv.stop()
        _remove_instance()
    if desktop.restart_requested:
        system.relaunch(extra=["--background"])
    return 0
