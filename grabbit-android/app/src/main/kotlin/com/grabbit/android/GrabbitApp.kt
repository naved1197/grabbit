package com.grabbit.android

import android.app.Application
import android.content.Context
import android.util.Log
import com.grabbit.android.downloads.Downloads
import com.grabbit.android.downloads.History
import com.grabbit.android.downloads.Notifier
import com.grabbit.android.downloads.Saver
import com.grabbit.android.engine.Engine
import com.grabbit.android.engine.Runtime
import com.grabbit.android.engine.YtDlpUpdater
import com.grabbit.android.settings.Prefs
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class GrabbitApp : Application() {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    lateinit var prefs: Prefs
    lateinit var runtime: Runtime
    lateinit var engine: Engine
    lateinit var history: History
    lateinit var notifier: Notifier
    lateinit var downloads: Downloads
    lateinit var updater: YtDlpUpdater
    private var warmedUp = false

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        runtime = Runtime(this)
        engine = Engine(runtime)
        history = History(this, scope)
        notifier = Notifier(this).also { it.createChannels() }
        downloads = Downloads(this, engine, history, Saver(this), notifier, prefs, scope)
        updater = YtDlpUpdater(runtime.ytdlp, prefs)
    }

    /**
     * Start the engine while the app is on screen (so it shares the app's priority), then check
     * for a newer yt-dlp at most twice a day. A link shared right away still waits for this start.
     */
    fun warmUp() {
        if (warmedUp) return
        warmedUp = true
        scope.launch {
            runCatching { engine.start() }.onFailure { Log.w("Grabbit", "engine didn't start", it) }
            delay(10_000)
            updater.check()
        }
    }
}

val Context.app: GrabbitApp get() = applicationContext as GrabbitApp
