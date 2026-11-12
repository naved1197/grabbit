"""Grabbit web server.

Run locally:   uv run uvicorn app.main:app --reload
In production: one process only (download state lives in memory), e.g.
               uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yt_dlp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import load_settings
from .cookies import MAX_COOKIE_TEXT, BadCookies, CookieSet, parse_cookies
from .formats import DownloadChoice
from .jobs import JobLimitError, JobManager
from .security import BadURL, RateLimiter, check_url, client_ip
from .resolve import lookup
from .ytdl import InfoCache, UserFacingError, ffmpeg_path, js_runtime

logging.basicConfig(
    level=os.environ.get("GRABBIT_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("grabbit")

STATIC_DIR = Path(__file__).parent / "static"
LOOKUP_TIMEOUT = 90

settings = load_settings()
cache = InfoCache()
manager = JobManager(settings, cache)
limiter = RateLimiter(settings.rate_limit_per_min)
lookup_slots = asyncio.Semaphore(8)


@asynccontextmanager
async def lifespan(_: FastAPI):
    manager.start()
    log.info(
        "Grabbit ready: yt-dlp %s, ffmpeg=%s, js-runtime=%s, downloads in %s",
        yt_dlp.version.__version__, ffmpeg_path() or "MISSING", js_runtime() or "none",
        settings.download_dir,
    )
    if not ffmpeg_path():
        log.warning("ffmpeg not found: HD video (separate audio/video streams) and audio conversion are disabled")
    yield
    manager.shutdown()


app = FastAPI(title="Grabbit", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# --------------------------------------------------------------------------- helpers


def _ip(request: Request) -> str:
    return client_ip(request, settings.trust_proxy_headers)


def _rate_limit(request: Request) -> None:
    wait = limiter.hit(_ip(request))
    if wait:
        raise HTTPException(
            429, f"Slow down a little — try again in {int(wait) + 1} seconds.",
            headers={"Retry-After": str(int(wait) + 1)},
        )


async def _checked_url(raw: str) -> str:
    try:
        return await run_in_threadpool(check_url, raw, settings.allow_private_urls)
    except BadURL as e:
        raise HTTPException(400, str(e)) from None


def _cookies(raw: str | None) -> CookieSet | None:
    if not raw or not raw.strip():
        return None
    if not settings.allow_user_cookies:
        raise HTTPException(400, {"error": "This server doesn't accept cookies.", "code": "cookies_disabled"})
    try:
        return parse_cookies(raw)
    except BadCookies as e:
        raise HTTPException(400, {"error": str(e), "code": "bad_cookies"}) from None


# --------------------------------------------------------------------------- API

CookieField = Field(None, max_length=MAX_COOKIE_TEXT, description="cookies.txt or JSON export")


class InfoRequest(BaseModel):
    url: str = Field(max_length=4096)
    cookies: str | None = CookieField


class CookieCheck(BaseModel):
    cookies: str = Field(max_length=MAX_COOKIE_TEXT)


class JobMeta(BaseModel):
    title: str | None = Field(None, max_length=500)
    thumbnail: str | None = Field(None, max_length=2048)
    uploader: str | None = Field(None, max_length=300)
    duration: float | None = None


class JobRequest(DownloadChoice):
    url: str = Field(max_length=4096)
    item: int | None = Field(None, ge=1, le=100, description="which item of a multi-item post (1-based)")
    meta: JobMeta | None = None
    cookies: str | None = CookieField


class BatchRequest(DownloadChoice):
    items: list[JobRequest] = Field(min_length=1, max_length=200)
    cookies: str | None = CookieField


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, **manager.stats()}


@app.get("/api/config")
async def config() -> dict[str, Any]:
    return {
        "ytdlp_version": yt_dlp.version.__version__,
        "ffmpeg": bool(ffmpeg_path()),
        "js_runtime": js_runtime(),
        "max_duration_min": settings.max_duration_min,
        "max_filesize_mb": settings.max_filesize_mb,
        "file_ttl_min": settings.file_ttl_min,
        "playlist_limit": settings.playlist_limit,
        "max_jobs_per_ip": settings.max_jobs_per_ip,
        "user_cookies": settings.allow_user_cookies,
        "server_cookies": bool(settings.cookies_file or settings.cookies_from_browser),
    }


@app.post("/api/cookies/check")
async def check_cookies(body: CookieCheck, request: Request) -> dict[str, Any]:
    """Validate and normalise cookies before the page stores them (nothing is kept here)."""
    _rate_limit(request)
    jar = _cookies(body.cookies)
    if jar is None:
        raise HTTPException(400, {"error": "Add a cookies file or paste its contents first.", "code": "bad_cookies"})
    return {"count": jar.count, "domains": list(jar.domains), "cookies": jar.text}


@app.post("/api/info")
async def info(body: InfoRequest, request: Request) -> dict[str, Any]:
    _rate_limit(request)
    url = await _checked_url(body.url)
    jar = _cookies(body.cookies)
    async with lookup_slots:
        try:
            return await asyncio.wait_for(run_in_threadpool(lookup, url, settings, cache, jar), LOOKUP_TIMEOUT)
        except UserFacingError as e:
            raise HTTPException(422, {"error": str(e), "code": e.code}) from None
        except TimeoutError:
            raise HTTPException(504, "The site took too long to answer. Try again.") from None


@app.post("/api/jobs", status_code=201)
async def create_job(body: JobRequest, request: Request) -> dict[str, Any]:
    _rate_limit(request)
    url = await _checked_url(body.url)
    jar = _cookies(body.cookies)
    choice = DownloadChoice(**body.model_dump(include={"mode", "quality", "container", "audio_format"}))
    try:
        job = manager.submit(url, choice, _ip(request), body.meta.model_dump() if body.meta else None, jar,
                             item=body.item)
    except JobLimitError as e:
        raise HTTPException(429, str(e)) from None
    return manager.public(job)


@app.post("/api/jobs/batch", status_code=201)
async def create_jobs(body: BatchRequest, request: Request) -> dict[str, Any]:
    _rate_limit(request)
    ip = _ip(request)
    jar = _cookies(body.cookies)
    choice = DownloadChoice(**body.model_dump(include={"mode", "quality", "container", "audio_format"}))
    created, errors = [], []
    for item in body.items[: settings.playlist_limit]:
        try:
            url = await _checked_url(item.url)
            job = manager.submit(url, choice, ip, item.meta.model_dump() if item.meta else None, jar,
                                 item=item.item)
            created.append(manager.public(job))
        except HTTPException as e:
            errors.append({"url": item.url, "error": e.detail})
        except JobLimitError as e:
            errors.append({"url": item.url, "error": str(e)})
            break
    return {"jobs": created, "errors": errors}


@app.get("/api/jobs")
async def list_jobs(ids: str = Query("", max_length=4000)) -> dict[str, Any]:
    out = []
    for job_id in [i for i in ids.split(",") if i][:100]:
        job = manager.get(job_id)
        out.append(manager.public(job) if job else {"id": job_id, "status": "gone"})
    return {"jobs": out}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "This download has expired or doesn't exist.")
    return manager.public(job)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, Any]:
    job = manager.cancel(job_id)
    if not job:
        raise HTTPException(404, "This download has expired or doesn't exist.")
    return manager.public(job)


@app.delete("/api/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str) -> None:
    manager.remove(job_id)


@app.get("/api/jobs/{job_id}/file")
async def job_file(job_id: str):
    job = manager.get(job_id)
    if not job or job.status != "finished" or not job.filepath or not job.filepath.is_file():
        raise HTTPException(404, "This file has expired or isn't ready yet.")
    media_type = mimetypes.guess_type(job.filepath.name)[0] or "application/octet-stream"
    return FileResponse(
        job.filepath, media_type=media_type, filename=job.filepath.name,
        content_disposition_type="attachment",
    )


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if not request.url.path.startswith("/api/") and exc.status_code == 404:
        return FileResponse(STATIC_DIR / "index.html", status_code=404)
    body = exc.detail if isinstance(exc.detail, dict) else {"error": exc.detail}
    return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    errors = exc.errors()
    msg = errors[0].get("msg", "Invalid request.") if errors else "Invalid request."
    return JSONResponse({"error": str(msg).removeprefix("Value error, ")}, status_code=422)


# Static site (must be last so it doesn't shadow the API).
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
