# Grabbit for Chrome

Hover over any video, image or audio player on any website and click **Download**.

* **Images, and videos/audio that are plain files** download straight away, with no server needed.
* **Streaming videos** (YouTube, Instagram, X, TikTok, Reddit, Facebook, Vimeo and 1,800+ more) are
  sent to your [Grabbit server](../grabbit-web), which runs yt-dlp, and the finished file lands in your
  Downloads folder. A small card in the corner shows progress.
* **Signed-in content just works.** The extension passes your browser's cookies *for that one site*
  to your Grabbit server, so private, follower-only and age-restricted videos download without
  exporting anything.

**Posts with several photos and videos** (Instagram carousels): you get the slide you're looking at.
Sending a photo-only post to Grabbit saves the photos (several come as one .zip).

The **▾** next to Download offers Best quality, 720p, Audio only (MP3), or the raw file.
Right-click works too: *Download video with Grabbit*, *Download audio (MP3)*, *Download image (full size)*.
The toolbar button lists every video, image and audio file on the page, with **Download all images**.

## Install (2 minutes)

**Easiest:** install the [Grabbit desktop app](../grabbit-desktop) for Mac or Windows. It runs the server for you,
starts at login and walks you through adding the extension. Or set it up by hand:

1. Start your Grabbit server (see `../grabbit-web/README.md`), e.g. `uv run uvicorn app.main:app --port 8000`.
2. In Chrome open `chrome://extensions`, switch on **Developer mode** (top right).
3. Click **Load unpacked** and pick this `grabbit-extension` folder.
4. Pin Grabbit (puzzle-piece icon). Open it and check that it says **Server connected**.
   If your server isn't on `http://localhost:8000`, change it under **Settings**.

Works the same in Edge, Brave, Opera, Vivaldi and Arc.

## Why streaming videos need the server

Big sites don't serve one video file. They stream encrypted or scrambled chunks into the player
(that's the `blob:` address you see on YouTube or Instagram). An extension can't turn that back into a file,
but yt-dlp can. So the extension sends the *post's* link (it finds the right post in feeds and
timelines) to Grabbit, and Grabbit downloads and merges the best streams.

## Privacy

* Cookies are only read for the site of the video you click (plus its sister domain, e.g. x.com and twitter.com),
  and only sent to the Grabbit server address in Settings. Turn off **Use my logins** to never send them.
* The server uses them for that one download and deletes them right after (see the server README).
* Point the extension only at a Grabbit server you trust, ideally your own.

## Phones

Chrome for Android and iPhone don't run extensions. On a phone, use the Grabbit website itself
(add it to your home screen; on Android it then appears in the Share menu).

## Not possible

DRM-protected video (Netflix, Prime Video, Disney+, Spotify, most paid courses) can't be downloaded.
The extension tells you when it detects it.
