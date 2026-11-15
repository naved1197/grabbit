# Grabbit for Android

Download videos, audio and photos on an Android phone (Android 10 or later). In YouTube, Instagram, TikTok, X,
Chrome or any other app, tap **Share → Grabbit**. A sheet opens over the app: pick a quality and tap
**Download**. The file goes to **Downloads/Grabbit**, and a notification shows progress.

* The same choices as the website: Best down to 144p as MP4, WebM or MKV, or audio only as MP3, M4A, Opus or the
  original stream, with cover art and tags.
* Playlists and multi-photo posts: tick the items you want.
* Images and other files: in Chrome, long-press an image › **Share image** › **Save with Grabbit**. Any image,
  video or audio shared from Photos, Files or a gallery is saved to Downloads/Grabbit the same way.
* Drag and drop: with Grabbit next to another app (split screen, a pop-up window, or the taskbar on tablets
  and foldables), drop an image or video on it to save it to Downloads/Grabbit, or a link to look it up. This
  only works with apps that let things be dragged out: Files, Google Photos and most Google apps, and Chrome on
  tablets and foldables. Instagram, YouTube and TikTok don't, so use Share → Grabbit there.
* Private, follower-only and age-restricted videos: sign in to Instagram, X, TikTok, Facebook, Reddit or Vimeo
  once in **Settings › Sign-ins**. That site's sign-in then goes with its downloads, as the extension does on a
  computer.
* Downloads keep going with the screen off. If Android stops the app midway, the download shows
  **Interrupted · Retry**.
* yt-dlp updates itself (a check at most twice a day), so sites keep working.

It isn't on Google Play: Play doesn't allow YouTube downloaders. It's distributed as an APK on GitHub Releases.

## How it works

```
 Share sheet / main screen (Jetpack Compose)        DownloadService (foreground, wake lock, notifications)
        │                                                     │
        └──────────── Engine.kt ──────────────────────────────┘
                         │  JSON lines over stdin/stdout
                         ▼
   Python 3.12: engine/grabbit_engine.py + grabbit-web/app/*.py + yt-dlp (unpacked, self-updating)
                         │
                         ▼
   ffmpeg, ffprobe, QuickJS  (native libraries from youtubedl-android)
```

The phone runs **grabbit-web's own download engine**. `resolve.py`, `jobs.py`, `formats.py` and the rest are
copied into the APK at build time, so the website, the desktop app and the phone all pick formats, name files
and word errors the same way. Python, ffmpeg and QuickJS come from
[youtubedl-android](https://github.com/yausername/youtubedl-android). It's GPL-3.0, so the APK is distributed
under the GPL v3. Grabbit's own code stays MIT.

* `engine/grabbit_engine.py`: the bridge. One long-lived Python process that answers `lookup`, `submit`,
  `batch`, `cancel` and `remove` requests, and streams job progress. Because it stays running, a link's
  YouTube JavaScript challenge is solved once, at lookup (about 15 s on a Pixel 6a and 26 s on a Moto G52),
  and the download reuses the result.
* `app/src/main/kotlin/com/grabbit/android/`
  * `engine/`: unpacks the runtime, starts and talks to the engine, and the yt-dlp updater.
  * `downloads/`: the foreground service, saving to Downloads through MediaStore, notifications, and the
    download list.
  * `ui/`: the share sheet, the main screen and settings.
  * `signin/`: in-app sign-in (WebView cookies).
* The engine runs with `GRABBIT_APPLE_COMPAT=false`. Phones play VP9 and AV1, and converting to H.264 in
  software would take about as long as the video lasts.

## Build

You need JDK 17 and an Android SDK with platform 36 and build-tools 35 or 36 (Unity's bundled SDK works).
Point `local.properties` at the SDK (`sdk.dir=…`), then:

```bash
./gradlew :app:testDebugUnitTest :app:assembleDebug
```

```bash
adb install -r app/build/outputs/apk/debug/app-arm64-v8a-debug.apk
```

The build downloads the pinned yt-dlp release and checks its SHA-256 (`ytdlpVersion` in `app/build.gradle.kts`).
Keep that version the same as `grabbit-web/pyproject.toml`. Release builds are shrunk with R8 and come out at
about 61 MB for arm64.

Engine tests run on the Mac with the phone's Python version, from the repository root:

```bash
uv run --no-project --python 3.12 --with yt-dlp==2026.10.26 --with pytest pytest grabbit-android/engine/tests -q
```

### Releases

Pushing a version tag such as `v1.2.0` makes the [release workflow](../.github/workflows/desktop.yml) run the
engine and unit tests and build signed APKs, versioned from the tag (`1.2.3` becomes versionCode 10203). It
attaches them to the GitHub Release next to the desktop installers:

* `Grabbit-<version>-android.apk` for arm64, which is nearly every phone
* `-android-32bit.apk` for older armeabi-v7a phones
* `-android-x86_64.apk` for Chromebooks and emulators

**One-time setup: the signing key.** Run this on your own computer:

```bash
grabbit-android/packaging/make_release_key.sh
```

It creates `~/.grabbit/grabbit-release.jks` with `keytool`, then stores it as the `ANDROID_KEYSTORE_BASE64` and
`ANDROID_KEYSTORE_PASSWORD` repository secrets with `gh`. Both tools ask you for the password; it isn't written
anywhere else. **Back up the .jks file and its password.** Every update must be signed with the same key. If it's
lost, people have to uninstall Grabbit before they can install a newer version.

To sign a release build locally, set `GRABBIT_KEYSTORE` (the .jks path) and `GRABBIT_KEYSTORE_PASSWORD`. Without
them, `assembleRelease` makes unsigned APKs. Debug builds are signed with the computer's own debug key, so to
switch a phone from a debug build to a release, uninstall it first.

### Testing on a phone

Debug builds log what yt-dlp does under the tags `Grabbit` and `Grabbit.engine`, and accept a link from the
computer:

```bash
adb shell am start -n com.grabbit.android/.ui.MainActivity --es url "https://youtu.be/UXqq0ZvbOnk" --ez auto true
```

```bash
adb logcat -s Grabbit Grabbit.engine
```

`auto` downloads with the saved defaults. To try the share sheet, send it a link the way other apps do:

```bash
adb shell am start -n com.grabbit.android/.ui.ShareActivity -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT "https://youtu.be/UXqq0ZvbOnk"
```
