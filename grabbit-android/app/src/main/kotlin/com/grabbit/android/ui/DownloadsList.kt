package com.grabbit.android.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.MoreVert
import androidx.compose.material.icons.rounded.MusicNote
import androidx.compose.material.icons.rounded.Photo
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.grabbit.android.downloads.DownloadRecord
import com.grabbit.android.engine.Status

@Composable
fun DownloadRow(
    r: DownloadRecord,
    onCancel: () -> Unit,
    onRetry: () -> Unit,
    onRemove: () -> Unit,
) {
    val context = LocalContext.current
    var menu by remember { mutableStateOf(false) }
    val active = Status.isActive(r.status)
    val placeholder = when {
        r.mime?.startsWith("image/") == true -> Icons.Rounded.Photo
        r.choice.mode == "audio" -> Icons.Rounded.MusicNote
        else -> androidx.compose.material.icons.Icons.Rounded.PlayArrow
    }
    Column(
        Modifier.fillMaxWidth().clickable(enabled = r.saved) { FileActions.open(context, r) }.padding(vertical = 10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Thumbnail(r.thumbnail, 80.dp, 45.dp, Fmt.duration(r.duration), placeholder)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(r.title ?: r.filename ?: r.url, style = MaterialTheme.typography.bodyLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(Fmt.statusLine(r), style = MaterialTheme.typography.bodySmall,
                    color = if (r.status == Status.ERROR) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (r.status == Status.ERROR && r.error != null) {
                    Text(r.error, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 3, overflow = TextOverflow.Ellipsis)
                }
            }
            when {
                active -> IconButton(onClick = onCancel) { Icon(Icons.Rounded.Close, contentDescription = "Cancel") }
                r.canRetry -> IconButton(onClick = onRetry) { Icon(Icons.Rounded.Refresh, contentDescription = "Retry") }
            }
            Box {
                IconButton(onClick = { menu = true }) { Icon(Icons.Rounded.MoreVert, contentDescription = "More") }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    if (r.saved) {
                        DropdownMenuItem(text = { Text("Open") }, onClick = { menu = false; FileActions.open(context, r) })
                        DropdownMenuItem(text = { Text("Share") }, onClick = { menu = false; FileActions.share(context, r) })
                    }
                    if (r.url.isNotBlank()) {
                        DropdownMenuItem(text = { Text("Copy link") }, onClick = { menu = false; FileActions.copyLink(context, r.url) })
                    }
                    DropdownMenuItem(
                        text = { Text(if (active) "Cancel and remove" else "Remove from list") },
                        onClick = { menu = false; onRemove() },
                    )
                }
            }
        }
        if (r.busy) {
            val p = r.progress
            if (r.status == Status.DOWNLOADING && p != null) {
                LinearProgressIndicator({ (p / 100).toFloat() }, Modifier.fillMaxWidth().padding(top = 8.dp))
            } else {
                LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 8.dp))
            }
        }
    }
}

object FileActions {
    fun open(context: Context, r: DownloadRecord) {
        val uri = r.uris.firstOrNull()?.let(Uri::parse) ?: return
        val intent = Intent(Intent.ACTION_VIEW).setDataAndType(uri, r.mime).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        try {
            context.startActivity(intent)
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(context, "No app on this phone can open this file.", Toast.LENGTH_SHORT).show()
        }
    }

    fun share(context: Context, r: DownloadRecord) {
        val uris = r.uris.map(Uri::parse)
        if (uris.isEmpty()) return
        val send = if (uris.size == 1) {
            Intent(Intent.ACTION_SEND).putExtra(Intent.EXTRA_STREAM, uris[0])
        } else {
            Intent(Intent.ACTION_SEND_MULTIPLE).putParcelableArrayListExtra(Intent.EXTRA_STREAM, ArrayList(uris))
        }
        send.setType(r.mime ?: "*/*").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        context.startActivity(withoutGrabbit(context, Intent.createChooser(send, null)))
    }

    /** Sharing a saved file shouldn't offer "Save with Grabbit" (it would only make a copy). */
    fun withoutGrabbit(context: Context, chooser: Intent): Intent = chooser.putExtra(
        Intent.EXTRA_EXCLUDE_COMPONENTS,
        arrayOf(android.content.ComponentName(context, ShareActivity::class.java)),
    )

    fun copyLink(context: Context, url: String) {
        val cm = context.getSystemService(android.content.ClipboardManager::class.java)
        cm.setPrimaryClip(android.content.ClipData.newPlainText("Link", url))
    }
}
