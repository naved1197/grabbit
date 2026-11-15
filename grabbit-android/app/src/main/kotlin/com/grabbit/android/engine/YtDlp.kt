package com.grabbit.android.engine

import android.content.Context
import android.util.Log
import com.grabbit.android.settings.Prefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** Compare yt-dlp versions like 2026.10.26 and 2026.10.26.1. */
fun compareVersions(a: String, b: String): Int {
    val x = a.split('.').map { it.toIntOrNull() ?: 0 }
    val y = b.split('.').map { it.toIntOrNull() ?: 0 }
    for (i in 0 until maxOf(x.size, y.size)) {
        val c = (x.getOrElse(i) { 0 }).compareTo(y.getOrElse(i) { 0 })
        if (c != 0) return c
    }
    return 0
}

/**
 * Installed yt-dlp versions, each unpacked in files/ytdlp/<version>/ (unpacked rather than run from
 * the zip, so Python can cache compiled files). `current` is what the engine runs, `pending` a
 * tested update to switch to at the next cold start, `previous` the fallback if it won't start.
 */
class YtDlpStore(private val context: Context, private val runtime: Runtime) {
    val root = File(context.filesDir, "ytdlp")
    private val currentFile = File(root, "current")
    private val pendingFile = File(root, "pending")
    private val previousFile = File(root, "previous")

    private fun read(f: File) = f.takeIf { it.isFile }?.readText()?.trim()?.ifEmpty { null }
    private fun write(f: File, v: String?) {
        root.mkdirs()
        if (v == null) f.delete() else f.writeText(v)
    }

    fun current(): String? = read(currentFile)
    fun pending(): String? = read(pendingFile)
    fun dirFor(version: String) = File(root, version)
    fun activeDir(): File = dirFor(current() ?: error("yt-dlp isn't installed"))
    fun isInstalled(version: String) = File(dirFor(version), "yt_dlp/__init__.py").isFile

    fun bundledVersion(): String = context.assets.list("ytdlp").orEmpty()
        .first { it.startsWith("yt-dlp-") && it.endsWith(".zip") }
        .removePrefix("yt-dlp-").removeSuffix(".zip")

    @Synchronized
    fun ensureInstalled() {
        pending()?.let { p ->
            if (isInstalled(p) && p != current()) {
                write(previousFile, current())
                write(currentFile, p)
                Log.i(TAG, "switched to yt-dlp $p")
            }
            write(pendingFile, null)
        }
        val bundled = bundledVersion()
        val cur = current()
        if (cur == null || !isInstalled(cur) || compareVersions(bundled, cur) > 0) {
            if (!isInstalled(bundled)) {
                val zip = File(context.cacheDir, "yt-dlp-$bundled.zip")
                context.assets.open("ytdlp/yt-dlp-$bundled.zip").use { i -> zip.outputStream().use { i.copyTo(it) } }
                try {
                    unpack(zip, bundled)
                } finally {
                    zip.delete()
                }
            }
            if (cur != null && isInstalled(cur)) write(previousFile, cur)
            write(currentFile, bundled)
        }
        val keep = setOfNotNull(current(), pending(), read(previousFile))
        root.listFiles()?.filter { it.isDirectory && it.name !in keep }?.forEach { it.deleteRecursively() }
    }

    /** Unpack a yt-dlp zipapp (it starts with a #! line, which Python's zipfile copes with). */
    @Synchronized
    fun unpack(zip: File, version: String) {
        val tmp = File(root, "$version.tmp").apply { deleteRecursively() }
        val (code, out) = runtime.runPython(
            listOf("-c", "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])", zip.path, tmp.path)
        )
        check(code == 0 && File(tmp, "yt_dlp/__init__.py").isFile) { "Couldn't unpack yt-dlp $version: $out" }
        dirFor(version).deleteRecursively()
        check(tmp.renameTo(dirFor(version))) { "Couldn't install yt-dlp $version" }
    }

    /** The version string a candidate reports, or null if it doesn't import (with its EJS scripts). */
    fun test(version: String): String? {
        val (code, out) = runtime.runPython(
            listOf("-c", "import yt_dlp, yt_dlp_ejs; print(yt_dlp.version.__version__)"),
            listOf(dirFor(version)),
        )
        return out.lines().lastOrNull()?.trim()?.takeIf { code == 0 && it.isNotEmpty() }
    }

    @Synchronized
    fun markPending(version: String) = write(pendingFile, version)

    /** The current version won't start: go back to the previous one. */
    @Synchronized
    fun revert(): Boolean {
        val prev = read(previousFile)?.takeIf { it != current() && isInstalled(it) } ?: return false
        Log.w(TAG, "yt-dlp ${current()} failed; going back to $prev")
        write(currentFile, prev)
        write(previousFile, null)
        return true
    }

    companion object {
        const val TAG = "Grabbit"
    }
}

/** Keeps yt-dlp current like grabbit-desktop does: check at most every 12 hours, switch on a cold start. */
class YtDlpUpdater(private val store: YtDlpStore, private val prefs: Prefs) {
    sealed interface Result {
        data class UpToDate(val version: String?) : Result
        data class Ready(val version: String) : Result
        data class Failed(val message: String) : Result
        data object Skipped : Result
    }

    suspend fun check(force: Boolean = false): Result = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        if (!force && now - prefs.lastUpdateCheck < CHECK_EVERY_MS) return@withContext Result.Skipped
        prefs.lastUpdateCheck = now
        try {
            val release = EngineJson.parseToJsonElement(get(LATEST).decodeToString()).jsonObject
            val latest = release["tag_name"]?.jsonPrimitive?.contentOrNull ?: return@withContext Result.Failed("No version")
            val have = listOfNotNull(store.current(), store.pending()).maxWithOrNull(::compareVersions)
            if (have != null && compareVersions(latest, have) <= 0) return@withContext Result.UpToDate(have)

            val assets = release["assets"]!!.jsonArray.associate {
                val o = it.jsonObject
                o["name"]!!.jsonPrimitive.content to o["browser_download_url"]!!.jsonPrimitive.content
            }
            val sums = get(assets.getValue("SHA2-256SUMS")).decodeToString()
            val expected = sums.lines().firstOrNull { it.trim().endsWith(" yt-dlp") }?.trim()?.split(Regex("\\s+"))?.first()
                ?: return@withContext Result.Failed("No checksum for yt-dlp $latest")
            val bytes = get(assets.getValue("yt-dlp"))
            val actual = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
            if (!actual.equals(expected, ignoreCase = true)) return@withContext Result.Failed("yt-dlp $latest failed its checksum")

            val zip = File.createTempFile("yt-dlp-", ".zip")
            try {
                zip.writeBytes(bytes)
                store.unpack(zip, latest)
            } finally {
                zip.delete()
            }
            if (store.test(latest) == null) {
                store.dirFor(latest).deleteRecursively()
                return@withContext Result.Failed("yt-dlp $latest didn't start on this phone")
            }
            store.markPending(latest)
            Log.i(YtDlpStore.TAG, "yt-dlp $latest is ready for the next start")
            Result.Ready(latest)
        } catch (e: Exception) {
            Log.w(YtDlpStore.TAG, "yt-dlp update check failed", e)
            Result.Failed(e.message ?: e.javaClass.simpleName)
        }
    }

    private fun get(url: String): ByteArray {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 15_000
        conn.readTimeout = 60_000
        conn.setRequestProperty("User-Agent", "Grabbit-Android")
        conn.setRequestProperty("Accept", "application/vnd.github+json, */*")
        conn.instanceFollowRedirects = true
        try {
            check(conn.responseCode in 200..299) { "HTTP ${conn.responseCode} for $url" }
            return conn.inputStream.use { it.readBytes() }
        } finally {
            conn.disconnect()
        }
    }

    companion object {
        private const val LATEST = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
        private const val CHECK_EVERY_MS = 12 * 60 * 60 * 1000L
    }
}
