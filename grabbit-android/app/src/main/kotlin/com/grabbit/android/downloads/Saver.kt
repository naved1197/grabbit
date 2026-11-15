package com.grabbit.android.downloads

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.os.StatFs
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.webkit.MimeTypeMap
import java.io.File
import java.io.InputStream
import java.util.zip.ZipFile

class SaveException(message: String) : Exception(message)

/** Puts finished files into Downloads/Grabbit, where the gallery, file manager and other apps find them. */
class Saver(private val context: Context) {
    data class Saved(val uris: List<Uri>, val names: List<String>, val mime: String)

    fun save(file: File): Saved {
        val free = StatFs(Environment.getExternalStorageDirectory().path).availableBytes
        if (free < file.length() + MARGIN) {
            throw SaveException("There isn't enough free space on this phone to save this (${mb(file.length())} needed).")
        }
        if (file.extension.equals("zip", ignoreCase = true)) photosIn(file)?.let { return it }
        val mime = mimeFor(file.name)
        val uri = insert(file.name, mime) { file.inputStream() }
        return Saved(listOf(uri), listOf(file.name), mime)
    }

    /** What another app handed over (a dropped image or video): its name, type and size, if it says. */
    data class Incoming(val uri: Uri, val name: String, val mime: String, val size: Long?)

    fun describe(uri: Uri): Incoming {
        val resolver = context.contentResolver
        var name: String? = null
        var size: Long? = null
        runCatching {
            resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
                if (c.moveToFirst()) {
                    c.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 && !c.isNull(it) }?.let { name = c.getString(it) }
                    c.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 && !c.isNull(it) }?.let { size = c.getLong(it) }
                }
            }
        }
        val mime = resolver.getType(uri) ?: name?.let(::mimeFor) ?: "application/octet-stream"
        val ext = MimeTypeMap.getSingleton().getExtensionFromMimeType(mime)
        val base = name?.takeIf { it.isNotBlank() }
            ?: "Grabbit ${java.text.SimpleDateFormat("yyyy-MM-dd HH.mm.ss", java.util.Locale.US).format(java.util.Date())}"
        val fileName = if (ext != null && !base.contains('.')) "$base.$ext" else base
        return Incoming(uri, fileName, mime, size)
    }

    /** Copy something another app handed over into Downloads/Grabbit. */
    fun save(item: Incoming): Saved {
        val need = item.size ?: 0L
        if (StatFs(Environment.getExternalStorageDirectory().path).availableBytes < need + MARGIN) {
            throw SaveException("There isn't enough free space on this phone to save this.")
        }
        val uri = insert(item.name, item.mime) {
            context.contentResolver.openInputStream(item.uri) ?: throw SaveException("The other app didn't hand over “${item.name}”.")
        }
        return Saved(listOf(uri), listOf(item.name), item.mime)
    }

    /** A post's photos arrive as one .zip (that's how the website hands them over); save them one by one. */
    private fun photosIn(file: File): Saved? = ZipFile(file).use { zip ->
        val entries = zip.entries().toList().filter { !it.isDirectory }
        if (entries.isEmpty() || !entries.all { mimeFor(it.name).startsWith("image/") }) return null
        val uris = entries.map { e -> insert(File(e.name).name, mimeFor(e.name)) { zip.getInputStream(e) } }
        Saved(uris, entries.map { File(it.name).name }, mimeFor(entries.first().name))
    }

    private fun insert(name: String, mime: String, open: () -> InputStream): Uri {
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, name)
            put(MediaStore.MediaColumns.MIME_TYPE, mime)
            put(MediaStore.MediaColumns.RELATIVE_PATH, "${Environment.DIRECTORY_DOWNLOADS}/Grabbit")
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: throw SaveException("Android didn't let Grabbit create “$name” in Downloads.")
        try {
            val out = resolver.openOutputStream(uri) ?: throw SaveException("Couldn't write “$name” to Downloads.")
            out.use { o -> open().use { it.copyTo(o, 1 shl 20) } }
            values.clear()
            values.put(MediaStore.MediaColumns.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
            return uri
        } catch (e: Exception) {
            runCatching { resolver.delete(uri, null, null) }
            throw if (e is SaveException) e else SaveException("Couldn't save “$name” to Downloads: ${e.message}")
        }
    }

    companion object {
        private const val MARGIN = 32L * 1024 * 1024

        fun mimeFor(name: String): String {
            val ext = name.substringAfterLast('.', "").lowercase()
            return MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext) ?: when (ext) {
                "opus", "ogg" -> "audio/ogg"
                "m4a" -> "audio/mp4"
                "mkv" -> "video/x-matroska"
                "webm" -> "video/webm"
                "mp4" -> "video/mp4"
                "mp3" -> "audio/mpeg"
                "jpg", "jpeg" -> "image/jpeg"
                "png" -> "image/png"
                "webp" -> "image/webp"
                else -> "application/octet-stream"
            }
        }

        private fun mb(bytes: Long) = "%.0f MB".format(bytes / 1_048_576.0)
    }
}
