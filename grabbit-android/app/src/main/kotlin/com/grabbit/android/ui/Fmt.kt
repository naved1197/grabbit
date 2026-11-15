package com.grabbit.android.ui

import com.grabbit.android.downloads.DownloadRecord
import com.grabbit.android.engine.Choice
import com.grabbit.android.engine.Status
import kotlin.math.roundToInt
import kotlin.math.roundToLong

/** Text the screens show, worded like the website (grabbit-web/app/static/app.js). */
object Fmt {
    private val URL = Regex("""https?://[^\s<>"']+""", RegexOption.IGNORE_CASE)
    private val WWW = Regex("""(?<![\w/])www\.[^\s<>"']+""", RegexOption.IGNORE_CASE)

    /** The first link in shared or pasted text ("Check this out https://youtu.be/…"). */
    fun extractUrl(text: String?): String? {
        if (text.isNullOrBlank()) return null
        val m = URL.find(text)?.value ?: WWW.find(text)?.value?.let { "https://$it" } ?: return null
        return m.trimEnd('.', ',', ')', ']', '!', '?', ';', ':')
    }

    fun size(bytes: Double?): String? {
        if (bytes == null || bytes <= 0) return null
        val kb = bytes / 1024
        val mb = kb / 1024
        val gb = mb / 1024
        return when {
            gb >= 1 -> "%.1f GB".format(gb)
            mb >= 100 -> "${mb.roundToInt()} MB"
            mb >= 1 -> "%.1f MB".format(mb).replace(".0 MB", " MB")
            kb >= 1 -> "${kb.roundToInt()} KB"
            else -> "${bytes.roundToInt()} B"
        }
    }

    fun duration(seconds: Double?): String? {
        if (seconds == null || seconds <= 0) return null
        val s = seconds.roundToLong()
        val h = s / 3600
        val m = (s % 3600) / 60
        val sec = s % 60
        return if (h > 0) "%d:%02d:%02d".format(h, m, sec) else "%d:%02d".format(m, sec)
    }

    fun eta(seconds: Double?): String? {
        if (seconds == null || seconds < 0) return null
        return when {
            seconds < 60 -> "${seconds.roundToInt()}s left"
            seconds < 3600 -> "${(seconds / 60).roundToInt()} min left"
            else -> "%.1f h left".format(seconds / 3600)
        }
    }

    private val SITES = mapOf(
        "youtube" to "YouTube", "youtubetab" to "YouTube", "twitter" to "X / Twitter", "instagram" to "Instagram",
        "tiktok" to "TikTok", "facebook" to "Facebook", "reddit" to "Reddit", "vimeo" to "Vimeo", "twitch" to "Twitch",
        "soundcloud" to "SoundCloud", "generic" to "Web", "dailymotion" to "Dailymotion", "bilibili" to "Bilibili",
    )

    fun siteName(site: String?): String? {
        if (site.isNullOrBlank()) return null
        val key = site.lowercase().removeSuffix("ie")
        return SITES[key] ?: SITES.entries.firstOrNull { key.startsWith(it.key) }?.value ?: site
    }

    fun formatLabel(choice: Choice): String = when {
        choice.mode == "audio" && choice.audioFormat == "best" -> "original"
        choice.mode == "audio" -> choice.audioFormat.uppercase()
        else -> choice.container.uppercase()
    }

    private val PART = Regex("""Downloading (video|audio) \((\d+) of (\d+)\)""")

    /** One line under a download, like the website's "Your downloads" list. */
    fun statusLine(r: DownloadRecord): String = when (r.status) {
        Status.QUEUED -> "Waiting in line" + (r.queuePosition?.let { " · #$it" } ?: "")
        Status.STARTING -> r.stage ?: "Getting video details"
        Status.DOWNLOADING -> {
            val parts = mutableListOf<String>()
            r.progress?.let { parts += "${it.roundToInt()}%" }
            PART.find(r.stage.orEmpty())?.let { m -> parts += "${m.groupValues[1]} ${m.groupValues[2]}/${m.groupValues[3]}" }
            val done = size(r.downloaded)
            val total = size(r.total)
            when {
                done != null && total != null -> parts += "$done of $total"
                done != null -> parts += done
            }
            size(r.speed)?.let { parts += "$it/s" }
            eta(r.eta)?.let { parts += it }
            if (parts.isEmpty()) r.stage ?: "Downloading" else parts.joinToString(" · ")
        }
        Status.PROCESSING -> r.stage ?: "Finishing up"
        Status.FINISHED -> if (!r.saved) "Saving to Downloads" else listOfNotNull(
            if (r.uris.size > 1) "Saved ${r.uris.size} photos" else "Saved",
            size(r.filesize),
            formatLabel(r.choice).takeIf { r.uris.size <= 1 && r.filename?.substringAfterLast('.')?.lowercase() !in IMAGES },
        ).joinToString(" · ")
        Status.ERROR -> "Failed"
        Status.CANCELED -> "Canceled"
        Status.INTERRUPTED -> "Interrupted"
        else -> r.stage ?: r.status
    }

    private val IMAGES = setOf("jpg", "jpeg", "png", "webp", "gif", "heic", "avif")

    /** The download button: "Download MP4 · 1080p", "Download MP3", "Download audio". */
    fun downloadButton(choice: Choice): String = when {
        choice.mode == "audio" && choice.audioFormat == "best" -> "Download audio"
        choice.mode == "audio" -> "Download ${choice.audioFormat.uppercase()}"
        choice.quality == "best" -> "Download ${choice.container.uppercase()}"
        else -> "Download ${choice.container.uppercase()} · ${choice.quality}p"
    }

    /**
     * The engine's messages are written for the website (cookies files, "this server"). Reword the
     * few that read oddly on a phone; sign-in hints are added by the screen itself.
     */
    fun engineMessage(message: String, code: String?): String {
        var m = message
        if (code == "login_required") {
            m = m.substringBefore(" Add cookies from").substringBefore(" The cookies you added")
        }
        return m.replace("this server's", "Grabbit's")
            .replace("rate-limiting this server", "rate-limiting this phone's connection")
            .replace("in the server's country", "in your country")
            .replace(" Open it on the home page to pick videos.", "")
    }
}
