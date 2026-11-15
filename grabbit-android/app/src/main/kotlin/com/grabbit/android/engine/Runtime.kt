package com.grabbit.android.engine

import android.content.Context
import android.util.Log
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Everything the Python engine needs on disk, and the environment to run it in.
 *
 * youtubedl-android supplies the native pieces: Python 3.12 (`libpython.so`), ffmpeg/ffprobe and
 * QuickJS in nativeLibraryDir, plus Python's stdlib and ffmpeg's libraries, which its init calls
 * unpack once into noBackupFilesDir. The engine itself (grabbit-web's modules + the bridge) and
 * the active yt-dlp come from this app's assets and the updater.
 */
class Runtime(private val context: Context) {
    val nativeDir = File(context.applicationInfo.nativeLibraryDir)
    private val packages = File(context.noBackupFilesDir, "youtubedl-android/packages")
    private val pythonHome = File(packages, "python/usr")
    val engineDir = File(context.filesDir, "engine")
    val workDir = File(context.noBackupFilesDir, "engine") // jobs/, yt-dlp-cache/, recovered/
    val ytdlp = YtDlpStore(context, this)

    @Volatile
    private var prepared = false

    val python get() = File(nativeDir, "libpython.so")

    /** Slow the first time (unpacks ~60 MB); call off the main thread. */
    @Synchronized
    fun prepare() {
        if (prepared) return
        val t0 = System.currentTimeMillis()
        YoutubeDL.init(context)
        FFmpeg.init(context)
        extractEngine()
        ytdlp.ensureInstalled()
        prepared = true
        Log.i(TAG, "runtime ready in ${System.currentTimeMillis() - t0} ms, yt-dlp ${ytdlp.current()}")
    }

    fun env(pythonPath: List<File>): Map<String, String> {
        val home = File(context.filesDir, "home").apply { mkdirs() }
        val tmp = File(context.cacheDir, "tmp").apply { mkdirs() }
        return mapOf(
            "LD_LIBRARY_PATH" to "$pythonHome/lib:$packages/ffmpeg/usr/lib",
            "SSL_CERT_FILE" to "$pythonHome/etc/tls/cert.pem",
            "PYTHONHOME" to pythonHome.path,
            "PYTHONPATH" to pythonPath.joinToString(":") { it.path },
            "PYTHONIOENCODING" to "utf-8",
            "PYTHONUTF8" to "1",
            "HOME" to home.path,
            "TMPDIR" to tmp.path,
            "PATH" to (System.getenv("PATH") ?: "/system/bin") + ":" + nativeDir.path,
            "GRABBIT_FFMPEG" to File(nativeDir, "libffmpeg.so").path,
            "GRABBIT_FFPROBE" to File(nativeDir, "libffprobe.so").path,
            "GRABBIT_QJS" to File(nativeDir, "libqjs.so").path,
            "GRABBIT_DOWNLOAD_DIR" to File(workDir, "jobs").path,
            // Debug builds log what yt-dlp does (extraction, JS challenges, merging) to logcat.
            "GRABBIT_LOG_LEVEL" to if (com.grabbit.android.BuildConfig.DEBUG) "DEBUG" else "INFO",
        )
    }

    /** Run a short Python snippet (unpacking yt-dlp, testing an update). Returns exit code and output. */
    fun runPython(args: List<String>, pythonPath: List<File> = emptyList(), timeoutSec: Long = 120): Pair<Int, String> {
        val pb = ProcessBuilder(listOf(python.path) + args).redirectErrorStream(true)
        pb.environment().putAll(env(pythonPath))
        val p = pb.start()
        val out = StringBuilder()
        val reader = Thread { p.inputStream.bufferedReader().forEachLine { if (out.length < 8000) out.appendLine(it) } }
        reader.start()
        if (!p.waitFor(timeoutSec, TimeUnit.SECONDS)) {
            p.destroyForcibly()
            return -1 to "timed out"
        }
        reader.join(2000)
        return p.exitValue() to out.toString().trim()
    }

    /** Copy assets/engine to files/engine when the engine's content hash changed (a new app build). */
    private fun extractEngine() {
        val assets = context.assets
        val wanted = assets.open("engine/VERSION").bufferedReader().use { it.readText().trim() }
        val have = File(engineDir, "VERSION").takeIf { it.isFile }?.readText()?.trim()
        if (wanted == have) return
        engineDir.deleteRecursively()
        fun copy(path: String, to: File) {
            val children = assets.list(path).orEmpty()
            if (children.isEmpty()) {
                to.parentFile?.mkdirs()
                assets.open(path).use { input -> to.outputStream().use { input.copyTo(it) } }
            } else {
                children.forEach { copy("$path/$it", File(to, it)) }
            }
        }
        copy("engine", engineDir)
        Log.i(TAG, "engine $wanted extracted")
    }

    companion object {
        const val TAG = "Grabbit"
    }
}
