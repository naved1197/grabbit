# Grabbit (web)

A website for downloading video and audio from YouTube, TikTok, Instagram, X,
Reddit, Vimeo and [1,800+ other sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md),
powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp). It works in any browser
on Mac, Windows, Android and iPhone, with nothing to install.

* Paste a link (or share it to Grabbit on Android once it's installed as an app)
* Pick a quality from Best/4K down to 144p, with size estimates, as MP4, WebM or MKV
* MP4s always play on Mac, iPhone and iPad: sites' VP9/AV1 streams are converted to H.264
  (a few seconds per clip on a Mac, which has a hardware encoder)
* Or get just the audio as MP3, M4A, Opus or the original stream, with cover art and tags
* Playlists: tick the videos you want and queue them all
* Instagram posts with several photos and videos: pick the items you want (photos save as images, several at once as a .zip)
* Private, follower-only, members-only or age-restricted videos: add your own cookies (see below)
* Live progress with speed and time left, cancel, retry, and saving starts automatically
* Light and dark themes, a phone-friendly layout, and an installable PWA

**On desktop, the easiest way to use it is the Chrome extension** in `../grabbit-extension`: it puts a
Download button on every video, image and audio player, and uses your browser's logins automatically.

## How it works

```
browser ──POST /api/info──▶ FastAPI ──▶ yt-dlp (extract only, cached 20 min)
        ──POST /api/jobs──▶ worker pool ──▶ yt-dlp download ──▶ ffmpeg merge/convert
        ──GET  /api/jobs?ids=…  (progress polling)
        ──GET  /api/jobs/{id}/file  (the finished file, deleted after 60 min)
```

The server does the downloading because browsers can't run yt-dlp. The file
waits on the server until the visitor saves it, then the janitor deletes it.

## Run it locally

Needs [uv](https://docs.astral.sh/uv/) and ffmpeg (`brew install ffmpeg`, `winget install ffmpeg`, `apt install ffmpeg`).
Deno, which yt-dlp uses for YouTube, is installed automatically as a Python dependency.

```bash
uv sync
uv run uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000.

### Fix MP4s that won't play on a Mac

Files downloaded before the conversion was added may be VP9 inside an MP4, which QuickTime
can't open. This writes a "(plays on Mac)" copy next to each one and leaves the originals alone:

```bash
uv run python -m app.compat ~/Downloads/*.mp4
```

## Put it online

The app is one Docker container. Run **exactly one instance**, because
download progress is kept in memory.

**Any VPS (Hetzner, DigitalOcean, Lightsail…)**

```bash
docker compose up -d --build
```

Put Caddy or Nginx in front for HTTPS, and set `GRABBIT_TRUST_PROXY_HEADERS=true`.

**Render**: push this folder to a GitHub repo, then *New > Blueprint* (uses `render.yaml`).

**Fly.io**

```bash
fly launch --copy-config --no-deploy
fly deploy
fly scale count 1
```

**Railway / Koyeb / Google Cloud Run**: deploy the `Dockerfile`, expose port `8000`, limit to 1 instance
(Cloud Run: `--max-instances 1 --no-cpu-throttling`, since downloads run in the background).

## Videos that need you to be signed in

Errors like *"only available for registered users who follow this account"* (Instagram),
*"Sign in to confirm your age"* or *"confirm you're not a bot"* (YouTube) mean the site wants a
signed-in account. There are three ways to give yt-dlp one:

1. **Per visitor (on the page).** Click **Add cookies** (under the search box, or on the error).
   Export cookies with *Get cookies.txt LOCALLY*
   ([Chrome](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) /
   [Firefox](https://addons.mozilla.org/firefox/addon/get-cookies-txt-locally/)) while signed in to the site,
   then upload or paste the file. Netscape `cookies.txt` and JSON exports (Cookie-Editor, EditThisCookie)
   both work. The cookies stay in that visitor's browser and are sent only with their own requests.
   The server writes them to a private temp file for that one lookup or download and deletes it right
   after. They are never logged, cached for others, or sent back out. Turn this off with
   `GRABBIT_ALLOW_USER_COOKIES=false`.
2. **One cookies file for everyone** with `GRABBIT_COOKIES_FILE=/data/cookies.txt`. Use a throwaway
   account, because every visitor's downloads run as that account.
3. **Your own browser, for personal use** with `GRABBIT_COOKIES_FROM_BROWSER=chrome` (or `safari`,
   `firefox`, `edge`, `brave`, or `chrome:Profile 1`). yt-dlp reads the cookies straight from that browser
   on the machine running Grabbit. This is handy on your own Mac or PC. **Never** set it on a public server:
   every visitor would be using your accounts. On macOS, Safari needs Full Disk Access for the terminal,
   and Chrome will ask for Keychain access the first time.

Cookies expire when you sign out or after a while. If a download that used to work starts failing,
export them again. For YouTube, export from a private/incognito window and then close it, so YouTube
doesn't rotate the cookies you just exported.

## Configuration

All settings are optional environment variables.

| Variable | Default | What it does |
| --- | --- | --- |
| `GRABBIT_MAX_CONCURRENT` | `3` | Downloads running at once, server-wide |
| `GRABBIT_MAX_JOBS_PER_IP` | `20` | Unfinished downloads one visitor may queue |
| `GRABBIT_MAX_FILESIZE_MB` | `4096` | Refuse files bigger than this (0 = no limit) |
| `GRABBIT_MAX_DURATION_MIN` | `240` | Refuse videos longer than this (0 = no limit) |
| `GRABBIT_FILE_TTL_MIN` | `60` | Minutes a finished file is kept |
| `GRABBIT_PLAYLIST_LIMIT` | `50` | Max playlist items listed/queued at once |
| `GRABBIT_RATE_LIMIT_PER_MIN` | `30` | Look-ups + downloads per minute per visitor |
| `GRABBIT_ALLOW_USER_COOKIES` | `true` | Let visitors add their own cookies on the page |
| `GRABBIT_COOKIES_FILE` | none | Netscape `cookies.txt` used when a visitor hasn't added their own |
| `GRABBIT_COOKIES_FROM_BROWSER` | none | Read cookies from a browser on the server, e.g. `chrome` (personal use only) |
| `GRABBIT_PROXY` | none | Proxy for yt-dlp, e.g. `socks5://user:pass@host:1080` |
| `GRABBIT_TRUST_PROXY_HEADERS` | `false` | Read the visitor IP from the reverse proxy's headers |
| `GRABBIT_ALLOW_PRIVATE_URLS` | `false` | Allow links to private/LAN addresses (keep off when public) |
| `GRABBIT_DOWNLOAD_DIR` | `./data/downloads` | Where files wait to be saved |

## Things to know before making it public

* **YouTube blocks many cloud servers.** Requests from data-center IPs often get
  "Sign in to confirm you're not a bot". Fixes, in order of effort: visitors add their own cookies on the page, or export cookies from a
  throwaway Google account ([how](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies))
  and set `GRABBIT_COOKIES_FILE`, or route traffic through a residential proxy with `GRABBIT_PROXY`.
  Most other sites work fine from servers.
* **Keep yt-dlp current.** Sites change constantly. Bump the version in `pyproject.toml`, run `uv lock`, and redeploy.
* **Bandwidth.** Every download passes through your server twice: once from the site, once to the visitor.
  Watch your host's egress pricing, and use the size, duration and concurrency limits.
* **Legal.** Downloading can break a site's terms of service, and public download
  sites do get copyright complaints. Consider who you open it to, and add a contact
  for takedown requests.

## Safety built in

* Only `http(s)` links. Links that resolve to private, loopback or link-local addresses
  are refused (blocks SSRF against cloud metadata endpoints).
* Per-visitor rate limit and download cap, plus fair scheduling so one person's playlist can't starve others.
* Strict Content-Security-Policy. Page text from other sites is always inserted as text, never HTML.
* Download IDs are random 96-bit tokens. Files are deleted after the TTL and on cancel or failure.
* The container runs as a non-root user.

## Tests

```bash
uv run pytest                              # offline: units + full pipeline against a local test server
GRABBIT_NETWORK_TESTS=1 uv run pytest      # also downloads a real YouTube video
```
