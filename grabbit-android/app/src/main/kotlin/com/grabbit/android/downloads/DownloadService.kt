package com.grabbit.android.downloads

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.grabbit.android.app
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Keeps Grabbit alive while it downloads: a foreground service with the progress notification,
 * a wake lock so the phone doesn't doze off mid-file, and a Wi-Fi lock on older Androids.
 */
class DownloadService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var watching: Job? = null
    private var stopping: Job? = null
    private var lastStart = 0L

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val app = applicationContext.app
        ServiceCompat.startForeground(
            this, NOTIFICATION_ID, app.notifier.progress(app.history.records.value),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0,
        )
        lastStart = SystemClock.elapsedRealtime()
        if (intent?.action == ACTION_CANCEL_ALL) app.downloads.cancelAll()
        acquireLocks()
        if (watching == null) {
            watching = scope.launch {
                app.history.records.collect { records -> // a StateFlow: slow collection skips stale lists
                    if (records.any { it.busy }) {
                        stopping?.cancel()
                        stopping = null
                        app.notifier.progress(records).let {
                            androidx.core.app.NotificationManagerCompat.from(this@DownloadService).let { nm ->
                                if (nm.areNotificationsEnabled()) runCatching { nm.notify(NOTIFICATION_ID, it) }
                            }
                        }
                    } else if (stopping == null) {
                        stopping = scope.launch {
                            // A download that was just tapped may not be in the list yet.
                            val sinceStart = SystemClock.elapsedRealtime() - lastStart
                            delay(maxOf(2_000L, GRACE_MS - sinceStart))
                            if (app.history.records.value.none { it.busy }) stopNow()
                            stopping = null
                        }
                    }
                    delay(1_000) // at most one notification update a second
                }
            }
        }
        return START_NOT_STICKY
    }

    private fun acquireLocks() {
        if (wakeLock == null) {
            wakeLock = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Grabbit:download")
                .apply { setReferenceCounted(false); acquire(6 * 60 * 60 * 1000L) }
        }
        if (wifiLock == null && Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            @Suppress("DEPRECATION")
            wifiLock = getSystemService(WifiManager::class.java)
                ?.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "Grabbit:download")
                ?.apply { setReferenceCounted(false); acquire() }
        }
    }

    private fun stopNow() {
        Log.i("Grabbit", "downloads done; stopping the service")
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    /** Android 15 limits data-sync services to 6 hours a day. */
    override fun onTimeout(startId: Int, fgsType: Int) {
        Log.w("Grabbit", "download service timed out")
        stopNow()
    }

    override fun onDestroy() {
        runCatching { wakeLock?.release() }
        runCatching { wifiLock?.release() }
        wakeLock = null
        wifiLock = null
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        const val ACTION_CANCEL_ALL = "com.grabbit.android.CANCEL_ALL"
        private const val NOTIFICATION_ID = 1
        private const val GRACE_MS = 15_000L

        /** Call while the app is on screen (Android won't start this from the background). */
        fun start(context: Context) {
            try {
                ContextCompat.startForegroundService(context, Intent(context, DownloadService::class.java))
            } catch (e: Exception) {
                Log.w("Grabbit", "couldn't start the download service", e)
            }
        }
    }
}
