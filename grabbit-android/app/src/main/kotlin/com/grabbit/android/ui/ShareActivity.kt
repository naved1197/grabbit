package com.grabbit.android.ui

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import com.grabbit.android.R
import com.grabbit.android.app
import com.grabbit.android.signin.SignInActivity
import com.grabbit.android.ui.theme.GrabbitTheme
import kotlinx.coroutines.launch

/** "Share → Grabbit": a sheet over the app the link came from. Pick a quality, tap Download, done. */
class ShareActivity : ComponentActivity() {
    private val vm: LookupViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        app.warmUp()
        // Also when Android brings the sheet back after the app was stopped: the link is still in the intent.
        if (savedInstanceState == null || vm.state.value == LookupViewModel.State.Idle) handle(intent)
        setContent { GrabbitTheme { ShareSheet(vm, saving.value, onClose = ::finish) } }
    }

    /** Files shared to Grabbit (Chrome's "Share image", Photos, a gallery): how many are being saved. */
    private val saving = androidx.compose.runtime.mutableStateOf<Int?>(null)

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    companion object {
        @Volatile private var lastShare: Pair<String, Long>? = null
        @Volatile private var lastFiles = 0L
    }

    private fun handle(intent: Intent?) {
        val files = sharedFiles(intent)
        if (files.isNotEmpty()) {
            lastShare = null
            lastFiles = System.currentTimeMillis()
            saveFiles(files)
            return
        }
        val text = intent?.getStringExtra(Intent.EXTRA_TEXT) ?: intent?.getStringExtra(Intent.EXTRA_SUBJECT)
        val link = Fmt.extractUrl(text)
        // Android's system UI sometimes relaunches the share sheet a moment later with no text (and a
        // cleared task, so a fresh sheet): reuse the link that was just shared instead of "Paste a link".
        val recent = lastShare?.takeIf { System.currentTimeMillis() - it.second < 15_000 }?.first
        android.util.Log.i("Grabbit", "share: link=${link != null} recent=${recent != null} state=${vm.state.value::class.simpleName}")
        when {
            link != null -> {
                lastShare = link to System.currentTimeMillis()
                vm.lookup(link)
            }
            vm.state.value != LookupViewModel.State.Idle -> Unit // keep what's on screen
            recent != null -> vm.lookup(recent)
            saving.value != null || System.currentTimeMillis() - lastFiles < 15_000 -> if (saving.value == null) finish()
            else -> vm.lookup(text)
        }
    }

    /** Images, videos or audio handed over with Share (not links: those are looked up). */
    private fun sharedFiles(intent: Intent?): List<android.net.Uri> {
        intent ?: return emptyList()
        val type = intent.type.orEmpty()
        if (!(type.startsWith("image/") || type.startsWith("video/") || type.startsWith("audio/"))) return emptyList()
        val uris = when (intent.action) {
            Intent.ACTION_SEND ->
                listOfNotNull(androidx.core.content.IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, android.net.Uri::class.java))
            Intent.ACTION_SEND_MULTIPLE ->
                androidx.core.content.IntentCompat.getParcelableArrayListExtra(intent, Intent.EXTRA_STREAM, android.net.Uri::class.java).orEmpty()
            else -> emptyList()
        }
        return uris.ifEmpty {
            intent.clipData?.let { c -> (0 until c.itemCount).mapNotNull { c.getItemAt(it).uri } }.orEmpty()
        }
    }

    /** Copy them into Downloads/Grabbit while this screen still holds the permission to read them. */
    private fun saveFiles(uris: List<android.net.Uri>) {
        saving.value = uris.size
        lifecycleScope.launch {
            val message = try {
                val n = app.downloads.saveIncoming(uris)
                if (n == 1) "Saved to Downloads/Grabbit" else "Saved $n files to Downloads/Grabbit"
            } catch (e: Exception) {
                e.message ?: "Couldn't save that."
            }
            Toast.makeText(this@ShareActivity, message, Toast.LENGTH_SHORT).show()
            finish()
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ShareSheet(vm: LookupViewModel, saving: Int?, onClose: () -> Unit) {
    val context = LocalContext.current
    val app = context.app
    val state by vm.state.collectAsStateWithLifecycle()
    val busy by vm.busy.collectAsStateWithLifecycle()
    val prefs by app.prefs.values.collectAsStateWithLifecycle()
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var error by remember { mutableStateOf<String?>(null) }
    val ask = rememberNotificationAsk()
    RetryAfterSignIn(vm)

    fun close() {
        scope.launch { sheet.hide() }.invokeOnCompletion { onClose() }
    }

    fun started(err: String?) {
        error = err
        if (err == null) {
            Toast.makeText(context, "Downloading. Follow along in the notification.", Toast.LENGTH_SHORT).show()
            close()
        }
    }

    ModalBottomSheet(onDismissRequest = onClose, sheetState = sheet) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).padding(bottom = 28.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(bottom = 8.dp)) {
                Image(painterResource(R.drawable.ic_logo), contentDescription = null, modifier = Modifier.size(26.dp))
                Spacer(Modifier.width(8.dp))
                Text("Grabbit", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                TextButton(onClick = {
                    context.startActivity(Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    onClose()
                }) { Text("Open app") }
            }
            if (saving != null) {
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 16.dp)) {
                    androidx.compose.material3.CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(12.dp))
                    Text(if (saving == 1) "Saving to Downloads/Grabbit…" else "Saving $saving files to Downloads/Grabbit…",
                        style = MaterialTheme.typography.bodyLarge)
                }
            } else LookupPanel(
                state = state, prefs = prefs, busy = busy,
                onDownload = { c ->
                    app.prefs.update { it.copy(mode = c.mode, quality = c.quality, container = c.container, audioFormat = c.audioFormat) }
                    ask { scope.launch { started(vm.download(c)) } }
                },
                onDownloadEntries = { entries, c ->
                    app.prefs.update { it.copy(playlistQuality = c.quality, audioFormat = c.audioFormat) }
                    ask { scope.launch { started(vm.downloadEntries(entries, c)) } }
                },
                onSignIn = { site -> context.startActivity(SignInActivity.intent(context, site)) },
                onRetry = vm::retry,
            )
            error?.let {
                Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 12.dp))
            }
        }
    }
}
