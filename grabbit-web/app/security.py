"""Input checks that matter once the site is public.

* Only http(s) links are accepted.
* Links that resolve to private, loopback or link-local addresses are refused,
  so the server can't be used to reach its own network (e.g. cloud metadata
  endpoints at 169.254.169.254).
* A small per-IP rate limiter keeps one visitor from hogging the server.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from fastapi import Request

MAX_URL_LENGTH = 2048
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)


class BadURL(ValueError):
    """The link can't be used. The message is safe to show to visitors."""


def normalize_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        raise BadURL("Paste a link to a video first.")
    if len(url) > MAX_URL_LENGTH:
        raise BadURL("That link is too long.")
    if any(ch.isspace() for ch in url):
        # People often paste "Check this out https://..." — pick out the link.
        match = re.search(r"https?://\S+", url)
        if not match:
            raise BadURL("That doesn't look like a link.")
        url = match.group(0)
    if not _SCHEME_RE.match(url):
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise BadURL("Only http:// and https:// links are supported.")
    if not parts.hostname or "." not in parts.hostname.strip("."):
        if not _is_ip_literal(parts.hostname or ""):
            raise BadURL("That doesn't look like a valid link.")
    return url


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def ensure_public_host(url: str) -> None:
    """Raise BadURL if the link's host is (or resolves to) a non-public address."""
    host = urlsplit(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise BadURL(f"Couldn't find the website “{host}”. Check the link.") from None
    addresses = {info[4][0] for info in infos}
    if not addresses or not all(_is_public(a) for a in addresses):
        raise BadURL("Links to private or local network addresses aren't allowed.")


def check_url(raw: str, allow_private: bool) -> str:
    url = normalize_url(raw)
    if not allow_private:
        ensure_public_host(url)
    return url


def client_ip(request: Request, trust_proxy_headers: bool) -> str:
    """The visitor's IP. Behind a proxy, use the address *that proxy* saw.

    The proxy appends the real client address to the end of X-Forwarded-For;
    anything before it was sent by the client and can be forged, so only the
    last entry is trusted.
    """
    if trust_proxy_headers:
        for header in ("fly-client-ip", "cf-connecting-ip"):
            value = request.headers.get(header)
            if value:
                return value.strip()
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[-1].strip()
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Sliding-window limiter: at most ``limit`` hits per ``window`` seconds per key."""

    def __init__(self, limit: int, window: float = 60.0):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float:
        """Record a hit. Returns 0 if allowed, else seconds until the next slot frees up."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return max(self.window - (now - q[0]), 1.0)
            q.append(now)
            if len(self._hits) > 10_000:  # forget idle visitors
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return 0.0
