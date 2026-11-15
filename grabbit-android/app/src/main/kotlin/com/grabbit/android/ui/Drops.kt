package com.grabbit.android.ui

import android.app.Activity
import android.content.ClipData
import android.content.ClipDescription
import android.net.Uri
import android.util.Log
import android.view.DragEvent
import androidx.activity.compose.LocalActivity
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.draganddrop.dragAndDropTarget
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Download
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draganddrop.DragAndDropEvent
import androidx.compose.ui.draganddrop.DragAndDropTarget
import androidx.compose.ui.draganddrop.mimeTypes
import androidx.compose.ui.draganddrop.toAndroidDragEvent
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.app.ActivityCompat

/** What a drop carried: files to save, or a link to look up. */
sealed interface Dropped {
    data class Files(val uris: List<Uri>) : Dropped
    data class Link(val url: String) : Dropped
}

object Drops {
    /** Mime types worth a drop zone: media to save, text that may hold a link. */
    fun accepts(mime: String) = mime.startsWith("image/") || mime.startsWith("video/") ||
        mime.startsWith("audio/") || mime.startsWith("text/") || mime == ClipDescription.MIMETYPE_TEXT_URILIST

    /** Files win over text: dragging an image out of Chrome brings the picture, not just its address. */
    fun read(clip: ClipData, text: (ClipData.Item) -> CharSequence?): Dropped? {
        val items = (0 until clip.itemCount).map { clip.getItemAt(it) }
        val files = items.mapNotNull { it.uri }.filter { it.scheme == "content" || it.scheme == "file" }
        if (files.isNotEmpty()) return Dropped.Files(files)
        val web = items.mapNotNull { it.uri }.firstOrNull { it.scheme == "http" || it.scheme == "https" }
        if (web != null) return Dropped.Link(web.toString())
        val link = Fmt.extractUrl(items.joinToString(" ") { text(it)?.toString().orEmpty() })
        return link?.let { Dropped.Link(it) }
    }
}

/**
 * Makes [content] a drop zone. With Grabbit next to another app (split screen, a pop-up window, or the
 * taskbar on tablets and foldables), drag an image or video onto it to save it, or a link to look it up.
 * [onFiles] must call `release` once it has read the files (that ends the other app's read permission).
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun DropZone(
    onLink: (String) -> Unit,
    onFiles: (uris: List<Uri>, release: () -> Unit) -> Unit,
    content: @Composable () -> Unit,
) {
    val activity = LocalActivity.current
    var dragging by remember { mutableStateOf(false) }
    var over by remember { mutableStateOf(false) }
    val target = remember(activity) {
        object : DragAndDropTarget {
            override fun onStarted(event: DragAndDropEvent) { dragging = true }
            override fun onEntered(event: DragAndDropEvent) { over = true }
            override fun onExited(event: DragAndDropEvent) { over = false }
            override fun onEnded(event: DragAndDropEvent) {
                dragging = false
                over = false
            }

            override fun onDrop(event: DragAndDropEvent): Boolean {
                dragging = false
                over = false
                return handle(activity, event.toAndroidDragEvent(), onLink, onFiles)
            }
        }
    }
    Box(
        Modifier.fillMaxSize().dragAndDropTarget(
            shouldStartDragAndDrop = { ev -> ev.mimeTypes().any(Drops::accepts) },
            target = target,
        )
    ) {
        content()
        if (dragging) DropOverlay(over)
    }
}

private fun handle(activity: Activity?, e: DragEvent, onLink: (String) -> Unit, onFiles: (List<Uri>, () -> Unit) -> Unit): Boolean {
    val clip = e.clipData ?: return false
    // Reading another app's content:// files needs the permission the drag carries.
    val perms = if (activity != null) runCatching { ActivityCompat.requestDragAndDropPermissions(activity, e) }.getOrNull() else null
    val dropped = Drops.read(clip) { item -> if (activity != null) item.coerceToText(activity) else item.text }
    Log.i("Grabbit", "drop: ${clip.description.let { d -> (0 until d.mimeTypeCount).joinToString { d.getMimeType(it) } }} -> ${dropped?.javaClass?.simpleName}")
    return when (dropped) {
        is Dropped.Files -> {
            onFiles(dropped.uris) { perms?.release() }
            true
        }
        is Dropped.Link -> {
            perms?.release()
            onLink(dropped.url)
            true
        }
        null -> {
            perms?.release()
            false
        }
    }
}

@Composable
private fun DropOverlay(over: Boolean) {
    val colors = MaterialTheme.colorScheme
    Box(
        Modifier.fillMaxSize().background(colors.background.copy(alpha = if (over) 0.94f else 0.86f)).padding(16.dp)
            .drawBehind {
                drawRoundRect(
                    color = colors.primary,
                    cornerRadius = CornerRadius(28.dp.toPx()),
                    style = Stroke(
                        width = (if (over) 4 else 2).dp.toPx(),
                        pathEffect = if (over) null else PathEffect.dashPathEffect(floatArrayOf(28f, 18f)),
                    ),
                )
            },
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.padding(32.dp)) {
            Icon(Icons.Rounded.Download, contentDescription = null, tint = colors.primary, modifier = Modifier.size(52.dp))
            Text(if (over) "Let go to save it" else "Drop it on Grabbit", style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold, color = colors.onBackground)
            Text(
                "Images and videos go to Downloads/Grabbit. Links are looked up, as if you'd pasted them.",
                style = MaterialTheme.typography.bodyMedium, color = colors.onSurfaceVariant, textAlign = TextAlign.Center,
            )
        }
    }
}
