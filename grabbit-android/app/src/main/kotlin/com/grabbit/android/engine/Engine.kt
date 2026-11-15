package com.grabbit.android.engine

import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.encodeToJsonElement
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.io.BufferedWriter
import java.io.File
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/** An error the engine (or this client) reports; [message] is fit to show, [code] is machine-readable. */
class EngineException(message: String, val code: String? = null) : Exception(message)

/**
 * The Python engine process (grabbit_engine.py) and its JSON-lines protocol.
 *
 * One process for the whole app; it starts on first use, and again on the next request if it dies.
 * [jobs] mirrors the engine's job list from its "jobs" events.
 */
class Engine(private val runtime: Runtime) {
    sealed interface State {
        data object Stopped : State
        data object Starting : State
        data class Ready(val hello: Hello) : State
        data class Failed(val message: String) : State
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val _state = MutableStateFlow<State>(State.Stopped)
    val state: StateFlow<State> = _state.asStateFlow()
    private val _jobs = MutableStateFlow<Map<String, Job>>(emptyMap())
    val jobs: StateFlow<Map<String, Job>> = _jobs.asStateFlow()
    private val _stopped = MutableSharedFlow<Unit>(extraBufferCapacity = 8)
    /** Fires when a running engine goes away; its unfinished jobs are lost. */
    val stopped: SharedFlow<Unit> = _stopped

    private val startLock = Mutex()
    private val writeLock = Any()
    private val pending = ConcurrentHashMap<Int, CompletableDeferred<JsonObject>>()
    private val nextId = AtomicInteger()
    @Volatile private var process: Process? = null
    @Volatile private var writer: BufferedWriter? = null
    @Volatile private var ready: CompletableDeferred<JsonObject>? = null
    private var quickFailures = 0

    val hello: Hello? get() = (state.value as? State.Ready)?.hello

    /** Start the engine if it isn't running; returns its hello (versions, recovered files). */
    suspend fun start(): Hello = startLock.withLock {
        val current = state.value
        if (current is State.Ready && process?.isAlive == true) return current.hello
        if (quickFailures >= 3) delay(2_000L * quickFailures) // don't spin on an engine that can't start
        _state.value = State.Starting
        try {
            val hello = withContext(Dispatchers.IO) {
                try {
                    launch()
                } catch (e: EngineException) {
                    if (e.code == "start_failed" && runtime.ytdlp.revert()) launch() else throw e
                }
            }
            _state.value = State.Ready(hello)
            hello
        } catch (e: Exception) {
            quickFailures++
            val message = (e as? EngineException)?.message ?: "Grabbit's download engine didn't start: ${e.message}"
            _state.value = State.Failed(message)
            throw EngineException(message, "start_failed")
        }
    }

    private suspend fun launch(): Hello {
        runtime.prepare()
        val pythonPath = listOf(runtime.engineDir, runtime.ytdlp.activeDir())
        val pb = ProcessBuilder(runtime.python.path, "-u", File(runtime.engineDir, "grabbit_engine.py").path)
            .directory(runtime.workDir.apply { mkdirs() })
        pb.environment().putAll(runtime.env(pythonPath))
        val started = System.currentTimeMillis()
        val p = pb.start()
        val readySignal = CompletableDeferred<JsonObject>()
        ready = readySignal
        process = p
        writer = p.outputStream.bufferedWriter(Charsets.UTF_8)
        scope.launch { readStdout(p) }
        scope.launch { EngineLog.drain(p, runtime.workDir) }
        try {
            withTimeout(START_TIMEOUT_MS) { readySignal.await() }
        } catch (e: Exception) {
            p.destroy()
            throw EngineException("Grabbit's download engine didn't start: ${e.message ?: e.javaClass.simpleName}", "start_failed")
        }
        val hello = EngineJson.decodeFromJsonElement<Hello>(call("hello"))
        if (System.currentTimeMillis() - started < 60_000) quickFailures = 0
        Log.i(TAG, "engine ready: yt-dlp ${hello.ytdlp}, Python ${hello.python}, JS ${hello.jsRuntime}")
        return hello
    }

    private fun readStdout(p: Process) {
        try {
            p.inputStream.bufferedReader(Charsets.UTF_8).forEachLine { line -> handle(line) }
        } catch (e: Exception) {
            Log.w(TAG, "engine output ended", e)
        }
        val code = runCatching { p.waitFor() }.getOrNull()
        if (process === p) {
            Log.w(TAG, "engine exited ($code)")
            process = null
            writer = null
            ready?.completeExceptionally(EngineException("The download engine stopped while starting.", "start_failed"))
            val err = EngineException("Grabbit's download engine stopped. Try again.", "engine_stopped")
            pending.values.forEach { it.completeExceptionally(err) }
            pending.clear()
            _state.value = State.Stopped
            _stopped.tryEmit(Unit)
        }
    }

    private fun handle(line: String) {
        val msg = runCatching { EngineJson.parseToJsonElement(line).jsonObject }.getOrElse {
            Log.w(TAG, "engine said something that isn't JSON: ${line.take(200)}")
            return
        }
        val id = msg["id"]?.jsonPrimitive?.intOrNull
        if (id != null) {
            pending.remove(id)?.complete(msg)
            return
        }
        when (msg["event"]?.jsonPrimitive?.contentOrNull) {
            "jobs" -> {
                val updates = msg["jobs"]?.jsonArray.orEmpty().mapNotNull {
                    runCatching { EngineJson.decodeFromJsonElement<Job>(it) }.getOrNull()
                }
                _jobs.update { old -> old + updates.associateBy { it.id } }
            }
            "ready" -> ready?.complete(msg)
            "fatal" -> ready?.completeExceptionally(
                EngineException(msg["error"]?.jsonPrimitive?.contentOrNull ?: "The download engine failed to start.", "start_failed")
            )
        }
    }

    /** Send one request and wait for its reply; throws [EngineException] with the engine's message. */
    suspend fun call(op: String, args: JsonObject = JsonObject(emptyMap()), timeoutMs: Long = 150_000): JsonElement {
        if (op != "hello") start()
        val w = writer ?: throw EngineException("Grabbit's download engine isn't running. Try again.", "engine_stopped")
        val id = nextId.incrementAndGet()
        val reply = CompletableDeferred<JsonObject>()
        pending[id] = reply
        val line = buildJsonObject {
            put("id", id)
            put("op", op)
            args.forEach { (k, v) -> put(k, v) }
        }.toString()
        try {
            withContext(Dispatchers.IO) {
                synchronized(writeLock) {
                    w.write(line)
                    w.newLine()
                    w.flush()
                }
            }
            val msg = withTimeout(timeoutMs) { reply.await() }
            if (msg["ok"]?.jsonPrimitive?.booleanOrNull == true) return msg["result"] ?: JsonNull
            throw EngineException(
                msg["error"]?.jsonPrimitive?.contentOrNull ?: "Something went wrong.",
                msg["code"]?.jsonPrimitive?.contentOrNull,
            )
        } catch (e: java.io.IOException) {
            throw EngineException("Grabbit's download engine stopped. Try again.", "engine_stopped")
        } finally {
            pending.remove(id)
        }
    }

    // Typed operations ---------------------------------------------------------------------

    suspend fun lookup(url: String, cookies: String?): Info = EngineJson.decodeFromJsonElement(
        call("lookup", buildJsonObject {
            put("url", url)
            cookies?.let { put("cookies", it) }
        }, timeoutMs = 120_000)
    )

    suspend fun submit(url: String, choice: Choice, item: Int?, meta: Meta?, cookies: String?): Job {
        val job = EngineJson.decodeFromJsonElement<Job>(call("submit", buildJsonObject {
            put("url", url)
            put("choice", EngineJson.encodeToJsonElement(choice))
            item?.let { put("item", it) }
            meta?.let { put("meta", EngineJson.encodeToJsonElement(it)) }
            cookies?.let { put("cookies", it) }
        }))
        _jobs.update { it + (job.id to job) }
        return job
    }

    suspend fun batch(choice: Choice, items: List<BatchItem>, cookies: String?): BatchResult {
        val result = EngineJson.decodeFromJsonElement<BatchResult>(call("batch", buildJsonObject {
            put("choice", EngineJson.encodeToJsonElement(choice))
            put("items", EngineJson.encodeToJsonElement(items))
            cookies?.let { put("cookies", it) }
        }))
        _jobs.update { old -> old + result.jobs.associateBy { it.id } }
        return result
    }

    suspend fun cancel(jobId: String) {
        call("cancel", buildJsonObject { put("job", jobId) })
    }

    suspend fun remove(jobId: String) {
        _jobs.update { it - jobId }
        if (process != null) runCatching { call("remove", buildJsonObject { put("job", jobId) }) }
    }

    companion object {
        const val TAG = "Grabbit"
        private const val START_TIMEOUT_MS = 90_000L
    }
}

/** The engine's stderr (Python logging) to logcat and a small rolling file for bug reports. */
object EngineLog {
    private const val MAX_BYTES = 512 * 1024L

    fun file(workDir: File) = File(workDir, "engine.log")

    fun drain(p: Process, workDir: File) {
        val log = file(workDir)
        try {
            p.errorStream.bufferedReader(Charsets.UTF_8).forEachLine { line ->
                Log.i("Grabbit.engine", line)
                runCatching {
                    if (log.length() > MAX_BYTES) log.renameTo(File(workDir, "engine.1.log"))
                    log.appendText(line + "\n")
                }
            }
        } catch (_: Exception) {
        }
    }
}
