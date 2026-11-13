"""The local web server: the Grabbit web app plus a few /desktop/ routes for setup."""

from __future__ import annotations

import logging
import socket
import threading
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import extension, paths

if TYPE_CHECKING:
    from .controller import Desktop

log = logging.getLogger("grabbit.desktop")

EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://")
PAGE_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


class Hello(BaseModel):
    version: str = Field("", max_length=40)


class BrowserChoice(BaseModel):
    browser: str = Field(max_length=40)


class Toggle(BaseModel):
    enabled: bool


def create_app(desktop: Desktop, allowed_hosts: list[str] | None = None) -> FastAPI:
    # Imported here: the server reads its settings from the environment at import time.
    from app import main as web

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Starlette doesn't run a mounted app's lifespan, so run the web app's here.
        async with web.lifespan(web.app):
            yield

    desk = FastAPI(title="Grabbit", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # Only answer requests addressed to this computer (blocks DNS-rebinding attacks).
    desk.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or ["localhost", "127.0.0.1"])

    page_origins = {f"http://localhost:{desktop.port}", f"http://127.0.0.1:{desktop.port}"}

    def from_setup_page(request: Request) -> None:
        if request.headers.get("origin") not in page_origins:
            raise HTTPException(403, "Not allowed.")

    @desk.middleware("http")
    async def page_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/desktop"):
            response.headers.setdefault("Content-Security-Policy", PAGE_CSP)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            response.headers["Cache-Control"] = "no-store"
        return response

    @desk.get("/desktop/status")
    async def status() -> dict[str, Any]:
        return desktop.status()

    @desk.post("/desktop/hello")
    async def hello(request: Request, body: Hello | None = None) -> dict[str, bool]:
        """The extension calls this when it's installed, so the setup page can say "done"."""
        origin = request.headers.get("origin") or ""
        if origin and not origin.startswith(EXTENSION_ORIGINS):
            raise HTTPException(403, "Not allowed.")
        desktop.extension_hello(body.version if body else "")
        return {"ok": True}

    @desk.post("/desktop/open-browser")
    async def open_browser(body: BrowserChoice, request: Request) -> dict[str, bool]:
        from_setup_page(request)
        return {"ok": desktop.open_browser(body.browser)}

    @desk.post("/desktop/reveal")
    async def reveal(request: Request) -> dict[str, bool]:
        from_setup_page(request)
        desktop.reveal_extension()
        return {"ok": True}

    @desk.post("/desktop/shortcut")
    def shortcut(request: Request) -> dict[str, Any]:
        """Put a link to the extension folder on the Desktop.

        A plain `def` so it runs in a worker thread: the first time, macOS holds the call
        while it asks the person whether Grabbit may use the Desktop.
        """
        from_setup_page(request)
        if not extension.shortcuts_supported():
            return {"ok": False, "error": "unsupported"}
        try:
            link = desktop.put_on_desktop()
        except PermissionError:
            log.warning("not allowed to use the Desktop", exc_info=True)
            return {"ok": False, "error": "denied"}
        except OSError:
            log.exception("couldn't put the extension link on the Desktop")
            return {"ok": False, "error": "failed"}
        return {"ok": True, "path": str(link)}

    @desk.post("/desktop/autostart")
    async def autostart(body: Toggle, request: Request) -> dict[str, bool]:
        from_setup_page(request)
        return {"enabled": desktop.set_autostart(body.enabled)}

    @desk.post("/desktop/quit")
    async def quit_app(request: Request) -> dict[str, bool]:
        """Lets a newer copy of the app take over. Needs the token from the data folder."""
        if request.headers.get("x-grabbit-token") != desktop.token:
            raise HTTPException(403, "Not allowed.")
        threading.Timer(0.3, desktop.quit).start()
        return {"ok": True}

    @desk.get("/desktop", include_in_schema=False)
    async def desktop_redirect():
        return RedirectResponse("/desktop/")

    @desk.get("/desktop/", include_in_schema=False)
    async def setup_page():
        return FileResponse(paths.setup_page_dir() / "index.html", media_type="text/html")

    desk.mount("/desktop/assets", StaticFiles(directory=paths.setup_page_dir()), name="desktop-assets")
    desk.mount("/", web.app)
    return desk


# --------------------------------------------------------------------------- running it


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def bind(port: int, wait: float = 0) -> socket.socket | None:
    """Claim 127.0.0.1:port, waiting up to `wait` seconds for it to come free."""
    deadline = time.monotonic() + wait
    while True:
        if not port_in_use(port):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if paths.IS_WINDOWS:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # only after the check above
                s.bind(("127.0.0.1", port))
                s.listen(128)
                return s
            except OSError:
                s.close()
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


class ServerThread:
    def __init__(self, app: FastAPI, sock: socket.socket):
        config = uvicorn.Config(app, log_config=None, access_log=False, lifespan="on",
                                timeout_graceful_shutdown=5)
        self.server = uvicorn.Server(config)
        self.sock = sock
        self.thread = threading.Thread(target=self._run, name="grabbit-server", daemon=True)

    def _run(self) -> None:
        try:
            self.server.run(sockets=[self.sock])
        except BaseException:  # SystemExit included: uvicorn exits on startup failure
            log.exception("server stopped")

    def start(self, timeout: float = 60) -> bool:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.thread.is_alive():
            if self.server.started:
                return True
            time.sleep(0.05)
        return False

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)
        try:
            self.sock.close()
        except OSError:
            pass
