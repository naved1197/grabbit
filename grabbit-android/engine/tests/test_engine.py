"""The Android engine bridge, end to end on the Mac: spawn it like the app does and talk JSON.

Run from the repo root (Python 3.12 and plain yt-dlp, like the phone):
    uv run --no-project --python 3.12 --with yt-dlp==2026.10.26 --with pytest pytest grabbit-android/engine/tests -q
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1] / "grabbit_engine.py"
WEB = Path(__file__).resolve().parents[3] / "grabbit-web"
FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(not FFMPEG, reason="ffmpeg needed to make test media")
MEMBER_COOKIES = json.dumps([{"domain": "127.0.0.1", "name": "session", "value": "letmein", "path": "/"}])


@pytest.fixture(scope="module")
def media():
    root = tempfile.mkdtemp(prefix="grabbit-engine-media-")
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", os.path.join(root, "clip.mp4")],
        check=True,
    )
    with open(os.path.join(root, "big.bin"), "wb") as f:
        f.write(os.urandom(512 * 1024))

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send_head(self):
            if self.path.startswith("/members/") and "session=letmein" not in (self.headers.get("Cookie") or ""):
                self.send_error(401, "Unauthorized")
                return None
            return super().send_head()

        def copyfile(self, source, outputfile):
            if self.path.startswith("/slow"):
                while chunk := source.read(4096):
                    outputfile.write(chunk)
                    time.sleep(0.05)
            else:
                super().copyfile(source, outputfile)

        def translate_path(self, path):
            return super().translate_path(path.replace("/slow", "", 1).replace("/members", "", 1))

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=root))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    shutil.rmtree(root, ignore_errors=True)


class EngineProc:
    """The engine as the app runs it: a child process, JSON lines both ways."""

    def __init__(self, home: Path):
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": str(WEB),
            "GRABBIT_DOWNLOAD_DIR": str(home / "jobs"),
            "GRABBIT_FFMPEG": FFMPEG or "",
            "GRABBIT_FFPROBE": shutil.which("ffprobe") or "",
        })
        self.proc = subprocess.Popen([sys.executable, "-u", str(ENGINE)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        self.replies: dict[int, dict] = {}
        self.events: queue.Queue[dict] = queue.Queue()
        self.raw: list[str] = []
        self.jobs: dict[str, dict] = {}
        self._cv = threading.Condition()
        self._next = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: [None for _ in self.proc.stderr], daemon=True).start()  # drain logs
        ready = self.event("ready", timeout=60)
        assert ready["protocol"] == 1

    def _read(self):
        for line in self.proc.stdout:
            self.raw.append(line)
            msg = json.loads(line)  # every line on the protocol channel must be JSON
            with self._cv:
                if "id" in msg:
                    self.replies[msg["id"]] = msg
                else:
                    if msg.get("event") == "jobs":
                        for j in msg["jobs"]:
                            self.jobs[j["id"]] = j
                    self.events.put(msg)
                self._cv.notify_all()

    def call(self, op, timeout=60, **args):
        self._next += 1
        rid = self._next
        self.proc.stdin.write(json.dumps({"id": rid, "op": op, **args}) + "\n")
        self.proc.stdin.flush()
        with self._cv:
            assert self._cv.wait_for(lambda: rid in self.replies, timeout), f"no reply to {op}"
            return self.replies.pop(rid)

    def event(self, name, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.events.get(timeout=0.5)
            except queue.Empty:
                continue
            if msg.get("event") == name:
                return msg
            if msg.get("event") == "fatal":
                raise AssertionError(msg)
        raise AssertionError(f"no {name} event")

    def wait_job(self, job_id, statuses=("finished", "error", "canceled"), timeout=60):
        with self._cv:
            assert self._cv.wait_for(lambda: self.jobs.get(job_id, {}).get("status") in statuses, timeout), \
                self.jobs.get(job_id)
            return self.jobs[job_id]

    def close(self):
        self.proc.stdin.close()
        return self.proc.wait(timeout=10)


@pytest.fixture
def home(tmp_path):
    return tmp_path


@pytest.fixture
def engine(home):
    e = EngineProc(home)
    yield e
    if e.proc.poll() is None:
        e.close()


def test_hello(engine):
    r = engine.call("hello")
    assert r["ok"] and r["result"]["ffmpeg"] is True and r["result"]["ytdlp"] and r["result"]["recovered"] == []


def test_lookup_then_download(engine, media):
    info = engine.call("lookup", url=f"{media}/clip.mp4")
    assert info["ok"], info
    assert info["result"]["kind"] == "video" and info["result"]["title"] == "clip"

    r = engine.call("submit", url=f"{media}/clip.mp4", choice={"mode": "video", "quality": "best"},
                    meta={"title": "clip"})
    assert r["ok"], r
    job = engine.wait_job(r["result"]["id"])
    assert job["status"] == "finished", job
    path = Path(job["path"])
    assert path.is_file() and path.suffix == ".mp4" and job["filesize"] == path.stat().st_size

    assert engine.call("remove", job=job["id"])["result"]["removed"] is True
    assert not path.exists()


def test_audio_only(engine, media):
    r = engine.call("submit", url=f"{media}/clip.mp4", choice={"mode": "audio", "audio_format": "m4a"})
    job = engine.wait_job(r["result"]["id"])
    assert job["status"] == "finished", job
    assert Path(job["path"]).suffix == ".m4a"


def test_cancel(engine, media):
    r = engine.call("submit", url=f"{media}/slow/big.bin", choice={})
    job_id = r["result"]["id"]
    engine.wait_job(job_id, statuses=("downloading",))
    assert engine.call("cancel", job=job_id)["ok"]
    assert engine.wait_job(job_id)["status"] == "canceled"


def test_errors_have_codes(engine, media):
    r = engine.call("lookup", url=f"{media}/members/clip.mp4")
    assert not r["ok"] and r["code"] == "login_required", r
    r = engine.call("lookup", url=f"{media}/members/clip.mp4", cookies=MEMBER_COOKIES)
    assert r["ok"], r
    r = engine.call("lookup", url=f"{media}/clip.mp4", cookies="not cookies at all")
    assert not r["ok"] and r["code"] == "bad_cookies"
    r = engine.call("submit", url=f"{media}/clip.mp4", choice={"quality": "999"})
    assert not r["ok"] and r["code"] == "bad_choice"
    r = engine.call("lookup", url="ftp://example.com/x")
    assert not r["ok"] and r["code"] == "bad_url"
    r = engine.call("cancel", job="nope")
    assert not r["ok"] and r["code"] == "not_found"
    r = engine.call("teleport")
    assert not r["ok"] and r["code"] == "bad_request"


def test_batch(engine, media):
    r = engine.call("batch", choice={"mode": "audio", "audio_format": "best"},
                    items=[{"url": f"{media}/clip.mp4"}, {"url": "ftp://nope"}])
    assert r["ok"] and len(r["result"]["jobs"]) == 1 and len(r["result"]["errors"]) == 1
    assert engine.wait_job(r["result"]["jobs"][0]["id"])["status"] == "finished"


def test_finished_file_survives_a_restart(home, media):
    first = EngineProc(home)
    r = first.call("submit", url=f"{media}/clip.mp4", choice={})
    job = first.wait_job(r["result"]["id"])
    time.sleep(1)  # let the engine mark it done
    assert first.close() is not None  # the app went away before saving the file

    second = EngineProc(home)
    try:
        recovered = second.call("hello")["result"]["recovered"]
        assert len(recovered) == 1 and recovered[0]["job"] == job["id"]
        assert Path(recovered[0]["path"]).is_file() and recovered[0]["name"] == Path(job["path"]).name
    finally:
        second.close()


def test_exits_when_the_app_goes_away(engine):
    assert engine.close() == 0
