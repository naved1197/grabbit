package com.grabbit.android.signin

import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.grabbit.android.R
import com.grabbit.android.downloads.Notifier

/**
 * While you've stepped away from a half-done sign-in (to fetch a code, or approve the login in the
 * site's own app), keep Grabbit running for a few minutes so Android doesn't close it, and show a
 * notification that takes you straight back. Android 14+ runs this as a "short service" (3 minutes).
 */
class SignInKeepAlive : Service() {
    private val handler = Handler(Looper.getMainLooper())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val site = SignIn.sites.firstOrNull { it.domain == intent?.getStringExtra(SignInActivity.EXTRA_SITE) }
        val back = PendingIntent.getActivity(
            this, 2, site?.let { SignInActivity.intent(this, it) } ?: Intent(), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val n = NotificationCompat.Builder(this, Notifier.CH_PROGRESS)
            .setSmallIcon(R.drawable.ic_stat_grabbit)
            .setContentTitle("Finish signing in to ${site?.name ?: "the site"}")
            .setContentText("Tap to go back to Grabbit")
            .setContentIntent(back)
            .setOngoing(true)
            .setSilent(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
        try {
            ServiceCompat.startForeground(
                this, NOTIFICATION_ID, n,
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) ServiceInfo.FOREGROUND_SERVICE_TYPE_SHORT_SERVICE else 0,
            )
        } catch (e: Exception) {
            Log.w("Grabbit", "couldn't keep Grabbit running during sign-in", e)
            stopSelf()
            return START_NOT_STICKY
        }
        // Older Androids have no short-service limit; stop on our own after the same few minutes.
        handler.removeCallbacksAndMessages(null)
        handler.postDelayed({ stopSelf() }, LIMIT_MS)
        return START_NOT_STICKY
    }

    /** Android 14+: the short service's time is up. */
    override fun onTimeout(startId: Int) {
        stopSelf()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }

    companion object {
        private const val NOTIFICATION_ID = 3
        private const val LIMIT_MS = 3 * 60 * 1000L

        fun start(context: Context, site: Site) {
            try {
                ContextCompat.startForegroundService(
                    context, Intent(context, SignInKeepAlive::class.java).putExtra(SignInActivity.EXTRA_SITE, site.domain),
                )
            } catch (e: Exception) {
                Log.w("Grabbit", "couldn't start the sign-in keep-alive", e)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, SignInKeepAlive::class.java))
        }
    }
}
