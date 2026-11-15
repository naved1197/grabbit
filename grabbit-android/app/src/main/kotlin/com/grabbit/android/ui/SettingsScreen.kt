package com.grabbit.android.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.grabbit.android.BuildConfig
import com.grabbit.android.app
import com.grabbit.android.engine.Engine
import com.grabbit.android.engine.YtDlpUpdater
import com.grabbit.android.signin.SignIn
import com.grabbit.android.signin.SignInActivity
import kotlinx.coroutines.launch

private val QUALITIES = listOf(
    "best" to "Best available", "2160" to "4K", "1440" to "1440p", "1080" to "1080p",
    "720" to "720p", "480" to "480p", "360" to "360p",
)
private val CONTAINERS = listOf("mp4" to "MP4 · plays everywhere", "webm" to "WebM · VP9/AV1, smaller", "mkv" to "MKV · keeps original streams")
private val AUDIO = listOf("mp3" to "MP3", "m4a" to "M4A", "opus" to "Opus", "best" to "Original (no conversion)")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val app = context.app
    val prefs by app.prefs.values.collectAsStateWithLifecycle()
    val engine by app.engine.state.collectAsStateWithLifecycle()
    val signIns by SignIn.changes.collectAsStateWithLifecycle()
    var checking by remember { mutableStateOf(false) }
    var updateText by remember { mutableStateOf<String?>(null) }
    var licenses by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Settings") },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, contentDescription = "Back") } },
            )
        },
    ) { pad ->
        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = pad.calculateTopPadding(), bottom = pad.calculateBottomPadding() + 32.dp),
        ) {
            item { Heading("Downloads") }
            item { Dropdown("Video quality", QUALITIES, prefs.quality, { q -> app.prefs.update { it.copy(quality = q) } }, Modifier.fillMaxWidth()) }
            item { Dropdown("Video format", CONTAINERS, prefs.container, { c -> app.prefs.update { it.copy(container = c) } }, Modifier.fillMaxWidth()) }
            item { Dropdown("Audio format", AUDIO, prefs.audioFormat, { a -> app.prefs.update { it.copy(audioFormat = a) } }, Modifier.fillMaxWidth()) }
            item { Note("These are picked when a link opens. Files are saved to Downloads/Grabbit.") }

            item { Heading("Sign-ins") }
            item {
                Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Use my sign-ins", style = MaterialTheme.typography.bodyLarge)
                        Note("For private, follower-only and age-restricted videos, Grabbit sends your sign-in for that one site along with its download. It goes only to that site.")
                    }
                    Spacer(Modifier.width(12.dp))
                    Switch(checked = prefs.useSignIns, onCheckedChange = { on -> app.prefs.update { it.copy(useSignIns = on) } })
                }
            }
            items(SignIn.sites, key = { it.domain + signIns }) { site ->
                val signed = SignIn.isSignedIn(site)
                Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(site.name, style = MaterialTheme.typography.bodyLarge)
                        Text(if (signed) "Signed in" else "Not signed in", style = MaterialTheme.typography.bodySmall,
                            color = if (signed) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (signed) TextButton(onClick = { SignIn.signOut(site) }) { Text("Sign out") }
                    else TextButton(onClick = { context.startActivity(SignInActivity.intent(context, site)) }) { Text("Sign in") }
                }
            }
            item { Note("YouTube rarely needs a sign-in, and Google doesn't allow signing in inside apps like this.") }

            item { Heading("Download engine") }
            item {
                val hello = (engine as? Engine.State.Ready)?.hello
                Text("yt-dlp ${hello?.ytdlp ?: app.runtime.ytdlp.current() ?: "…"}", style = MaterialTheme.typography.bodyLarge)
                Note(
                    when (val s = engine) {
                        is Engine.State.Ready -> "Python ${s.hello.python} · ffmpeg ${if (s.hello.ffmpeg) "ready" else "missing"} · JavaScript: ${s.hello.jsRuntime ?: "none"}"
                        is Engine.State.Failed -> s.message
                        Engine.State.Starting -> "Starting…"
                        Engine.State.Stopped -> "Starts when you look up a link."
                    }
                )
                Note("Sites change often, so Grabbit fetches new versions of yt-dlp by itself, at most twice a day.")
                Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    OutlinedButton(enabled = !checking, onClick = {
                        scope.launch {
                            checking = true
                            updateText = when (val r = app.updater.check(force = true)) {
                                is YtDlpUpdater.Result.UpToDate -> "You have the newest yt-dlp."
                                is YtDlpUpdater.Result.Ready -> "yt-dlp ${r.version} is ready. Grabbit uses it from its next start."
                                is YtDlpUpdater.Result.Failed -> "Couldn't check: ${r.message}"
                                YtDlpUpdater.Result.Skipped -> null
                            }
                            checking = false
                        }
                    }) { Text("Check for updates") }
                    if (checking) CircularProgressIndicator(Modifier.padding(start = 12.dp).size(18.dp), strokeWidth = 2.dp)
                }
                updateText?.let { Note(it) }
            }

            item { Heading("About") }
            item {
                Text("Grabbit ${BuildConfig.VERSION_NAME}", style = MaterialTheme.typography.bodyLarge)
                Note("Made by Cubehole. Free and open source, with no accounts and no tracking.")
                Note("Only download what you have the right to save: your own uploads, Creative Commons and public-domain media, and things creators let you download.")
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TextButton(onClick = {
                        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/naved1197/grabbit")))
                    }) { Text("Source code and help") }
                    TextButton(onClick = { licenses = true }) { Text("Licenses") }
                }
            }
        }
    }

    if (licenses) {
        val text = remember { runCatching { context.assets.open("licenses.txt").bufferedReader().readText() }.getOrDefault("") }
        AlertDialog(
            onDismissRequest = { licenses = false },
            confirmButton = { TextButton(onClick = { licenses = false }) { Text("Close") } },
            title = { Text("Licenses") },
            text = { Text(text, style = MaterialTheme.typography.bodySmall, modifier = Modifier.verticalScroll(rememberScrollState())) },
        )
    }
}

@Composable
private fun Heading(text: String) {
    Column {
        Spacer(Modifier.height(20.dp))
        HorizontalDivider()
        Text(text, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(top = 16.dp, bottom = 4.dp))
    }
}

@Composable
private fun Note(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 4.dp))
}
