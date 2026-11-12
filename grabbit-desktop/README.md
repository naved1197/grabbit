# Grabbit for Mac and Windows

One installer that sets up everything the [Grabbit extension](../grabbit-extension) needs:

* Runs the [Grabbit server](../grabbit-web) on the computer at `http://localhost:8000`, which is the
  extension's default address, so there's nothing to configure.
* Ships with ffmpeg, ffprobe and deno, so nothing else needs installing.
* Lives in the menu bar (Mac) or system tray (Windows) and starts by itself at login.
* Keeps yt-dlp up to date on its own. A newer yt-dlp is downloaded from PyPI in the
  background and used after the next quiet moment (it restarts itself when no download is running).
* Opens a setup page that walks through adding the extension and turns green once it's installed.

**The one manual step.** Chrome, Edge and Brave don't let any program install an extension unless
it comes from the Chrome Web Store (and Grabbit wouldn't be accepted there). So the person
drags the extension folder onto the browser's Extensions page once. The setup page shows them how.

## Sharing it: what to tell people

**Mac** (`Grabbit-1.0.0-mac-apple-silicon.dmg`, or `-intel` for older Macs)

1. Open the .dmg and drag **Grabbit** into **Applications**.
2. Open Grabbit from Applications. macOS says it can't check it for malware, because it isn't
   signed with a paid Apple developer account. Click **Done**, then open
   **System Settings › Privacy & Security**, scroll down and click **Open Anyway** next to
   "Grabbit was blocked". Confirm with your password. This is only needed the first time.
3. A setup page opens in the browser. Follow its three steps.

**Windows** (`Grabbit-Setup-1.0.0.exe`)

1. Run the installer. If Windows says "Windows protected your PC", click **More info** and then
   **Run anyway** (the installer isn't signed with a paid certificate).
2. Click **Finish** with "Open Grabbit" ticked. A setup page opens. Follow its three steps.

**The three steps** (the same on both):

1. Click **Copy address & open Chrome**, then paste into Chrome's address bar and press Enter.
2. Turn on **Developer mode** on the Extensions page.
3. Click **Show the folder** and drag **Grabbit Extension** onto the Extensions page.

The page then says **You're all set**. After that, the person only ever sees the Download buttons.

## Building the installers

The easiest way is GitHub Actions: put this whole `Grabbit` folder in a GitHub repository, open
**Actions › Desktop app › Run workflow**, and download the three installers when it finishes.
Pushing a tag like `v1.0.0` also publishes them as a GitHub Release. See `.github/workflows/desktop.yml`.

To build on your own computer you need [uv](https://docs.astral.sh/uv/):

```bash
./packaging/build_mac.sh          # on a Mac: dist/Grabbit-<version>-mac-<arch>.dmg
```

```powershell
.\packaging\build_windows.ps1     # on Windows, with Inno Setup 6: dist\Grabbit-Setup-<version>.exe
```

A Mac build only runs on Macs with the same kind of processor (Apple Silicon or Intel). The workflow builds both.

Both scripts start with `packaging/fetch_tools.py`, which downloads ffmpeg/ffprobe builds pinned by
URL and SHA-256 (Martin Riedl's signed builds for macOS, gyan.dev's for Windows) and copies deno
from the project's venv. To move to a newer ffmpeg, change the URLs and hashes there.

**Before releasing a new version:** bump `VERSION` in `grabbit_desktop/__init__.py` and `version` in
`pyproject.toml`, and keep the `fastapi`/`uvicorn`/`yt-dlp` pins in `pyproject.toml` the same as
`../grabbit-web/pyproject.toml`. Installing a new version over an old one keeps the person's setup,
and the old copy hands over to the new one by itself.

### Getting rid of the security warnings (optional, paid)

* **Mac:** with an Apple Developer ID ($99/year), sign with `codesign --options runtime`, notarize
  with `xcrun notarytool submit … --wait` and staple the .dmg. macOS then opens Grabbit without the
  Privacy & Security step. deno needs the `com.apple.security.cs.allow-jit` entitlement
  under the hardened runtime.
* **Windows:** sign `Grabbit.exe` and the installer with a code-signing certificate (`signtool`).
  SmartScreen warnings fade once a certificate builds reputation.

## How it works

```
Grabbit.app / Grabbit.exe
 ├─ uvicorn on 127.0.0.1:8000
 │    ├─ /desktop/…   setup page, status, "hello" from the extension (grabbit_desktop/server.py)
 │    └─ /…           the Grabbit web app and API, unchanged (../grabbit-web/app)
 ├─ menu bar / tray icon (pystray)
 ├─ copies the extension to a fixed folder the browser loads it from
 └─ yt-dlp updater (PyPI wheels, SHA-256 checked, applied at next start)
```

* The server only answers requests addressed to `localhost`/`127.0.0.1` (blocks DNS rebinding).
  Setup actions only accept requests from the setup page, and "quit" needs a token that only
  this user's other Grabbit copy can read.
* When installed or reloaded, the extension POSTs to `/desktop/hello`. That's how the setup page knows it's done.
  Ordinary Grabbit servers don't have that route and answer 404, which the extension ignores.
* Finished files are staged in the data folder and saved to Downloads by the browser, then deleted.

**Data folder:** `~/Library/Application Support/Grabbit` (Mac) or `%LOCALAPPDATA%\Grabbit` (Windows).
It holds the `Grabbit Extension` folder, staged downloads, yt-dlp updates and `grabbit.log`.

**Uninstall:** on Windows use *Settings › Apps* (this removes the data folder too). On a Mac, quit
Grabbit from the menu bar, delete it from Applications, and delete the data folder and
`~/Library/LaunchAgents/com.grabbit.desktop.plist`. Then remove the extension in the browser.

## Developing

```bash
uv sync
uv run pytest
GRABBIT_DESKTOP_PORT=18765 GRABBIT_DESKTOP_HOME=/tmp/grabbit-dev uv run python -m grabbit_desktop
```

| Variable | What it does |
| --- | --- |
| `GRABBIT_DESKTOP_PORT` | Use another port (the extension copy is patched to match) |
| `GRABBIT_DESKTOP_HOME` | Use another data folder |
| `GRABBIT_DESKTOP_NO_AUTOSTART` | Never add a login item (for testing a packaged build) |
| `GRABBIT_DESKTOP_NO_DIALOGS` | Log errors instead of showing dialogs |
| `GRABBIT_ALLOW_MISSING_TOOLS` | Let PyInstaller build without ffmpeg (test builds only, never share these) |

Running from source never adds a login item. The server's own `GRABBIT_*` settings
(see `../grabbit-web/README.md`) still work. The desktop app only changes their defaults.
