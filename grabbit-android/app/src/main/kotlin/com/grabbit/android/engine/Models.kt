package com.grabbit.android.engine

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/** JSON shapes of grabbit_engine.py, which are grabbit-web's own (/api/info, the job object). */

val EngineJson = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    encodeDefaults = true
    coerceInputValues = true
}

@Serializable
data class Hello(
    val protocol: Int = 1,
    val ytdlp: String = "",
    val python: String = "",
    val ffmpeg: Boolean = false,
    @SerialName("js_runtime") val jsRuntime: String? = null,
    val recovered: List<Recovered> = emptyList(),
)

@Serializable
data class Recovered(val job: String, val name: String, val path: String, val size: Double = 0.0)

@Serializable
data class Quality(
    val value: String,
    val height: Int? = null,
    val label: String,
    val badge: String? = null,
    val size: Double? = null,
)

@Serializable
data class Entry(
    val url: String,
    val title: String? = null,
    val duration: Double? = null,
    val thumbnail: String? = null,
    val uploader: String? = null,
    val item: Int? = null,
    val photo: Boolean = false,
)

/** A lookup result: one video, or a playlist / multi-item post. */
@Serializable
data class Info(
    val kind: String = "video",
    val id: String? = null,
    val title: String = "Untitled",
    val uploader: String? = null,
    val duration: Double? = null,
    val thumbnail: String? = null,
    @SerialName("webpage_url") val webpageUrl: String? = null,
    val site: String? = null,
    @SerialName("is_live") val isLive: Boolean = false,
    val qualities: List<Quality> = emptyList(),
    @SerialName("has_video") val hasVideo: Boolean = true,
    @SerialName("has_audio") val hasAudio: Boolean = true,
    @SerialName("audio_size") val audioSize: Double? = null,
    val warning: String? = null,
    // playlists and posts
    val post: Boolean = false,
    val count: Int = 0,
    val videos: Int = 0,
    val photos: Int = 0,
    val entries: List<Entry> = emptyList(),
    val truncated: Boolean = false,
) {
    val isPlaylist get() = kind == "playlist"
}

@Serializable
data class Choice(
    val mode: String = "video",
    val quality: String = "best",
    val container: String = "mp4",
    @SerialName("audio_format") val audioFormat: String = "mp3",
)

@Serializable
data class Meta(
    val title: String? = null,
    val thumbnail: String? = null,
    val uploader: String? = null,
    val duration: Double? = null,
)

/** grabbit-web's Job.public(), plus the finished file's path on this phone. */
@Serializable
data class Job(
    val id: String,
    val url: String = "",
    val mode: String = "video",
    val quality: String = "best",
    val format: String? = null,
    val title: String? = null,
    val thumbnail: String? = null,
    val uploader: String? = null,
    val duration: Double? = null,
    val item: Int? = null,
    val status: String = "queued",
    val stage: String? = null,
    val progress: Double? = null,
    val speed: Double? = null,
    val eta: Double? = null,
    val downloaded: Double? = null,
    val total: Double? = null,
    val filename: String? = null,
    val filesize: Double? = null,
    val error: String? = null,
    @SerialName("error_code") val errorCode: String? = null,
    @SerialName("queue_position") val queuePosition: Int? = null,
    val path: String? = null,
)

@Serializable
data class BatchItem(val url: String, val item: Int? = null, val meta: Meta? = null)

@Serializable
data class BatchResult(val jobs: List<Job> = emptyList(), val errors: List<BatchError> = emptyList())

@Serializable
data class BatchError(val url: String? = null, val error: String? = null)

object Status {
    const val QUEUED = "queued"
    const val STARTING = "starting"
    const val DOWNLOADING = "downloading"
    const val PROCESSING = "processing"
    const val FINISHED = "finished"
    const val ERROR = "error"
    const val CANCELED = "canceled"
    const val INTERRUPTED = "interrupted" // Android only: the app or engine stopped mid-download

    val ACTIVE = setOf(QUEUED, STARTING, DOWNLOADING, PROCESSING)
    fun isActive(status: String) = status in ACTIVE
}
