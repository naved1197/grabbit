package com.grabbit.android.settings

import android.content.Context
import com.grabbit.android.engine.Choice
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** The person's choices, remembered like the website's localStorage prefs. */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("grabbit", Context.MODE_PRIVATE)

    data class Values(
        val mode: String,
        val quality: String,
        val container: String,
        val audioFormat: String,
        val playlistQuality: String,
        val useSignIns: Boolean,
    ) {
        val choice get() = Choice(mode, quality, container, audioFormat)
    }

    private fun load() = Values(
        mode = sp.getString("mode", "video")!!,
        quality = sp.getString("quality", "best")!!,
        container = sp.getString("container", "mp4")!!,
        audioFormat = sp.getString("audio_format", "mp3")!!,
        playlistQuality = sp.getString("pl_quality", "1080")!!,
        useSignIns = sp.getBoolean("use_sign_ins", true),
    )

    private val _values = MutableStateFlow(load())
    val values: StateFlow<Values> = _values.asStateFlow()

    fun update(block: (Values) -> Values) {
        val v = block(_values.value)
        sp.edit()
            .putString("mode", v.mode)
            .putString("quality", v.quality)
            .putString("container", v.container)
            .putString("audio_format", v.audioFormat)
            .putString("pl_quality", v.playlistQuality)
            .putBoolean("use_sign_ins", v.useSignIns)
            .apply()
        _values.value = v
    }

    var lastUpdateCheck: Long
        get() = sp.getLong("ytdlp_checked", 0)
        set(value) = sp.edit().putLong("ytdlp_checked", value).apply()

    var askedNotifications: Boolean
        get() = sp.getBoolean("asked_notifications", false)
        set(value) = sp.edit().putBoolean("asked_notifications", value).apply()
}
