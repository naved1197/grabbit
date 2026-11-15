"""Offline unit tests: URL safety, format selection, info summaries, errors."""

import json

import pytest

from app.cookies import BadCookies, parse_cookies
from app.formats import DownloadChoice, format_options, summarize_playlist, summarize_video
from app.security import BadURL, RateLimiter, check_url, normalize_url
from app.resolve import best_image, is_post, photo_title, pick_entry, summarize_post
from app.ytdl import LOGIN_REQUIRED, describe_error, friendly_error

# --------------------------------------------------------------------- URLs


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://youtu.be/abc", "https://youtu.be/abc"),
        ("youtube.com/watch?v=abc", "https://youtube.com/watch?v=abc"),
        ("  https://x.com/a/status/1  ", "https://x.com/a/status/1"),
        ("Check this out! https://vimeo.com/123 so good", "https://vimeo.com/123"),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "ftp://example.com/x", "file:///etc/passwd", "javascript:alert(1)", "localhost:8000", "not a link"],
)
def test_rejects_bad_urls(raw):
    with pytest.raises(BadURL):
        normalize_url(raw)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.5/video.mp4",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
def test_blocks_private_addresses(url):
    with pytest.raises(BadURL, match="private or local"):
        check_url(url, allow_private=False)


def test_private_addresses_allowed_when_configured():
    assert check_url("http://127.0.0.1/x", allow_private=True) == "http://127.0.0.1/x"


def test_rate_limiter():
    rl = RateLimiter(limit=3, window=60)
    assert [rl.hit("a") for _ in range(3)] == [0, 0, 0]
    assert rl.hit("a") > 0
    assert rl.hit("b") == 0  # other visitors unaffected


# ------------------------------------------------------------------ formats


def test_video_mp4_prefers_h264_at_chosen_height():
    opts = format_options(DownloadChoice(mode="video", quality="1080p", container="mp4"))
    assert opts["format"] == "bv*+ba/b"
    assert opts["format_sort"][:3] == ["res:1080", "ext:mp4:m4a", "vcodec:h264"]
    assert opts["merge_output_format"] == "mp4"


def test_video_webm_falls_back_to_mkv():
    opts = format_options(DownloadChoice(mode="video", container="webm"))
    assert opts["merge_output_format"] == "webm/mkv"
    assert opts["format_sort"][0] == "res"


def test_audio_mp3_converts_and_tags():
    opts = format_options(DownloadChoice(mode="audio", audio_format="mp3"))
    keys = [p["key"] for p in opts["postprocessors"]]
    assert keys == ["FFmpegExtractAudio", "FFmpegMetadata", "EmbedThumbnail"]
    assert opts["postprocessors"][0]["preferredcodec"] == "mp3"
    assert opts["writethumbnail"] is True


def test_without_ffmpeg_only_single_files():
    assert format_options(DownloadChoice(mode="video"), has_ffmpeg=False)["format"] == "b"
    assert "postprocessors" not in format_options(DownloadChoice(mode="audio"), has_ffmpeg=False)


@pytest.mark.parametrize("bad", [{"quality": "999"}, {"container": "avi"}, {"audio_format": "wav"}, {"mode": "gif"}])
def test_choice_validation(bad):
    with pytest.raises(ValueError):
        DownloadChoice(**bad)


# ------------------------------------------------------------------ summaries


def test_summarize_video_groups_qualities_and_adds_audio_size():
    info = {
        "id": "x", "title": "Clip", "uploader": "Me", "duration": 100, "extractor_key": "Youtube",
        "thumbnails": [{"url": "https://i/small.jpg"}, {"url": "https://i/big.webp"}, {"url": "https://i/big.jpg"}],
        "formats": [
            {"format_id": "sb0", "url": "u", "vcodec": "none", "acodec": "none"},
            {"format_id": "140", "url": "u", "vcodec": "none", "acodec": "mp4a", "abr": 128, "filesize": 1_000},
            {"format_id": "137", "url": "u", "vcodec": "avc1", "acodec": "none", "height": 1080, "fps": 30, "filesize": 10_000},
            {"format_id": "399", "url": "u", "vcodec": "av01", "acodec": "none", "height": 1080, "fps": 60, "filesize": 8_000},
            {"format_id": "136", "url": "u", "vcodec": "avc1", "acodec": "none", "height": 720, "filesize": 5_000},
            {"format_id": "18", "url": "u", "vcodec": "avc1", "acodec": "mp4a", "height": 360, "filesize": 2_000},
        ],
    }
    s = summarize_video(info)
    assert s["thumbnail"] == "https://i/big.jpg"
    assert [q["value"] for q in s["qualities"]] == ["1080", "720", "360"]
    assert s["qualities"][0]["label"] == "1080p60"
    assert s["qualities"][0]["size"] == 11_000  # largest 1080 video + best audio
    assert s["qualities"][2]["size"] == 2_000  # already has audio
    assert s["has_audio"] and s["has_video"]


def test_direct_links_with_unknown_codecs_are_video():
    info = {"ext": "mp4", "formats": [{"format_id": "mp4", "url": "https://x/clip.mp4", "ext": "mp4"}]}
    s = summarize_video(info)
    assert s["has_video"] and s["has_audio"]


def test_audio_only_sources():
    s = summarize_video({"ext": "mp3", "formats": [{"url": "u", "ext": "mp3", "vcodec": "none", "acodec": "mp3"}]})
    assert not s["has_video"] and s["has_audio"]


def test_summarize_video_snaps_odd_heights():
    info = {"formats": [{"url": "u", "vcodec": "h264", "acodec": "aac", "height": 1072}]}
    assert summarize_video(info)["qualities"][0]["value"] == "1080"


def test_summarize_playlist_builds_youtube_urls_and_limits():
    info = {
        "_type": "playlist", "title": "Mix", "playlist_count": 3, "extractor_key": "YoutubeTab",
        "entries": [
            {"url": "aaa", "ie_key": "Youtube", "title": "A", "duration": 60},
            {"url": "https://www.youtube.com/watch?v=bbb", "title": "B"},
            {"url": "ccc", "ie_key": "Youtube", "title": "C"},
        ],
    }
    s = summarize_playlist(info, limit=2)
    assert [e["url"] for e in s["entries"]] == [
        "https://www.youtube.com/watch?v=aaa", "https://www.youtube.com/watch?v=bbb"]
    assert s["count"] == 3 and s["truncated"]


# ------------------------------------------------------------------- errors


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ERROR: Unsupported URL: https://example.com/", "This site isn't supported"),
        ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser", "isn't a bot"),
        ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "private"),
        ("ERROR: [vimeo] 123: Something odd happened", "Something odd happened"),
        ("\x1b[0;31mERROR:\x1b[0m [generic] HTTP Error 404: Not Found", "404"),
    ],
)
def test_friendly_errors(raw, expected):
    assert expected in friendly_error(raw)


IG_URL = "https://www.instagram.com/reels/DflobXFsjtda51Qdv/"
IG_FOLLOWERS = ("ERROR: [Instagram] DflobXFsjtda51Qdv: This content is only available for registered "
                "users who follow this account")
IG_RATE = ("ERROR: [Instagram] abc: Requested content is not available, rate-limit reached or login "
           "required. Use --cookies, --cookies-from-browser, --username and --password")


@pytest.mark.parametrize("raw", [IG_FOLLOWERS, IG_RATE, "ERROR: [youtube] x: Sign in to confirm your age"])
def test_login_errors_ask_for_cookies(raw):
    msg, code = describe_error(raw, None, IG_URL)
    assert code == LOGIN_REQUIRED
    assert "Add cookies" in msg and "instagram.com" in msg
    assert "--cookies" not in msg  # no CLI advice for website visitors


def test_login_error_with_cookies_for_another_site():
    yt = parse_cookies(".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tx")
    msg, code = describe_error(IG_FOLLOWERS, yt, IG_URL)
    assert code == LOGIN_REQUIRED
    assert "for youtube.com, not instagram.com" in msg


def test_login_error_with_matching_cookies_says_they_failed():
    ig = parse_cookies(".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tx")
    msg, _ = describe_error(IG_FOLLOWERS, ig, IG_URL)
    assert "didn't work" in msg


def test_other_errors_have_no_code():
    assert describe_error("ERROR: Unsupported URL: https://x.y/")[1] is None


# ------------------------------------------------------------------ cookies

NETSCAPE = """# Netscape HTTP Cookie File
# This is a generated file!  Do not edit.

.instagram.com\tTRUE\t/\tTRUE\t1793000000\tsessionid\tabc%3A123
#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1793000000\tds_user_id\t42
www.instagram.com\tFALSE\t/\tFALSE\t0\tig_nrcb\t1
"""


def test_parse_netscape_cookies():
    jar = parse_cookies(NETSCAPE)
    assert jar.count == 3
    assert jar.domains == ("instagram.com",)
    assert "#HttpOnly_" not in jar.text
    assert jar.text.startswith("# Netscape HTTP Cookie File")
    assert ".instagram.com\tTRUE\t/\tTRUE\t1793000000\tsessionid\tabc%3A123" in jar.text
    assert jar.covers("https://www.instagram.com/reel/x/") and jar.covers("https://instagr.am/p/x")
    assert not jar.covers("https://youtube.com/watch?v=x")


def test_parse_cookies_survives_tabs_turned_into_spaces():
    jar = parse_cookies(NETSCAPE.replace("\t", "    "))
    assert jar.count == 3


def test_parse_cookies_fixes_subdomain_flag():
    # yt-dlp rejects the whole file if the flag disagrees with the leading dot
    jar = parse_cookies("instagram.com\tTRUE\t/\tTRUE\t0\ta\t1\n.instagram.com\tFALSE\t/\tTRUE\t0\tb\t2")
    lines = [line for line in jar.text.splitlines() if not line.startswith("#")]
    assert lines == ["instagram.com\tFALSE\t/\tTRUE\t0\ta\t1", ".instagram.com\tTRUE\t/\tTRUE\t0\tb\t2"]


def test_parse_json_export():
    data = [
        {"domain": ".youtube.com", "name": "SID", "value": "v1", "path": "/", "secure": True,
         "expirationDate": 1793000000.5, "hostOnly": False},
        {"domain": "www.youtube.com", "name": "PREF", "value": "f6=1", "hostOnly": True, "session": True},
        {"domain": "youtube.com", "name": "VISITOR", "value": "z", "hostOnly": False},
        {"name": "missing-domain", "value": "x"},
    ]
    jar = parse_cookies(json.dumps(data))
    assert jar.count == 3 and jar.domains == ("youtube.com",)
    assert ".youtube.com\tTRUE\t/\tTRUE\t1793000000\tSID\tv1" in jar.text
    assert ".youtube.com\tTRUE\t/\tFALSE\t0\tVISITOR\tz" in jar.text
    assert jar.covers("https://youtu.be/abc")


def test_cookie_file_loads_in_yt_dlp(tmp_path):
    from yt_dlp.cookies import YoutubeDLCookieJar

    jar = parse_cookies(NETSCAPE)
    path = jar.write(tmp_path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    loaded = YoutubeDLCookieJar(str(path))
    loaded.load()
    assert {c.name for c in loaded} == {"sessionid", "ds_user_id", "ig_nrcb"}


@pytest.mark.parametrize("bad", ["", "hello world", "<html>nope</html>", "[1, 2, 3]", "{not json", "a\tb\tc"])
def test_rejects_non_cookie_text(bad):
    with pytest.raises(BadCookies):
        parse_cookies(bad)


def test_cookie_fingerprints_differ():
    a = parse_cookies(".a.com\tTRUE\t/\tTRUE\t0\tk\t1")
    b = parse_cookies(".a.com\tTRUE\t/\tTRUE\t0\tk\t2")
    assert a.fingerprint != b.fingerprint


# --------------------------------------------------------------------- posts

VIDEO = {"formats": [{"url": "https://cdn/v.mp4"}]}
PHOTO = {"formats": [], "thumbnails": [{"url": "https://cdn/small.jpg", "width": 150, "height": 150},
                                       {"url": "https://cdn/big.jpg", "width": 1440, "height": 1440},
                                       {"url": "https://cdn/mid.jpg", "width": 640, "height": 640}]}


@pytest.mark.parametrize("entries, item, duration, expected", [
    ([PHOTO, VIDEO, VIDEO], None, None, 1),                 # first video
    ([PHOTO, VIDEO, VIDEO], 3, None, 2),                    # the slide they were on
    ([PHOTO, VIDEO], 1, None, 0),                           # a photo slide, no video playing
    ([PHOTO, VIDEO], 1, 4.0, 1),                            # a video was playing: not the photo
    ([PHOTO, PHOTO], None, None, None),                     # photos only: all of them
    ([PHOTO, PHOTO], 2, None, 1),
    ([PHOTO, PHOTO], 9, None, None),                        # out of range is ignored
    ([dict(VIDEO, duration=30), dict(VIDEO, duration=8)], None, 8.4, 1),  # matched by length
    ([dict(VIDEO, duration=30), dict(VIDEO, duration=8)], 1, 8.4, 1),     # length beats a wrong slide guess
    ([dict(VIDEO, duration=30), dict(VIDEO, duration=8)], 1, 30.2, 0),
])
def test_pick_entry(entries, item, duration, expected):
    assert pick_entry(entries, item, duration) == expected


def test_is_post_only_for_fully_extracted_items():
    assert is_post({"_type": "playlist", "entries": [VIDEO, PHOTO]})
    assert not is_post({"_type": "playlist", "entries": [{"_type": "url", "url": "https://youtu.be/x"}]})
    assert not is_post({"_type": "playlist", "entries": (e for e in [VIDEO])})  # lazy: a real playlist
    assert not is_post(VIDEO)


def test_best_image_and_titles():
    assert best_image(PHOTO).url == "https://cdn/big.jpg"
    assert best_image({"thumbnail": "https://cdn/t.jpg"}).url == "https://cdn/t.jpg"
    assert best_image({}) is None
    assert photo_title("Video by nasa") == "Photo by nasa"
    assert photo_title("My trip") == "My trip"


def test_summarize_post():
    raw = {"_type": "playlist", "title": "Post by nasa", "webpage_url": "https://www.instagram.com/p/X/",
           "extractor_key": "Instagram", "entries": [PHOTO, dict(VIDEO, duration=12)]}
    s = summarize_post(raw, "https://instagram.com/p/X")
    assert s["post"] and s["count"] == 2 and (s["videos"], s["photos"]) == (1, 1)
    assert [(e["item"], e["title"], e["photo"], e["url"]) for e in s["entries"]] == [
        (1, "Photo 1", True, "https://www.instagram.com/p/X/"), (2, "Video 2", False, "https://www.instagram.com/p/X/")]
    lone = summarize_post(dict(PHOTO, title="Video by nasa"), "https://www.instagram.com/p/Y/")
    assert lone["title"] == "Photo by nasa" and lone["entries"][0]["item"] is None


def test_no_video_errors_are_plain():
    raw = ("ERROR: [Instagram] DQxyz: No video formats found!; please report this issue on "
           "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. "
           "Confirm you are on the latest version using yt-dlp -U")
    msg = friendly_error(raw)
    assert "github" not in msg and "photo" in msg
    assert "no sound" in friendly_error("ERROR: Postprocessing: WARNING: unable to obtain file audio codec with ffprobe")
    # Unknown errors still lose yt-dlp's bug-report boilerplate
    msg = friendly_error("ERROR: [site] 1: Something odd happened; please report this issue on https://github.com/x , yada")
    assert msg == "Something odd happened"


# --------------------------------------------------------------- Apple MP4s


def test_apple_codecs():
    from app.compat import apple_codec, needs_conversion
    assert apple_codec("avc1.64001F") and apple_codec("hvc1.1.6.L93") and apple_codec("h264")
    assert not apple_codec("vp09.00.40.08") and not apple_codec("av01.0.08M.08") and not apple_codec(None)
    stream = lambda kind, codec, tag="": {"codec_type": kind, "codec_name": codec, "codec_tag_string": tag}
    assert not needs_conversion({"streams": [stream("video", "h264"), stream("audio", "aac")]})
    assert not needs_conversion({"streams": [stream("video", "hevc", "hvc1"), stream("audio", "aac")]})
    assert needs_conversion({"streams": [stream("video", "hevc", "hev1")]})        # QuickTime wants hvc1
    assert needs_conversion({"streams": [stream("video", "vp9"), stream("audio", "aac")]})
    assert needs_conversion({"streams": [stream("video", "h264"), stream("audio", "opus")]})


def test_long_best_mp4_prefers_an_apple_stream():
    from types import SimpleNamespace
    from app.jobs import JobManager
    ydl = SimpleNamespace(params={"format_sort": ["res"]})
    job = SimpleNamespace(choice=DownloadChoice(mode="video", quality="best", container="mp4"))
    long_vp9 = {"duration": 3600, "vcodec": "vp09.00.51.08", "formats": [{"vcodec": "vp09"}, {"vcodec": "avc1.640028"}]}
    JobManager._prefer_apple_friendly(job, ydl, dict(long_vp9, duration=60))      # short: keep the best, convert later
    assert ydl.params["format_sort"] == ["res"]
    JobManager._prefer_apple_friendly(job, ydl, long_vp9)
    assert ydl.params["format_sort"][0] == "vcodec:h264"


# ------------------------------------------------ the Android app's engine

def test_engine_imports_without_fastapi_or_pydantic():
    """The Android app runs these modules on a Python that has neither."""
    import subprocess
    import sys
    code = ("import sys; sys.modules.update(fastapi=None, pydantic=None, starlette=None)\n"
            "import app.jobs, app.resolve, app.security, app.compat\n"
            "from app.formats import DownloadChoice\n"
            "assert DownloadChoice(quality='720p').quality == '720'\n")
    subprocess.run([sys.executable, "-c", code], check=True, cwd=str(__import__("pathlib").Path(__file__).parents[1]))


def test_tool_overrides(monkeypatch, tmp_path):
    from app import compat, ytdl
    fake = tmp_path / "libffmpeg.so"
    fake.write_text("")
    monkeypatch.setenv("GRABBIT_FFMPEG", str(fake))
    assert ytdl.ffmpeg_path() == str(fake) == compat.ffmpeg()
    monkeypatch.setenv("GRABBIT_FFMPEG", str(tmp_path / "missing"))
    assert ytdl.ffmpeg_path() is None  # an override that doesn't exist means "no ffmpeg", not PATH


def test_quickjs_runtime(monkeypatch, tmp_path):
    from pathlib import Path
    from app import ytdl
    from app.config import load_settings
    qjs = tmp_path / "libqjs.so"
    qjs.write_text("")
    for env in ("GRABBIT_DENO", "GRABBIT_NODE", "GRABBIT_BUN"):  # pretend none of the others exist
        monkeypatch.setenv(env, str(tmp_path / "none"))
    monkeypatch.setenv("GRABBIT_QJS", str(qjs))
    assert ytdl.js_runtime() == "quickjs"
    opts = ytdl.base_options(load_settings(), ytdl.YtdlLogger(), Path(tmp_path))
    assert opts["js_runtimes"] == {"quickjs": {"path": str(qjs)}}


def test_apple_compat_setting(monkeypatch):
    from app.config import load_settings
    assert load_settings().apple_compat is True
    monkeypatch.setenv("GRABBIT_APPLE_COMPAT", "false")
    assert load_settings().apple_compat is False
