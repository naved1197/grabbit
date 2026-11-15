package com.grabbit.android.downloads

import android.content.Context
import android.util.Log
import com.grabbit.android.engine.BatchItem
import com.grabbit.android.engine.BatchResult
import com.grabbit.android.engine.Choice
import com.grabbit.android.engine.Engine
import com.grabbit.android.engine.EngineException
import com.grabbit.android.engine.Hello
import com.grabbit.android.engine.Job
import com.grabbit.android.engine.Meta
import com.grabbit.android.engine.Status
import com.grabbit.android.settings.Prefs
import com.grabbit.android.signin.SignIn
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.Collections

/**
 * Ties the engine's jobs to the app's download list: records each request, follows progress,
 * saves finished files into Downloads/Grabbit, and tidies up after the engine or app stopped.
 */
class Downloads(
    private val context: Context,
    private val engine: Engine,
    val history: History,
    private val saver: Saver,
    private val notifier: Notifier,
    private val prefs: Prefs,
    private val scope: CoroutineScope,
) {
    private val saving = Collections.synchronizedSet(mutableSetOf<String>())
    private val saveFailed = Collections.synchronizedSet(mutableSetOf<String>())

    init {
        scope.launch { engine.jobs.collect { onJobs(it) } }
        scope.launch { engine.stopped.collect { onEngineStopped() } }
        scope.launch {
            engine.state.collect { if (it is Engine.State.Ready) onHello(it.hello) }
        }
    }

    suspend fun submit(url: String, choice: Choice, item: Int?, meta: Meta?, cookies: String?): DownloadRecord {
        DownloadService.start(context)
        val job = engine.submit(url, choice, item, meta, cookies)
        val record = DownloadRecord(id = job.id, url = url, choice = choice, item = item, meta = meta,
            title = meta?.title, thumbnail = meta?.thumbnail, uploader = meta?.uploader, duration = meta?.duration).merge(job)
        history.add(record)
        onJobs(engine.jobs.value) // in case it already moved on before it was in the list
        return record
    }

    suspend fun submitAll(choice: Choice, items: List<BatchItem>, cookies: String?): BatchResult {
        DownloadService.start(context)
        val result = engine.batch(choice, items, cookies)
        result.jobs.forEachIndexed { i, job ->
            val req = items.firstOrNull { it.url == job.url && it.item == job.item } ?: items.getOrNull(i)
            history.add(
                DownloadRecord(id = job.id, url = req?.url ?: job.url, choice = choice, item = job.item, meta = req?.meta,
                    title = req?.meta?.title, thumbnail = req?.meta?.thumbnail).merge(job)
            )
        }
        onJobs(engine.jobs.value)
        return result
    }

    /**
     * Save files dropped onto Grabbit (or handed over by another app) straight into Downloads/Grabbit,
     * and list them with the other downloads. Returns how many were saved; throws if none could be.
     */
    suspend fun saveIncoming(uris: List<android.net.Uri>): Int = withContext(Dispatchers.IO) {
        var saved = 0
        var lastError: Exception? = null
        for (uri in uris) {
            try {
                val item = saver.describe(uri)
                val result = saver.save(item)
                val mode = if (item.mime.startsWith("audio/")) "audio" else "video"
                val ext = item.name.substringAfterLast('.', "").lowercase()
                val record = DownloadRecord(
                    id = "drop-" + java.util.UUID.randomUUID(),
                    url = "",
                    choice = Choice(mode = mode, container = ext.ifEmpty { "mp4" }, audioFormat = ext.ifEmpty { "best" }),
                    title = item.name.substringBeforeLast('.'),
                    thumbnail = result.uris.first().toString().takeIf { item.mime.startsWith("image/") },
                    status = Status.FINISHED,
                    stage = "Saved",
                    filename = item.name,
                    filesize = item.size?.toDouble(),
                    uris = result.uris.map(Any::toString),
                    mime = result.mime,
                    finished = System.currentTimeMillis(),
                )
                history.add(record)
                notifier.saved(record)
                saved++
            } catch (e: Exception) {
                Log.w("Grabbit", "couldn't save a dropped item", e)
                lastError = e
            }
        }
        if (saved == 0) throw SaveException(lastError?.message ?: "Couldn't save that.")
        saved
    }

    suspend fun retry(r: DownloadRecord): DownloadRecord {
        val cookies = if (prefs.values.value.useSignIns) SignIn.cookiesFor(r.url) else null
        val fresh = submit(r.url, r.choice, r.item, r.meta, cookies)
        history.remove(r.id)
        return fresh
    }

    suspend fun cancel(r: DownloadRecord) {
        try {
            engine.cancel(r.id)
        } catch (e: EngineException) {
            history.update(r.id) { it.copy(status = Status.CANCELED, stage = "Canceled", speed = null, eta = null) }
        }
    }

    fun cancelAll() {
        history.records.value.filter { Status.isActive(it.status) }.forEach { r -> scope.launch { cancel(r) } }
    }

    fun remove(r: DownloadRecord) {
        history.remove(r.id)
        scope.launch {
            if (Status.isActive(r.status)) runCatching { engine.cancel(r.id) }
            engine.remove(r.id)
        }
    }

    fun clearFinished() {
        val gone = history.records.value.filter { !it.busy }
        history.clearFinished()
        scope.launch { gone.forEach { engine.remove(it.id) } }
    }

    // ------------------------------------------------------------------------------------

    @Synchronized
    private fun onJobs(jobs: Map<String, Job>) {
        for (job in jobs.values) {
            val before = history[job.id] ?: continue
            if (before.saved || before.status == Status.INTERRUPTED || job.id in saveFailed) continue
            history.update(job.id) { it.merge(job) }
            if (job.status == Status.FINISHED && job.path != null && saving.add(job.id)) {
                scope.launch { save(job.id, File(job.path)) }
            }
            if (job.status == Status.ERROR && before.status != Status.ERROR) history[job.id]?.let(notifier::failed)
        }
    }

    private suspend fun save(id: String, file: File, removeFromEngine: Boolean = true) {
        try {
            val saved = withContext(Dispatchers.IO) { saver.save(file) }
            history.update(id) {
                it.copy(uris = saved.uris.map(Any::toString), mime = saved.mime, filename = saved.names.first(),
                    status = Status.FINISHED, stage = "Saved")
            }
            history[id]?.let(notifier::saved)
            if (removeFromEngine) engine.remove(id) else file.delete()
        } catch (e: Exception) {
            Log.w("Grabbit", "saving $file failed", e)
            saveFailed.add(id)
            history.update(id) {
                it.copy(status = Status.ERROR, stage = "Failed", error = e.message ?: "Couldn't save the file to Downloads.")
            }
            history[id]?.let(notifier::failed)
        } finally {
            saving.remove(id)
        }
    }

    /** The engine went away mid-download (it crashed, or Android stopped it). */
    private fun onEngineStopped() {
        history.updateAll { if (Status.isActive(it.status)) it.interrupted() else it }
    }

    /** A fresh engine: hand over files a killed one finished but couldn't deliver. */
    private fun onHello(hello: Hello) {
        val recovered = hello.recovered.associateBy { it.job }
        for (rec in hello.recovered) {
            if (!saving.add(rec.job)) continue
            if (history[rec.job] == null) {
                history.add(DownloadRecord(id = rec.job, url = "", title = rec.name, status = Status.FINISHED))
            }
            scope.launch { save(rec.job, File(rec.path), removeFromEngine = false) }
        }
        // Finished before, but the file is gone with no recovery: nothing to save any more.
        history.updateAll {
            if (it.status == Status.FINISHED && !it.saved && it.id !in recovered && it.id !in saving &&
                it.id !in engine.jobs.value
            ) it.interrupted() else it
        }
    }
}
