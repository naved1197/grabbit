package com.grabbit.android.ui

import android.content.ClipboardManager
import android.content.Intent
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Link
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import com.grabbit.android.BuildConfig
import com.grabbit.android.R
import com.grabbit.android.app
import com.grabbit.android.engine.Choice
import com.grabbit.android.engine.Entry
import com.grabbit.android.signin.SignInActivity
import com.grabbit.android.ui.theme.GrabbitGradient
import com.grabbit.android.ui.theme.GrabbitTheme
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val vm: LookupViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        app.warmUp()
        if (savedInstanceState == null) handleDebugIntent(intent)
        setContent { GrabbitTheme { MainScreen(vm) } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleDebugIntent(intent)
    }

    /**
     * Debug builds only, for testing from a computer:
     *   adb shell am start -n com.grabbit.android/.ui.MainActivity --es url <link> [--ez auto true]
     * `auto` downloads with the saved defaults (every item, for playlists and posts).
     */
    private fun handleDebugIntent(intent: Intent?) {
        if (!BuildConfig.DEBUG) return
        val url = intent?.getStringExtra("url") ?: return
        vm.lookup(url)
        if (!intent.getBooleanExtra("auto", false)) return
        lifecycleScope.launch {
            val s = vm.state.first { it is LookupViewModel.State.Loaded || it is LookupViewModel.State.Failed }
            if (s !is LookupViewModel.State.Loaded) return@launch Unit.also { Log.i("Grabbit", "auto: lookup failed: $s") }
            val p = app.prefs.values.value
            val err = if (s.info.isPlaylist) vm.downloadEntries(s.info.entries, p.choice.copy(quality = p.playlistQuality))
            else vm.download(p.choice)
            Log.i("Grabbit", "auto: download ${err ?: "started"}")
            if (err == null) vm.reset()
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MainScreen(vm: LookupViewModel) {
    var settings by rememberSaveable { mutableStateOf(false) }
    if (settings) {
        BackHandler { settings = false }
        SettingsScreen { settings = false }
        return
    }
    val context = LocalContext.current
    val app = context.app
    val state by vm.state.collectAsStateWithLifecycle()
    val busy by vm.busy.collectAsStateWithLifecycle()
    val prefs by app.prefs.values.collectAsStateWithLifecycle()
    val records by app.history.records.collectAsStateWithLifecycle()
    var text by rememberSaveable { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    val focus = LocalFocusManager.current
    val ask = rememberNotificationAsk()
    RetryAfterSignIn(vm)

    fun lookup(value: String) {
        focus.clearFocus()
        text = value
        vm.lookup(value)
    }

    fun started(err: String?) = scope.launch {
        if (err == null) {
            vm.reset()
            text = ""
            snackbar.showSnackbar("Added to your downloads")
        } else {
            snackbar.showSnackbar(err)
        }
    }

    // Drag an image, video or link onto Grabbit from another app (split screen, pop-up window, taskbar).
    DropZone(
        onLink = { link -> lookup(link) },
        onFiles = { uris, release ->
            scope.launch {
                try {
                    val n = app.downloads.saveIncoming(uris)
                    snackbar.showSnackbar(if (n == 1) "Saved to Downloads/Grabbit" else "Saved $n files to Downloads/Grabbit")
                } catch (e: Exception) {
                    snackbar.showSnackbar(e.message ?: "Couldn't save that.")
                } finally {
                    release()
                }
            }
        },
    ) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Image(painterResource(R.drawable.ic_logo), contentDescription = null, modifier = Modifier.size(30.dp))
                        Spacer(Modifier.width(10.dp))
                        Text("Grabbit", fontWeight = FontWeight.Bold)
                    }
                },
                actions = { IconButton(onClick = { settings = true }) { Icon(Icons.Rounded.Settings, contentDescription = "Settings") } },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { pad ->
        LazyColumn(
            Modifier.fillMaxSize().imePadding(),
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = pad.calculateTopPadding(), bottom = pad.calculateBottomPadding() + 32.dp),
        ) {
            item {
                Text(
                    buildAnnotatedString {
                        append("Download ")
                        withStyle(SpanStyle(brush = GrabbitGradient)) { append("any video.") }
                    },
                    style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.ExtraBold,
                    modifier = Modifier.padding(top = 8.dp),
                )
                Text(
                    "From YouTube, Instagram, TikTok, X, Reddit, Vimeo and 1,800+ other sites. Paste a link, or tap Share → Grabbit in any app.",
                    style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 6.dp, bottom = 16.dp),
                )
            }
            item {
                OutlinedTextField(
                    value = text,
                    onValueChange = { v ->
                        // Pasting a whole link looks it up straight away, like the website.
                        val pasted = v.length - text.length > 8
                        text = v
                        if (pasted && Fmt.extractUrl(v) != null) lookup(v)
                    },
                    modifier = Modifier.fillMaxWidth(),
                    placeholder = { Text("Paste a video link…") },
                    leadingIcon = { Icon(Icons.Rounded.Link, contentDescription = null) },
                    trailingIcon = {
                        if (text.isEmpty()) {
                            TextButton(onClick = {
                                val clip = context.getSystemService(ClipboardManager::class.java)?.primaryClip
                                val pasted = clip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(context)?.toString()
                                if (pasted.isNullOrBlank()) scope.launch { snackbar.showSnackbar("There's no link on the clipboard.") }
                                else lookup(pasted)
                            }) { Text("Paste") }
                        } else {
                            IconButton(onClick = { text = ""; vm.reset() }) { Icon(Icons.Rounded.Close, contentDescription = "Clear") }
                        }
                    },
                    singleLine = true,
                    shape = RoundedCornerShape(28.dp),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go),
                    keyboardActions = KeyboardActions(onGo = { lookup(text) }),
                )
                Button(
                    onClick = { lookup(text) },
                    enabled = state !is LookupViewModel.State.Loading,
                    modifier = Modifier.fillMaxWidth().padding(top = 10.dp).height(50.dp),
                ) { Text(if (state is LookupViewModel.State.Loading) "Looking…" else "Get video") }
            }
            if (state != LookupViewModel.State.Idle) {
                item {
                    Surface(
                        color = MaterialTheme.colorScheme.surfaceContainerLow,
                        shape = MaterialTheme.shapes.large,
                        tonalElevation = 1.dp,
                        modifier = Modifier.fillMaxWidth().padding(top = 16.dp),
                    ) {
                        Column(Modifier.padding(16.dp)) {
                            LookupPanel(
                                state = state, prefs = prefs, busy = busy,
                                onDownload = { c: Choice ->
                                    app.prefs.update { it.copy(mode = c.mode, quality = c.quality, container = c.container, audioFormat = c.audioFormat) }
                                    ask { scope.launch { started(vm.download(c)) } }
                                },
                                onDownloadEntries = { entries: List<Entry>, c: Choice ->
                                    app.prefs.update { it.copy(playlistQuality = c.quality, audioFormat = c.audioFormat) }
                                    ask { scope.launch { started(vm.downloadEntries(entries, c)) } }
                                },
                                onSignIn = { site -> context.startActivity(SignInActivity.intent(context, site)) },
                                onRetry = vm::retry,
                            )
                        }
                    }
                }
            }
            item {
                Row(Modifier.fillMaxWidth().padding(top = 28.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("Your downloads", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                    if (records.any { !it.busy }) TextButton(onClick = { app.downloads.clearFinished() }) { Text("Clear finished") }
                }
            }
            if (records.isEmpty()) {
                item {
                    Text(
                        "Nothing yet. Downloads you start show up here, and the files go to Downloads/Grabbit.",
                        style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            items(records, key = { it.id }) { r ->
                DownloadRow(
                    r,
                    onCancel = { scope.launch { app.downloads.cancel(r) } },
                    onRetry = {
                        ask {
                            scope.launch {
                                runCatching { app.downloads.retry(r) }.onFailure { snackbar.showSnackbar(it.message ?: "Couldn't retry.") }
                            }
                        }
                    },
                    onRemove = { app.downloads.remove(r) },
                )
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            }
        }
    }
    }
}
