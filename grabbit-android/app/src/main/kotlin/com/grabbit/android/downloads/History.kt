package com.grabbit.android.downloads

import android.content.Context
import android.util.Log
import com.grabbit.android.engine.Choice
import com.grabbit.android.engine.EngineJson
import com.grabbit.android.engine.Job
import com.grabbit.android.engine.Meta
import com.grabbit.android.engine.Status
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import java.io.File

/** One download as the app remembers it: the request (for Retry), progress, and where it was saved. */
@Serializable
data class DownloadRecord(
    val id: String, // the engine's job id
    val url: String,
    val choice: Choice = Choice(),
    val item: Int? = null,
    val meta: Meta? = null,
    val title: String? = null,
    val thumbnail: String? = null,
    val uploader: String? = null,
    val duration: Double? = null,
    val status: String = Status.QUEUED,
    val stage: String? = null,
    val progress: Double? = null,
    val speed: Double? = null,
    val eta: Double? = null,
    val downloaded: Double? = null,
    val total: Double? = null,
    val queuePosition: Int? = null,
    val filename: String? = null,
    val filesize: Double? = null,
    val error: String? = null,
    val errorCode: String? = null,
    val uris: List<String> = emptyList(),
    val mime: String? = null,
    val created: Long = System.currentTimeMillis(),
    val finished: Long? = null,
) {
    val saved get() = uris.isNotEmpty()
    /** Keeps the download service running: still downloading, or finished but not saved yet. */
    val busy get() = Status.isActive(status) || (status == Status.FINISHED && !saved)
    val canRetry get() = status in setOf(Status.ERROR, Status.CANCELED, Status.INTERRUPTED)

    fun merge(job: Job): DownloadRecord {
        val terminal = job.status in setOf(Status.FINISHED, Status.ERROR, Status.CANCELED)
        return copy(
            title = job.title ?: title,
            thumbnail = job.thumbnail ?: thumbnail,
            uploader = job.uploader ?: uploader,
            duration = job.duration ?: duration,
            status = job.status,
            stage = job.stage,
            progress = job.progress,
            speed = job.speed,
            eta = job.eta,
            downloaded = job.downloaded,
            total = job.total,
            queuePosition = job.queuePosition,
            filename = job.filename ?: filename,
            filesize = job.filesize ?: filesize,
            error = job.error,
            errorCode = job.errorCode,
            finished = if (terminal) finished ?: System.currentTimeMillis() else null,
        )
    }
}

/** The last [LIMIT] downloads, newest first, kept in a JSON file. */
@OptIn(FlowPreview::class)
class History(context: Context, scope: CoroutineScope) {
    private val file = File(context.filesDir, "downloads.json")
    private val serializer = ListSerializer(DownloadRecord.serializer())
    private val _records = MutableStateFlow(load())
    val records: StateFlow<List<DownloadRecord>> = _records.asStateFlow()

    init {
        scope.launch(Dispatchers.IO) {
            _records.drop(1).debounce(400).collect { write(it) }
        }
    }

    private fun load(): List<DownloadRecord> = try {
        if (!file.isFile) emptyList() else EngineJson.decodeFromString(serializer, file.readText()).map {
            // Anything still running belonged to an app process that's gone now.
            if (Status.isActive(it.status)) it.interrupted() else it
        }
    } catch (e: Exception) {
        Log.w("Grabbit", "couldn't read the download list", e)
        emptyList()
    }

    private fun write(list: List<DownloadRecord>) {
        runCatching {
            val tmp = File(file.path + ".tmp")
            tmp.writeText(EngineJson.encodeToString(serializer, list))
            tmp.renameTo(file)
        }.onFailure { Log.w("Grabbit", "couldn't save the download list", it) }
    }

    operator fun get(id: String) = _records.value.firstOrNull { it.id == id }

    fun add(record: DownloadRecord) = _records.update { (listOf(record) + it.filter { r -> r.id != record.id }).take(LIMIT) }

    fun update(id: String, block: (DownloadRecord) -> DownloadRecord) =
        _records.update { list -> list.map { if (it.id == id) block(it) else it } }

    fun updateAll(block: (DownloadRecord) -> DownloadRecord) = _records.update { list -> list.map(block) }

    fun remove(id: String) = _records.update { list -> list.filter { it.id != id } }

    fun clearFinished() = _records.update { list -> list.filter { it.busy } }

    companion object {
        const val LIMIT = 100
    }
}

fun DownloadRecord.interrupted() = copy(
    status = Status.INTERRUPTED,
    stage = "Interrupted",
    speed = null,
    eta = null,
    finished = finished ?: System.currentTimeMillis(),
)
