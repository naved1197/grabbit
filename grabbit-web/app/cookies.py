"""Visitor-supplied cookies, for videos that need a signed-in account.

Accepts the two formats browser extensions export:

* Netscape ``cookies.txt`` (e.g. "Get cookies.txt LOCALLY"), including copies
  where tabs were turned into spaces by a paste
* JSON arrays (e.g. "Cookie-Editor", "EditThisCookie")

Everything is normalised into a clean Netscape file that yt-dlp's loader
accepts. Cookies are never stored by the server: the visitor's browser keeps
them and sends them with each request; the server writes them to a private
temp file for that one lookup/download and deletes it afterwards.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MAX_COOKIE_TEXT = 1_000_000
MAX_COOKIES = 5000
_HEADER = "# Netscape HTTP Cookie File\n# Supplied by a Grabbit visitor for one request; deleted afterwards.\n"
_DOMAIN_RE = re.compile(r"^\.?[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
_BOOL = {"TRUE": True, "FALSE": False}

# Links whose cookies live on another domain.
_ALIASES = {
    "youtu.be": "youtube.com",
    "youtube-nocookie.com": "youtube.com",
    "x.com": "twitter.com",
    "twitter.com": "x.com",
    "instagr.am": "instagram.com",
    "fb.watch": "facebook.com",
    "redd.it": "reddit.com",
    "vm.tiktok.com": "tiktok.com",
}


class BadCookies(ValueError):
    """The cookies can't be used. The message is safe to show to visitors."""


@dataclass(frozen=True)
class CookieSet:
    text: str  # normalised Netscape cookies.txt
    count: int
    domains: tuple[str, ...]  # registrable-ish names for display, e.g. ("instagram.com",)
    fingerprint: str  # short hash, used to keep caches per-visitor

    def covers(self, url: str) -> bool:
        """Whether any cookie could be sent to this link's site."""
        host = (urlsplit(url).hostname or "").lower()
        hosts = {host}
        for alias, target in _ALIASES.items():
            if host == alias or host.endswith("." + alias):
                hosts.add(target)
        for d in self.domains:
            for h in hosts:
                if h == d or h.endswith("." + d):
                    return True
        return False

    def write(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "cookies.txt"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(self.text)
        return path


def _row(domain: str, path: str, secure: bool, expires: int, name: str, value: str) -> tuple[str, ...] | None:
    domain = domain.strip()
    if domain.startswith("#HttpOnly_"):
        domain = domain[len("#HttpOnly_"):]
    if not name or not _DOMAIN_RE.match(domain):
        return None
    if any(ch in field for field in (domain, path, name, value) for ch in "\t\r\n"):
        return None
    # yt-dlp (via http.cookiejar) insists the subdomain flag matches the leading dot.
    include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
    return (domain, include_subdomains, path or "/", "TRUE" if secure else "FALSE",
            str(max(int(expires), 0)), name, value)


def _from_netscape(raw: str) -> tuple[list[tuple[str, ...]], int]:
    rows, bad = [], 0
    for line in raw.splitlines():
        line = line.rstrip("\r")
        if not line.strip():
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 6:
            parts = line.split(None, 6)  # tabs turned into spaces by copy/paste
        if len(parts) == 6:
            parts.append("")  # cookie with an empty value
        if len(parts) != 7:
            bad += 1
            continue
        domain, flag, path, secure, expires, name, value = (p.strip() if i < 6 else p for i, p in enumerate(parts))
        if flag.upper() not in _BOOL or secure.upper() not in _BOOL:
            bad += 1
            continue
        try:
            exp = int(float(expires or 0))
        except ValueError:
            bad += 1
            continue
        row = _row(domain, path, _BOOL[secure.upper()], exp, name, value)
        if row:
            rows.append(row)
        else:
            bad += 1
    return rows, bad


def _from_json(raw: str) -> tuple[list[tuple[str, ...]], int]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise BadCookies("That looks like JSON but couldn't be read. Export the cookies again.") from None
    if isinstance(data, dict):
        data = data.get("cookies") or data.get("Cookies") or []
    if not isinstance(data, list):
        raise BadCookies("Expected a list of cookies in the JSON.")
    rows, bad = [], 0
    for c in data:
        if not isinstance(c, dict):
            bad += 1
            continue
        domain = str(c.get("domain") or c.get("host") or "").strip()
        name = str(c.get("name") or "")
        value = str(c.get("value") if c.get("value") is not None else "")
        if c.get("hostOnly") is False and domain and not domain.startswith("."):
            domain = "." + domain
        exp = c.get("expirationDate", c.get("expires", c.get("expiry", 0)))
        try:
            exp = int(float(exp or 0))
        except (TypeError, ValueError):
            exp = 0
        row = _row(domain, str(c.get("path") or "/"), bool(c.get("secure")), exp, name, value)
        if row:
            rows.append(row)
        else:
            bad += 1
    return rows, bad


def _display_domain(domain: str) -> str:
    d = domain.lstrip(".").lower()
    if re.fullmatch(r"[\d.]+", d):  # IP address: show as-is
        return d
    parts = d.split(".")
    # keep the last two labels (three for things like co.uk / com.br)
    keep = 3 if len(parts) >= 3 and len(parts[-2]) <= 3 and len(parts[-1]) == 2 else 2
    return ".".join(parts[-keep:])


def parse_cookies(raw: str) -> CookieSet:
    raw = (raw or "").strip().lstrip("﻿")
    if not raw:
        raise BadCookies("Add a cookies file or paste its contents first.")
    if len(raw) > MAX_COOKIE_TEXT:
        raise BadCookies("That cookies file is too big. Export cookies for just the site you need.")
    rows, bad = _from_json(raw) if raw[0] in "[{" else _from_netscape(raw)
    if not rows:
        if bad:
            raise BadCookies(
                "That doesn't look like a cookies file. Export it in Netscape (cookies.txt) or JSON format."
            )
        raise BadCookies("No cookies found in that file.")
    if len(rows) > MAX_COOKIES:
        raise BadCookies("That's too many cookies. Export cookies for just the site you need.")
    # de-duplicate (last one wins), keep order
    unique: dict[tuple[str, str, str], tuple[str, ...]] = {}
    for r in rows:
        unique[(r[0], r[2], r[5])] = r
    rows = list(unique.values())
    text = _HEADER + "".join("\t".join(r) + "\n" for r in rows)
    domains = tuple(sorted({_display_domain(r[0]) for r in rows}))
    return CookieSet(
        text=text,
        count=len(rows),
        domains=domains,
        fingerprint=hashlib.sha256(text.encode()).hexdigest()[:16],
    )
