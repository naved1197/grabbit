"""Offline tests for the desktop wrapper: routes, security checks, extension sync, updater."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

# Keep the tests away from the real data folder.
TMP_HOME = Path(os.environ.setdefault("GRABBIT_DESKTOP_HOME", tempfile.mkdtemp(prefix="grabbit-test-")))
os.environ.setdefault("GRABBIT_DOWNLOAD_DIR", str(TMP_HOME / "downloads"))

from fastapi.testclient import TestClient  # noqa: E402

from grabbit_desktop import extension, paths, system, updater  # noqa: E402
from grabbit_desktop.controller import Desktop  # noqa: E402
from grabbit_desktop.server import create_app  # noqa: E402

PORT = 8000
PAGE = {"Origin": f"http://localhost:{PORT}"}


@pytest.fixture(scope="module")
def desktop(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("desktop")
    return Desktop(PORT, paths.State(tmp / "state.json"), updater.Updater(tmp / "updates"))


@pytest.fixture(scope="module")
def client(desktop):
    # One app for the module: the web app's job manager starts once per process.
    app = create_app(desktop, allowed_hosts=["localhost", "testserver"])
    with TestClient(app, base_url=f"http://localhost:{PORT}") as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_state(request):
    if "desktop" in request.fixturenames:
        request.getfixturevalue("desktop").state.path.unlink(missing_ok=True)


def test_status_and_web_app_both_served(client):
    s = client.get("/desktop/status").json()
    assert s["app"] == "grabbit-desktop"
    assert s["extension"]["connected"] is False
    assert s["extension_dir"].endswith("Grabbit Extension")
    # the mounted Grabbit web app still works, and its startup ran (job manager)
    assert "ytdlp_version" in client.get("/api/config").json()
    assert client.get("/healthz").json()["ok"] is True
    assert "<title>Grabbit" in client.get("/").text


def test_setup_page_and_assets(client):
    r = client.get("/desktop/")
    assert r.status_code == 200 and "Set Up Grabbit" in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/desktop/assets/setup.js").status_code == 200
    assert client.get("/desktop", follow_redirects=False).status_code in (307, 308)


def test_extension_hello(client, desktop):
    r = client.post("/desktop/hello", json={"version": "1.0.0"}, headers={"Origin": "chrome-extension://abcdef"})
    assert r.status_code == 200
    assert client.get("/desktop/status").json()["extension"] == {
        "connected": True, "version": "1.0.0", "at": desktop.state.get("extension")["at"]}


def test_hello_from_a_website_is_refused(client):
    r = client.post("/desktop/hello", json={"version": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.get("/desktop/status").json()["extension"]["connected"] is False


def test_actions_need_the_setup_page_origin(client):
    for path, body in [("/desktop/reveal", {}), ("/desktop/open-browser", {"browser": "chrome"}),
                       ("/desktop/autostart", {"enabled": True})]:
        assert client.post(path, json=body).status_code == 403
        assert client.post(path, json=body, headers={"Origin": "https://evil.example"}).status_code == 403


def test_unknown_browser(client):
    r = client.post("/desktop/open-browser", json={"browser": "netscape"}, headers=PAGE)
    assert r.json() == {"ok": False}


def test_quit_needs_token(client, desktop):
    called = []
    desktop.on_quit = lambda: called.append(1)
    assert client.post("/desktop/quit").status_code == 403
    assert client.post("/desktop/quit", headers={"X-Grabbit-Token": "wrong"}).status_code == 403
    assert client.post("/desktop/quit", headers={"X-Grabbit-Token": desktop.token}).json() == {"ok": True}


def test_other_host_names_are_refused(desktop):
    app = create_app(desktop)  # production setting: localhost and 127.0.0.1 only
    # (no `with`: skips startup, which already ran for this process)
    assert TestClient(app, base_url="http://rebound.example").get("/desktop/status").status_code == 400
    assert TestClient(app, base_url="http://127.0.0.1:8000").get("/desktop/status").status_code == 200


# --------------------------------------------------------------------------- extension folder


def _fake_extension(root: Path) -> Path:
    src = root / "src"
    (src / "icons").mkdir(parents=True)
    (src / "manifest.json").write_text('{"manifest_version": 3}')
    (src / "background.js").write_text('const DEFAULTS = { server: "http://localhost:8000" };')
    (src / "icons" / "a.png").write_bytes(b"png")
    (src / ".DS_Store").write_bytes(b"junk")
    return src


def test_extension_sync(tmp_path):
    src, dst = _fake_extension(tmp_path), tmp_path / "Grabbit Extension"
    assert extension.sync(src, dst) is True
    assert sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file()) == [
        "background.js", "icons/a.png", "manifest.json"]
    assert extension.sync(src, dst) is False  # nothing changed

    (src / "icons" / "a.png").unlink()
    (src / "popup.js").write_text("x")
    (dst / "stale.js").write_text("old")
    assert extension.sync(src, dst) is True
    names = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
    assert names == ["background.js", "manifest.json", "popup.js"]
    assert not (dst / "icons").exists()


def test_extension_port_patch(tmp_path):
    src, dst = _fake_extension(tmp_path), tmp_path / "ext"
    extension.sync(src, dst, port=8765)
    assert "http://localhost:8765" in (dst / "background.js").read_text()


def test_real_extension_bundles():
    files = extension.bundled_files(paths.extension_source())
    assert "manifest.json" in files and "background.js" in files
    assert b"/desktop/hello" in files["background.js"]


# --------------------------------------------------------------------------- updater


def test_version_order():
    v = updater.vtuple
    assert v("2026.11.2") > v("2026.10.26") and v("2026.10.26.1") > v("2026.10.26") and v(None) == (0,)


def test_ejs_pin():
    rel = {"info": {"requires_dist": ['requests>=2; extra == "default"', 'yt-dlp-ejs==0.9.1; extra == "default"']}}
    assert updater._ejs_pin(rel) == "0.9.1"
    assert updater._ejs_pin({"info": {"requires_dist": None}}) is None


def test_extract_package_rejects_escape(tmp_path):
    good = tmp_path / "good.whl"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("yt_dlp/__init__.py", "")
        z.writestr("yt_dlp-1.dist-info/METADATA", "")
    updater.extract_package(good, "yt_dlp", tmp_path / "out")
    assert (tmp_path / "out" / "yt_dlp" / "__init__.py").is_file()
    assert not (tmp_path / "out" / "yt_dlp-1.dist-info").exists()

    bad = tmp_path / "bad.whl"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("yt_dlp/__init__.py", "")
        z.writestr("yt_dlp/../../evil.py", "")
    with pytest.raises(RuntimeError):
        updater.extract_package(bad, "yt_dlp", tmp_path / "out2")
    assert not (tmp_path / "evil.py").exists()


def test_activate_ignores_older_or_missing(tmp_path):
    u = updater.Updater(tmp_path)
    bundled = u.active_version
    assert u.activate() == bundled  # no pointer file
    (tmp_path / "yt-dlp-2000.1.1" / "yt_dlp").mkdir(parents=True)
    (tmp_path / "current.json").write_text(json.dumps({"yt-dlp": "2000.1.1", "dir": "yt-dlp-2000.1.1"}))
    assert u.activate() == bundled and str(tmp_path / "yt-dlp-2000.1.1") not in sys.path


# --------------------------------------------------------------------------- start at login


def test_launch_agent_plist():
    bundle = Path("/Applications/Grabbit.app")
    p = system.launch_agent_plist(bundle)
    assert p["RunAtLoad"] is True
    assert p["ProgramArguments"] == ["/usr/bin/open", "-g", "-a", str(bundle), "--args", "--background"]


def test_autostart_is_off_when_running_from_source():
    assert system.autostart_supported() is False
    assert system.set_autostart(True) is False
