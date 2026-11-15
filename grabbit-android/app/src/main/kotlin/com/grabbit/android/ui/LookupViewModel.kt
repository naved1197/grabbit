package com.grabbit.android.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.grabbit.android.app
import com.grabbit.android.engine.BatchItem
import com.grabbit.android.engine.Choice
import com.grabbit.android.engine.EngineException
import com.grabbit.android.engine.Entry
import com.grabbit.android.engine.Info
import com.grabbit.android.engine.Meta
import com.grabbit.android.signin.SignIn
import com.grabbit.android.signin.Site
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** Looking up a link and starting its download; shared by the share sheet and the main screen. */
class LookupViewModel(application: Application) : AndroidViewModel(application) {
    sealed interface State {
        data object Idle : State
        data class Loading(val url: String, val since: Long = System.currentTimeMillis()) : State
        data class Loaded(val url: String, val info: Info, val cookies: String?) : State
        data class Failed(val url: String?, val message: String, val code: String? = null, val site: Site? = null) : State
    }

    private val grabbit = application.app
    private val _state = MutableStateFlow<State>(State.Idle)
    val state: StateFlow<State> = _state.asStateFlow()
    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()
    private var lookupJob: Job? = null

    fun lookup(raw: String?) {
        val url = Fmt.extractUrl(raw)
        if (url == null) {
            _state.value = State.Failed(null, "Paste a link to a video first.")
            return
        }
        lookupJob?.cancel()
        _state.value = State.Loading(url)
        lookupJob = viewModelScope.launch {
            val cookies = if (grabbit.prefs.values.value.useSignIns) SignIn.cookiesFor(url) else null
            _state.value = try {
                State.Loaded(url, grabbit.engine.lookup(url, cookies), cookies)
            } catch (e: EngineException) {
                failure(url, e)
            }
        }
    }

    fun retry() {
        when (val s = _state.value) {
            is State.Failed -> s.url?.let(::lookup)
            is State.Loaded -> lookup(s.url)
            else -> Unit
        }
    }

    fun reset() {
        lookupJob?.cancel()
        _state.value = State.Idle
    }

    private fun failure(url: String, e: EngineException): State.Failed =
        State.Failed(url, Fmt.engineMessage(e.message ?: "Something went wrong.", e.code), e.code, SignIn.siteFor(url))

    /** Download the looked-up video. Returns an error message, or null when it's on its way. */
    suspend fun download(choice: Choice): String? {
        val s = _state.value as? State.Loaded ?: return "Look the link up first."
        _busy.value = true
        return try {
            val meta = Meta(s.info.title, s.info.thumbnail, s.info.uploader, s.info.duration)
            grabbit.downloads.submit(s.url, choice, null, meta, s.cookies)
            null
        } catch (e: EngineException) {
            Fmt.engineMessage(e.message ?: "Couldn't start the download.", e.code)
        } finally {
            _busy.value = false
        }
    }

    /** Download the picked playlist videos or post items. */
    suspend fun downloadEntries(entries: List<Entry>, choice: Choice): String? {
        val s = _state.value as? State.Loaded ?: return "Look the link up first."
        _busy.value = true
        return try {
            val items = entries.map { e ->
                // A post's items share the post's link; the engine picks them by number.
                val title = if (s.info.post) null else e.title
                BatchItem(e.url, e.item, Meta(title, e.thumbnail, e.uploader, e.duration))
            }
            val result = grabbit.downloads.submitAll(choice, items, s.cookies)
            result.errors.firstOrNull()?.error?.takeIf { result.jobs.isEmpty() }
        } catch (e: EngineException) {
            Fmt.engineMessage(e.message ?: "Couldn't start the downloads.", e.code)
        } finally {
            _busy.value = false
        }
    }
}
