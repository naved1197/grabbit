package com.grabbit.android

import com.grabbit.android.downloads.DownloadRecord
import com.grabbit.android.engine.Choice
import com.grabbit.android.engine.EngineJson
import com.grabbit.android.engine.Info
import com.grabbit.android.engine.Job
import com.grabbit.android.engine.Status
import com.grabbit.android.engine.compareVersions
import com.grabbit.android.signin.SignIn
import com.grabbit.android.ui.Fmt
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FmtTest {
    @Test
    fun extractsTheLinkFromSharedText() {
        assertEquals("https://youtu.be/abc123", Fmt.extractUrl("Watch this! https://youtu.be/abc123"))
        assertEquals("https://www.instagram.com/reel/X/", Fmt.extractUrl("https://www.instagram.com/reel/X/"))
        assertEquals("https://www.tiktok.com/@a/video/1", Fmt.extractUrl("Look www.tiktok.com/@a/video/1."))
        assertEquals("https://x.com/a/status/1", Fmt.extractUrl("(https://x.com/a/status/1)"))
        assertNull(Fmt.extractUrl("no link here"))
        assertNull(Fmt.extractUrl(null))
    }

    @Test
    fun sizesDurationsAndEtas() {
        assertEquals("42 MB", Fmt.size(42.0 * 1024 * 1024))
        assertEquals("1.5 MB", Fmt.size(1.5 * 1024 * 1024))
        assertEquals("120 MB", Fmt.size(120.0 * 1024 * 1024))
        assertEquals("2.0 GB", Fmt.size(2.0 * 1024 * 1024 * 1024))
        assertNull(Fmt.size(null))
        assertEquals("4:23", Fmt.duration(263.0))
        assertEquals("1:02:03", Fmt.duration(3723.0))
        assertEquals("45s left", Fmt.eta(45.0))
        assertEquals("3 min left", Fmt.eta(180.0))
    }

    @Test
    fun statusLinesReadLikeTheWebsite() {
        val base = DownloadRecord(id = "j", url = "u")
        assertEquals("Waiting in line · #2", Fmt.statusLine(base.copy(status = Status.QUEUED, queuePosition = 2)))
        assertEquals(
            "42% · video 1/2 · 12 MB of 80 MB · 3.1 MB/s · 45s left",
            Fmt.statusLine(base.copy(status = Status.DOWNLOADING, progress = 42.0, stage = "Downloading video (1 of 2)",
                downloaded = 12.0 * 1048576, total = 80.0 * 1048576, speed = 3.1 * 1048576, eta = 45.0)),
        )
        assertEquals("Merging video and audio", Fmt.statusLine(base.copy(status = Status.PROCESSING, stage = "Merging video and audio")))
        assertEquals("Saving to Downloads", Fmt.statusLine(base.copy(status = Status.FINISHED)))
        assertEquals("Saved · 42 MB · MP4",
            Fmt.statusLine(base.copy(status = Status.FINISHED, uris = listOf("content://x"), filesize = 42.0 * 1048576, filename = "a.mp4")))
        assertEquals("Saved · original",
            Fmt.statusLine(base.copy(status = Status.FINISHED, uris = listOf("content://x"), choice = Choice(mode = "audio", audioFormat = "best"))))
        assertEquals("Interrupted", Fmt.statusLine(base.copy(status = Status.INTERRUPTED)))
    }

    @Test
    fun downloadButtonLabels() {
        assertEquals("Download MP4", Fmt.downloadButton(Choice()))
        assertEquals("Download MP4 · 1080p", Fmt.downloadButton(Choice(quality = "1080")))
        assertEquals("Download MP3", Fmt.downloadButton(Choice(mode = "audio")))
        assertEquals("Download audio", Fmt.downloadButton(Choice(mode = "audio", audioFormat = "best")))
    }

    @Test
    fun engineMessagesLoseWebsiteOnlyHints() {
        assertEquals(
            "Only signed-in followers of this account can see this.",
            Fmt.engineMessage("Only signed-in followers of this account can see this. Add cookies from a browser where you're signed in to instagram.com and try again.", "login_required"),
        )
        assertEquals("This video isn't available in your country.", Fmt.engineMessage("This video isn't available in the server's country.", null))
        assertEquals("YouTube", Fmt.siteName("Youtube"))
        assertEquals("X / Twitter", Fmt.siteName("Twitter"))
        assertEquals("Web", Fmt.siteName("generic"))
    }

    @Test
    fun cookieRowsAreSortedJsonForTheWholeSite() {
        val json = SignIn.rows(mapOf("x.com" to "b=2; a=1", "twitter.com" to null))!!
        val rows = EngineJson.parseToJsonElement(json).jsonArray.map { it.jsonObject }
        assertEquals(listOf("a", "b"), rows.map { it["name"]!!.jsonPrimitive.content })
        assertEquals(".x.com", rows[0]["domain"]!!.jsonPrimitive.content)
        assertEquals("1", rows[0]["value"]!!.jsonPrimitive.content)
        // Same input, same text: the engine's info cache is keyed on it.
        assertEquals(json, SignIn.rows(mapOf("twitter.com" to null, "x.com" to "a=1; b=2")))
        assertNull(SignIn.rows(mapOf("x.com" to null)))
        assertEquals("Instagram", SignIn.siteFor("https://www.instagram.com/reel/abc/")?.name)
        assertEquals("X", SignIn.siteFor("https://twitter.com/a/status/1")?.name)
        assertNull(SignIn.siteFor("https://www.youtube.com/watch?v=1"))
        // Just visiting Instagram sets csrftoken/mid; only sessionid means signed in.
        assertTrue(!SignIn.hasSession(mapOf("instagram.com" to "csrftoken=a; mid=b"), listOf("sessionid")))
        assertTrue(SignIn.hasSession(mapOf("instagram.com" to "csrftoken=a; sessionid=123%3Aabc"), listOf("sessionid")))
        assertTrue(!SignIn.hasSession(mapOf("instagram.com" to "sessionid="), listOf("sessionid")))
    }

    @Test
    fun parsesTheEnginesJson() {
        val info = EngineJson.decodeFromString(Info.serializer(), """
            {"kind":"video","id":"x","title":"CHARGE","uploader":"Blender Studio","duration":263.0,"site":"Youtube",
             "qualities":[{"value":"1080","height":804,"label":"1080p","badge":"HD","size":44148389.5}],
             "has_video":true,"has_audio":true,"audio_size":4000000,"is_live":false,"new_field":1}
        """.trimIndent())
        assertEquals("1080", info.qualities.single().value)
        assertTrue(!info.isPlaylist)
        val job = EngineJson.decodeFromString(Job.serializer(), """
            {"id":"a","status":"downloading","progress":12.5,"eta":30,"total":1234.5,"queue_position":null,"path":null}
        """.trimIndent())
        assertEquals(12.5, job.progress!!, 0.0)
        assertEquals(1234.5, job.total!!, 0.0)
    }

    @Test
    fun ytDlpVersionsCompareNumerically() {
        assertTrue(compareVersions("2026.10.26", "2026.9.30") > 0)
        assertTrue(compareVersions("2026.10.26.1", "2026.10.26") > 0)
        assertEquals(0, compareVersions("2026.10.26", "2026.10.26"))
    }

    @Test
    fun recordsFollowTheEngine() {
        val r = DownloadRecord(id = "a", url = "u", title = "mine")
            .merge(Job(id = "a", status = "finished", filename = "x.mp4", filesize = 10.0))
        assertEquals("mine", r.title)
        assertTrue(r.busy) // finished but not saved yet
        assertTrue(!r.copy(uris = listOf("content://1")).busy)
    }
}
