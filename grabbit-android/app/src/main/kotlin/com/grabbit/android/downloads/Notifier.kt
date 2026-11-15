package com.grabbit.android.downloads

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.grabbit.android.R
import com.grabbit.android.engine.Status
import com.grabbit.android.ui.Fmt
import com.grabbit.android.ui.MainActivity

class Notifier(private val context: Context) {
    private val nm = NotificationManagerCompat.from(context)

    fun createChannels() {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CH_PROGRESS, "Downloads in progress", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Progress while Grabbit downloads"
                setShowBadge(false)
            }
        )
        manager.createNotificationChannel(
            NotificationChannel(CH_DONE, "Finished downloads", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "When a download is saved, or fails"
            }
        )
    }

    private fun openApp(): PendingIntent = PendingIntent.getActivity(
        context, 0, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    /** The download service's ongoing notification. */
    fun progress(records: List<DownloadRecord>): Notification {
        val busy = records.filter { it.busy }
        val downloading = busy.filter { Status.isActive(it.status) }
        val title = when {
            busy.isEmpty() -> "Preparing…"
            downloading.isEmpty() -> "Saving to Downloads"
            downloading.size == 1 -> downloading[0].title ?: "Downloading"
            else -> "Downloading ${downloading.size} files"
        }
        val known = downloading.mapNotNull { it.progress }
        val percent = if (known.isNotEmpty() && known.size == downloading.size) known.average().toInt() else null
        val text = when {
            downloading.size == 1 -> Fmt.statusLine(downloading[0])
            downloading.size > 1 -> listOfNotNull(percent?.let { "$it%" }, downloading.count { it.status == Status.QUEUED }
                .takeIf { it > 0 }?.let { "$it waiting" }).joinToString(" · ").ifEmpty { "Working…" }
            else -> null
        }
        val cancel = PendingIntent.getService(
            context, 1, Intent(context, DownloadService::class.java).setAction(DownloadService.ACTION_CANCEL_ALL),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(context, CH_PROGRESS)
            .setSmallIcon(R.drawable.ic_stat_grabbit)
            .setContentTitle(title)
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_PROGRESS)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setProgress(100, percent ?: 0, percent == null)
            .setContentIntent(openApp())
            .apply { if (downloading.isNotEmpty()) addAction(0, if (downloading.size > 1) "Cancel all" else "Cancel", cancel) }
            .build()
    }

    @SuppressLint("MissingPermission")
    fun saved(r: DownloadRecord) {
        if (!nm.areNotificationsEnabled()) return
        val uri = r.uris.firstOrNull()?.let(Uri::parse) ?: return
        val view = Intent(Intent.ACTION_VIEW).setDataAndType(uri, r.mime).addFlags(
            Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK
        )
        val share = com.grabbit.android.ui.FileActions.withoutGrabbit(context, Intent.createChooser(
            Intent(Intent.ACTION_SEND).setType(r.mime).putExtra(Intent.EXTRA_STREAM, uri)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), null,
        )).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val code = r.id.hashCode()
        val n = NotificationCompat.Builder(context, CH_DONE)
            .setSmallIcon(R.drawable.ic_stat_grabbit)
            .setContentTitle(if (r.uris.size > 1) "Saved ${r.uris.size} photos to Downloads" else "Saved to Downloads")
            .setContentText(r.title ?: r.filename)
            .setAutoCancel(true)
            .setContentIntent(PendingIntent.getActivity(context, code, view, PendingIntent.FLAG_IMMUTABLE))
            .addAction(0, "Open", PendingIntent.getActivity(context, code, view, PendingIntent.FLAG_IMMUTABLE))
            .addAction(0, "Share", PendingIntent.getActivity(context, code + 1, share, PendingIntent.FLAG_IMMUTABLE))
            .build()
        nm.notify(code, n)
    }

    @SuppressLint("MissingPermission")
    fun failed(r: DownloadRecord) {
        if (!nm.areNotificationsEnabled()) return
        val n = NotificationCompat.Builder(context, CH_DONE)
            .setSmallIcon(R.drawable.ic_stat_grabbit)
            .setContentTitle("Couldn't download ${r.title ?: "this"}")
            .setContentText(r.error)
            .setStyle(NotificationCompat.BigTextStyle().bigText(r.error))
            .setAutoCancel(true)
            .setContentIntent(openApp())
            .build()
        nm.notify(r.id.hashCode(), n)
    }

    companion object {
        const val CH_PROGRESS = "downloads"
        const val CH_DONE = "finished"
    }
}
