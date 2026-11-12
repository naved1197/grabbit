"""End-to-end API tests through the real yt-dlp pipeline, without the internet.

A generated MP4 is served from a local HTTP server; yt-dlp's generic extractor
downloads it like any direct video link. Private addresses are allowed for
this test server only.
"""

import copy
import functools
import http.server
import io
import zipfile
import os
import shutil
import subprocess
import tempfile
import threading
import time

import pytest

os.environ["GRABBIT_ALLOW_PRIVATE_URLS"] = "1"
os.environ["GRABBIT_DOWNLOAD_DIR"] = tempfile.mkdtemp(prefix="grabbit-test-dl-")
os.environ["GRABBIT_MAX_JOBS_PER_IP"] = "3"
os.environ["GRABBIT_RATE_LIMIT_PER_MIN"] = "1000"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app, manager  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg needed to make test media")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def media_server():
    root = tempfile.mkdtemp(prefix="grabbit-test-media-")
    if FFMPEG:
        subprocess.run(
            [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=3",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", os.path.join(root, "clip.mp4")],
            check=True,
        )
        # VP9 + Opus in an MP4: fine in Chrome, but QuickTime/iPhone won't play it.
        subprocess.run(
            [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=330:duration=2", "-c:v", "libvpx-vp9", "-deadline", "realtime",
             "-b:v", "300k", "-c:a", "libopus", "-shortest", os.path.join(root, "vp9.mp4")],
            check=True,
        )
        for i, color in ((1, "red"), (2, "blue")):  # photos for the post tests
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color={color}:size=64x48",
                            "-frames:v", "1", os.path.join(root, f"photo{i}.jpg")], check=True)
        # A bigger file for the cancel test (served slowly).
        with open(os.path.join(root, "big.bin"), "wb") as f:
            f.write(os.urandom(256 * 1024))

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send_head(self):
            # /members/... only works with the right session cookie
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


def wait_for(client, job_id, timeout=60):
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        seen.append(job["status"])
        if job["status"] in ("finished", "error", "canceled"):
            return job, seen
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish: {seen[-5:]}")


def test_index_and_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "Grabbit" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_config(client):
    cfg = client.get("/api/config").json()
    assert cfg["ytdlp_version"] and "file_ttl_min" in cfg


def test_bad_input_is_explained(client):
    r = client.post("/api/info", json={"url": "ftp://nope"})
    assert r.status_code == 400 and "http" in r.json()["error"]
    r = client.post("/api/jobs", json={"url": "https://example.com/v", "quality": "9000"})
    assert r.status_code == 422 and "quality" in r.json()["error"]
    r = client.get("/api/jobs/doesnotexist")
    assert r.status_code == 404
    assert client.get("/api/jobs?ids=nope").json()["jobs"] == [{"id": "nope", "status": "gone"}]


@needs_ffmpeg
def test_lookup_direct_video(client, media_server):
    r = client.post("/api/info", json={"url": f"{media_server}/clip.mp4"})
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["kind"] == "video" and info["title"] == "clip"


@needs_ffmpeg
@pytest.mark.parametrize("choice, ext, mime", [
    ({"mode": "video", "container": "mp4"}, ".mp4", "video/mp4"),
    ({"mode": "video", "container": "mkv"}, ".mkv", "video/x-matroska"),
    ({"mode": "audio", "audio_format": "mp3"}, ".mp3", "audio/mpeg"),
])
def test_download_end_to_end(client, media_server, choice, ext, mime):
    r = client.post("/api/jobs", json={"url": f"{media_server}/clip.mp4", **choice})
    assert r.status_code == 201, r.text
    job, _ = wait_for(client, r.json()["id"])
    assert job["status"] == "finished", job
    assert job["filename"].endswith(ext) and job["filesize"] > 1000
    f = client.get(f"/api/jobs/{job['id']}/file")
    assert f.status_code == 200
    assert f.headers["content-type"].startswith(mime)
    assert "attachment" in f.headers["content-disposition"]
    assert len(f.content) == job["filesize"]
    # Range requests work (resumable downloads / mobile players)
    part = client.get(f"/api/jobs/{job['id']}/file", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    # Removing deletes the file from disk
    path = manager.get(job["id"]).filepath
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert not path.exists()


@needs_ffmpeg
def test_cancel_running_download(client, media_server):
    r = client.post("/api/jobs", json={"url": f"{media_server}/slow/big.bin", "mode": "video"})
    job_id = r.json()["id"]
    deadline = time.time() + 30
    while client.get(f"/api/jobs/{job_id}").json()["status"] != "downloading":
        assert time.time() < deadline, "never started downloading"
        time.sleep(0.05)
    client.post(f"/api/jobs/{job_id}/cancel")
    job, _ = wait_for(client, job_id)
    assert job["status"] == "canceled"
    assert not manager.get(job_id).dir.exists()  # partial files cleaned up


MEMBER_COOKIES = "# Netscape HTTP Cookie File\n127.0.0.1\tFALSE\t/\tFALSE\t0\tsession\tletmein\n"


def test_cookie_check_endpoint(client):
    r = client.post("/api/cookies/check", json={"cookies": MEMBER_COOKIES})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1 and body["domains"] == ["127.0.0.1"]
    assert "letmein" in body["cookies"]
    r = client.post("/api/cookies/check", json={"cookies": "definitely not cookies"})
    assert r.status_code == 400 and r.json()["code"] == "bad_cookies"


@needs_ffmpeg
def test_cookies_unlock_members_only_video(client, media_server):
    url = f"{media_server}/members/clip.mp4"
    # Without cookies the site refuses
    r = client.post("/api/info", json={"url": url})
    assert r.status_code == 422 and r.json()["code"] == "login_required"
    assert "Add cookies" in r.json()["error"]
    # With the visitor's cookies, lookup and download both work
    r = client.post("/api/info", json={"url": url, "cookies": MEMBER_COOKIES})
    assert r.status_code == 200, r.text
    r = client.post("/api/jobs", json={"url": url, "cookies": MEMBER_COOKIES})
    assert r.status_code == 201
    created = r.json()
    assert "cookies" not in created and "letmein" not in r.text  # never echoed back
    job, _ = wait_for(client, created["id"])
    assert job["status"] == "finished", job
    assert not (manager.get(job["id"]).dir / "cookies.txt").exists()  # deleted after use
    assert manager.get(job["id"]).cookies is None
    # The cached (cookie-authorised) info is not reused for a visitor without cookies
    r = client.post("/api/jobs", json={"url": url})
    job, _ = wait_for(client, r.json()["id"])
    assert job["status"] == "error"


def test_bad_cookies_rejected_on_download(client):
    r = client.post("/api/jobs", json={"url": "https://example.com/v", "cookies": "nonsense"})
    assert r.status_code == 400 and r.json()["code"] == "bad_cookies"


def _codecs(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return out.split()


@needs_ffmpeg
def test_mp4_is_converted_to_play_on_apple_devices(client, media_server):
    job, stages = wait_for(client, client.post("/api/jobs", json={"url": f"{media_server}/vp9.mp4"}).json()["id"])
    assert job["status"] == "finished", job
    path = manager.get(job["id"]).filepath
    assert sorted(_codecs(path)) == ["aac", "h264"]
    # MKV keeps the original streams (nothing to convert there)
    job, _ = wait_for(client, client.post("/api/jobs", json={"url": f"{media_server}/vp9.mp4", "container": "mkv"}).json()["id"])
    assert job["status"] == "finished", job
    assert sorted(_codecs(manager.get(job["id"]).filepath)) == ["opus", "vp9"]


# --------------------------------------------------------------------- posts
# Instagram-style posts: a carousel of photos and videos, or a lone photo.
# yt-dlp's extraction is faked (the items point at the local media server) so
# this runs offline; everything after extraction is the real pipeline.


def _post(base, kinds, durations=None, title="Post by tester"):
    entries = []
    for i, kind in enumerate(kinds, 1):
        entry = {"id": f"item{i}", "title": "Video by tester",
                 "thumbnails": [{"url": f"{base}/photo2.jpg", "width": 32, "height": 24},
                                {"url": f"{base}/photo1.jpg", "width": 64, "height": 48}]}
        if kind == "video":
            entry["formats"] = [{"url": f"{base}/clip.mp4", "ext": "mp4", "format_id": "mp4", "vcodec": "avc1",
                                 "acodec": "mp4a", "width": 320, "height": 240}]
            entry["duration"] = (durations or {}).get(i)
        else:
            entry["formats"] = []  # what yt-dlp gives for a photo when signed in
        entries.append(entry)
    return {"_type": "playlist", "id": "POST", "title": title, "entries": entries,
            "extractor": "fake", "extractor_key": "Fake", "webpage_url": f"{base}/p/POST/"}


@pytest.fixture
def fake_extract(monkeypatch):
    """Map URL -> (raw result, warnings) for ``extract_raw``."""
    results = {}
    monkeypatch.setattr("app.resolve.extract_raw", lambda ydl, url, logger: copy.deepcopy(results[url]))
    return results


def _job(client, **body):
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 201, r.json()
    return wait_for(client, r.json()["id"])[0]


@needs_ffmpeg
@pytest.mark.parametrize("body, expected", [
    ({"item": 3}, "Video by tester (3 of 3).mp4"),                       # the slide they were on
    ({"meta": {"duration": 3.2}}, "Video by tester (3 of 3).mp4"),       # matched by length
    ({}, "Video by tester (1 of 3).mp4"),                                # no hints: first video
    ({"item": 2}, "Photo by tester (2 of 3).jpg"),                       # a photo slide
    ({"item": 2, "meta": {"duration": 9.9}}, "Video by tester (1 of 3).mp4"),  # watching a video: never a photo
])
def test_post_item_is_chosen(client, media_server, fake_extract, body, expected):
    url = f"{media_server}/p/MIXED{len(fake_extract)}/{abs(hash(str(body)))}"
    fake_extract[url] = (_post(media_server, ["video", "photo", "video"], durations={1: 10, 3: 3}), [])
    job = _job(client, url=url, **body)
    assert job["status"] == "finished", job
    assert job["filename"] == expected
    if expected.endswith(".jpg"):
        r = client.get(f"/api/jobs/{job['id']}/file")
        assert r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"


@needs_ffmpeg
def test_photo_only_post_is_zipped(client, media_server, fake_extract):
    url = f"{media_server}/p/PHOTOS/"
    fake_extract[url] = (_post(media_server, ["photo", "photo"]), [])
    job = _job(client, url=url)
    assert job["status"] == "finished", job
    assert job["filename"] == "Post by tester.zip"
    r = client.get(f"/api/jobs/{job['id']}/file")
    assert r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert names == ["Post by tester 1.jpg", "Post by tester 2.jpg"]
    job = _job(client, url=url, mode="audio")
    assert job["status"] == "error" and "only has photos" in job["error"]


@needs_ffmpeg
def test_single_photo_and_its_errors(client, media_server, fake_extract):
    photo = {"id": "PH", "title": "Video by tester", "formats": [], "extractor": "fake", "extractor_key": "Fake",
             "thumbnails": [{"url": f"{media_server}/photo1.jpg", "width": 64, "height": 48}]}
    fake_extract[f"{media_server}/p/PH/"] = (photo, [])
    job = _job(client, url=f"{media_server}/p/PH/")
    assert job["status"] == "finished" and job["filename"] == "Photo by tester.jpg", job
    job = _job(client, url=f"{media_server}/p/PH/", mode="audio")
    assert job["status"] == "error" and "photo" in job["error"]
    # No formats because the site wants a login: that reason wins, not "here's the thumbnail".
    fake_extract[f"{media_server}/p/AGE/"] = (dict(photo, id="AGE"), ["[fake] AGE: Sign in to confirm your age"])
    job = _job(client, url=f"{media_server}/p/AGE/")
    assert job["status"] == "error" and job["error_code"] == "login_required", job


@needs_ffmpeg
def test_post_lookup_then_batch_download(client, media_server, fake_extract):
    url = f"{media_server}/p/POST/"
    fake_extract[url] = (_post(media_server, ["photo", "video"], durations={2: 3}), [])
    info = client.post("/api/info", json={"url": url}).json()
    assert info["kind"] == "playlist" and info["post"] and (info["videos"], info["photos"]) == (1, 1)
    assert [(e["item"], e["title"], e["photo"]) for e in info["entries"]] == [(1, "Photo 1", True), (2, "Video 2", False)]
    del fake_extract[url]  # the download must come from the lookup's cache
    r = client.post("/api/jobs/batch", json={"items": [{"url": url, "item": 1}, {"url": url, "item": 2}]})
    jobs = [wait_for(client, j["id"])[0] for j in r.json()["jobs"]]
    assert [j["filename"] for j in jobs] == ["Photo by tester (1 of 2).jpg", "Video by tester (2 of 2).mp4"], jobs


@needs_ffmpeg
def test_silent_video_has_no_audio(client, media_server, fake_extract):
    url = f"{media_server}/p/SILENT/"
    raw = _post(media_server, ["video"])
    raw["entries"][0]["formats"][0]["acodec"] = "none"
    fake_extract[url] = (raw, [])
    job = _job(client, url=url, mode="audio")
    assert job["status"] == "error" and "no sound" in job["error"], job


def test_per_visitor_limit(client):
    ids = []
    try:
        for _ in range(3):
            # unroutable but "public" TEST-NET address: stays queued/starting a while
            r = client.post("/api/jobs", json={"url": "http://192.0.2.1/video.mp4"})
            assert r.status_code == 201
            ids.append(r.json()["id"])
        r = client.post("/api/jobs", json={"url": "http://192.0.2.1/video.mp4"})
        assert r.status_code == 429 and "downloads in progress" in r.json()["error"]
    finally:
        for i in ids:
            client.post(f"/api/jobs/{i}/cancel")


@pytest.mark.network
@pytest.mark.skipif(not os.environ.get("GRABBIT_NETWORK_TESTS"), reason="set GRABBIT_NETWORK_TESTS=1")
def test_real_youtube(client):
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    info = client.post("/api/info", json={"url": url}).json()
    assert info["title"] == "Me at the zoo"
    job, _ = wait_for(client, client.post("/api/jobs", json={"url": url}).json()["id"], timeout=120)
    assert job["status"] == "finished", job
