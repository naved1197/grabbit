"""Keeps yt-dlp current without reinstalling the app.

Sites change all the time, so a yt-dlp that is a few weeks old stops working on
some of them (YouTube especially). The app ships with one yt-dlp version; this
module downloads newer releases from PyPI (pure-Python wheels, checked against
PyPI's SHA-256) into the user's data folder and puts them first on sys.path at
the next start. If an update fails to import, the bundled version is used.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import logging
import re
import shutil
import ssl
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from . import VERSION

log = logging.getLogger("grabbit.desktop")

PYPI = "https://pypi.org/pypi"
USER_AGENT = f"Grabbit-Desktop/{VERSION}"
PACKAGES = {"yt-dlp": "yt_dlp", "yt-dlp-ejs": "yt_dlp_ejs"}  # PyPI name -> import name


def vtuple(version: str | None) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version or "")) or (0,)


def bundled_version(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as res:
        return res.read()


def _pypi(name: str, version: str | None = None) -> dict[str, Any]:
    url = f"{PYPI}/{name}/{version}/json" if version else f"{PYPI}/{name}/json"
    return json.loads(_get(url))


def _wheel(release: dict[str, Any]) -> tuple[str, str]:
    """(url, sha256) of the pure-Python wheel in a PyPI release."""
    for f in release.get("urls", []):
        if f.get("packagetype") == "bdist_wheel" and f.get("filename", "").endswith("-none-any.whl"):
            return f["url"], f["digests"]["sha256"]
    raise RuntimeError("no pure-Python wheel in this release")


def _ejs_pin(release: dict[str, Any]) -> str | None:
    for req in release.get("info", {}).get("requires_dist") or []:
        m = re.match(r"^yt-dlp-ejs\s*==\s*([\w.]+)", req)
        if m:
            return m.group(1)
    return None


def extract_package(wheel: Path, package: str, dest: Path) -> None:
    """Unpack only `package/` from a wheel, refusing anything that escapes `dest`."""
    root = dest.resolve()
    with zipfile.ZipFile(wheel) as z:
        for member in z.infolist():
            name = member.filename
            if not name.startswith(f"{package}/") or member.is_dir():
                continue
            out = (dest / name).resolve()
            if root not in out.parents:
                raise RuntimeError(f"unsafe path in wheel: {name}")
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, out.open("wb") as f:
                shutil.copyfileobj(src, f)
    if not (dest / package / "__init__.py").is_file():
        raise RuntimeError(f"{package} not found in {wheel.name}")


class Updater:
    def __init__(self, folder: Path):
        self.folder = folder
        self.pointer = folder / "current.json"
        self.active_dir: Path | None = None
        self.active_version = bundled_version("yt-dlp")
        self.pending_version: str | None = None  # downloaded, used after a restart

    # ------------------------------------------------------------------ startup

    def activate(self) -> str | None:
        """Call before anything imports yt_dlp. Returns the yt-dlp version in use."""
        try:
            info = json.loads(self.pointer.read_text("utf-8"))
            version, folder = info["yt-dlp"], self.folder / info["dir"]
        except (OSError, ValueError, KeyError, TypeError):
            return self.active_version
        if vtuple(version) <= vtuple(self.active_version) or not (folder / "yt_dlp").is_dir():
            return self.active_version
        sys.path.insert(0, str(folder))
        try:
            mod = importlib.import_module("yt_dlp")
            loaded = Path(mod.__file__ or "").resolve()
            if folder.resolve() not in loaded.parents:
                raise ImportError(f"bundled yt-dlp was imported instead ({loaded})")
            self.active_dir = folder
            self.active_version = importlib.import_module("yt_dlp.version").__version__
            log.info("using updated yt-dlp %s from %s", self.active_version, folder)
        except Exception:
            log.exception("updated yt-dlp %s failed to load; using the bundled one", version)
            sys.path.remove(str(folder))
            for name in [m for m in sys.modules if m.split(".")[0] in PACKAGES.values()]:
                del sys.modules[name]
            self.pointer.unlink(missing_ok=True)
            shutil.rmtree(folder, ignore_errors=True)
        return self.active_version

    # ------------------------------------------------------------------ checking

    def check(self) -> str | None:
        """Download a newer yt-dlp if there is one. Returns the new version, if any."""
        release = _pypi("yt-dlp")
        latest = release["info"]["version"]
        newest_known = max(vtuple(self.active_version), vtuple(self.pending_version))
        if vtuple(latest) <= newest_known:
            log.info("yt-dlp is up to date (%s)", self.pending_version or self.active_version)
            return None
        log.info("downloading yt-dlp %s (using %s)", latest, self.active_version)
        wheels = [("yt-dlp", release)]
        ejs = _ejs_pin(release)
        if ejs and ejs != bundled_version("yt-dlp-ejs"):
            wheels.append(("yt-dlp-ejs", _pypi("yt-dlp-ejs", ejs)))

        self.folder.mkdir(parents=True, exist_ok=True)
        final = self.folder / f"yt-dlp-{latest}"
        with tempfile.TemporaryDirectory(dir=self.folder, prefix=".download-") as tmp:
            staging = Path(tmp) / "pkg"
            for name, rel in wheels:
                url, sha256 = _wheel(rel)
                data = _get(url, timeout=300)
                if hashlib.sha256(data).hexdigest() != sha256:
                    raise RuntimeError(f"{name} download is corrupt (checksum mismatch)")
                wheel = Path(tmp) / url.rsplit("/", 1)[-1]
                wheel.write_bytes(data)
                extract_package(wheel, PACKAGES[name], staging)
            shutil.rmtree(final, ignore_errors=True)
            staging.rename(final)
        tmp_pointer = self.pointer.with_suffix(".tmp")
        tmp_pointer.write_text(json.dumps({"yt-dlp": latest, "dir": final.name}), "utf-8")
        tmp_pointer.replace(self.pointer)
        self._cleanup(keep={final.name, *([self.active_dir.name] if self.active_dir else [])})
        self.pending_version = latest
        log.info("yt-dlp %s is ready and will be used after the next restart", latest)
        return latest

    def _cleanup(self, keep: set[str]) -> None:
        for child in self.folder.glob("yt-dlp-*"):
            if child.is_dir() and child.name not in keep:
                shutil.rmtree(child, ignore_errors=True)
