package com.grabbit.android.ui

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Download
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.Lock
import androidx.compose.material.icons.rounded.Movie
import androidx.compose.material.icons.rounded.MusicNote
import androidx.compose.material.icons.rounded.Photo
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.WarningAmber
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TriStateCheckbox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.state.ToggleableState
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.grabbit.android.engine.Choice
import com.grabbit.android.engine.Entry
import com.grabbit.android.engine.Info
import com.grabbit.android.settings.Prefs
import com.grabbit.android.signin.SignIn
import com.grabbit.android.signin.Site
import kotlinx.coroutines.delay

/** Everything below the link: loading, the video or playlist picker, or what went wrong. */
@Composable
fun LookupPanel(
    state: LookupViewModel.State,
    prefs: Prefs.Values,
    busy: Boolean,
    onDownload: (Choice) -> Unit,
    onDownloadEntries: (List<Entry>, Choice) -> Unit,
    onSignIn: (Site) -> Unit,
    onRetry: () -> Unit,
) {
    when (state) {
        LookupViewModel.State.Idle -> Unit
        is LookupViewModel.State.Loading -> LoadingCard(state)
        is LookupViewModel.State.Failed -> ErrorCard(state, onSignIn, onRetry)
        is LookupViewModel.State.Loaded ->
            if (state.info.isPlaylist) PlaylistPicker(state.info, prefs, busy, onDownloadEntries)
            else VideoPicker(state.info, prefs, busy, onDownload)
    }
}

@Composable
private fun LoadingCard(state: LookupViewModel.State.Loading) {
    var slow by remember(state) { mutableStateOf(false) }
    LaunchedEffect(state) {
        delay(6_000)
        slow = true
    }
    val pulse by rememberInfiniteTransition(label = "pulse").animateFloat(
        0.45f, 1f, infiniteRepeatable(tween(700), RepeatMode.Reverse), label = "alpha",
    )
    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(120.dp, 68.dp).alpha(pulse).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(10.dp)))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                repeat(3) { i ->
                    Box(Modifier.fillMaxWidth(if (i == 2) 0.4f else 0.9f).height(12.dp).alpha(pulse)
                        .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(6.dp)))
                    Spacer(Modifier.height(8.dp))
                }
            }
        }
        Row(Modifier.padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
            Spacer(Modifier.width(10.dp))
            Text(
                if (slow) "Still working. YouTube links can take about 30 seconds on a phone." else "Getting video details…",
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun ErrorCard(state: LookupViewModel.State.Failed, onSignIn: (Site) -> Unit, onRetry: () -> Unit) {
    val site = state.site
    val signedIn = site != null && SignIn.isSignedIn(site)
    val needsLogin = state.code == "login_required"
    Surface(color = MaterialTheme.colorScheme.errorContainer, shape = MaterialTheme.shapes.medium, modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Icon(if (needsLogin) Icons.Rounded.Lock else Icons.Rounded.ErrorOutline, contentDescription = null,
                    tint = MaterialTheme.colorScheme.error, modifier = Modifier.padding(end = 10.dp, top = 1.dp))
                Column {
                    Text(state.message, color = MaterialTheme.colorScheme.onErrorContainer, style = MaterialTheme.typography.bodyMedium)
                    if (needsLogin && site != null) {
                        Text(
                            if (signedIn) "Your ${site.name} sign-in in Grabbit didn't work. It may have expired, or this " +
                                "account can't see this post: sign in again."
                            else "Grabbit can't use the ${site.name} app's sign-in: Android keeps each app's sign-ins to " +
                                "itself. Sign in to ${site.name} once inside Grabbit and it'll try again with your account.",
                            color = MaterialTheme.colorScheme.onErrorContainer, style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.padding(top = 6.dp),
                        )
                    }
                }
            }
            Row(Modifier.padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (needsLogin && site != null) {
                    Button(onClick = { onSignIn(site) }) { Text(if (signedIn) "Sign in again" else "Sign in to ${site.name}") }
                }
                if (state.url != null) {
                    OutlinedButton(onClick = onRetry) {
                        Icon(Icons.Rounded.Refresh, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Try again")
                    }
                }
            }
        }
    }
}

@Composable
fun MediaHeader(thumbnail: String?, site: String?, title: String, byline: String?, badge: String?) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Thumbnail(thumbnail, 128.dp, 72.dp, badge)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Fmt.siteName(site)?.let { SiteTag(it) }
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, maxLines = 3, overflow = TextOverflow.Ellipsis)
            if (!byline.isNullOrBlank()) {
                Text(byline, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
            }
        }
    }
}

private val CONTAINERS = listOf("mp4" to "MP4 · plays everywhere", "webm" to "WebM · VP9/AV1, smaller", "mkv" to "MKV · keeps original streams")

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun VideoPicker(info: Info, prefs: Prefs.Values, busy: Boolean, onDownload: (Choice) -> Unit) {
    val values = info.qualities.map { it.value }
    var mode by remember(info) { mutableStateOf(if (!info.hasVideo) "audio" else prefs.mode) }
    var quality by remember(info) { mutableStateOf(if (prefs.quality in values) prefs.quality else "best") }
    var container by remember(info) { mutableStateOf(prefs.container) }
    var audio by remember(info) { mutableStateOf(prefs.audioFormat) }
    val choice = Choice(mode, quality, container, audio)

    Column(Modifier.fillMaxWidth()) {
        MediaHeader(info.thumbnail, info.site, info.title, info.uploader, Fmt.duration(info.duration))
        info.warning?.let { Notice(it) }

        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().padding(top = 14.dp)) {
            SegmentedButton(
                selected = mode == "video", onClick = { mode = "video" }, enabled = info.hasVideo,
                shape = SegmentedButtonDefaults.itemShape(0, 2),
                icon = { Icon(Icons.Rounded.Movie, contentDescription = null, modifier = Modifier.size(18.dp)) },
                label = { Text("Video") },
            )
            SegmentedButton(
                selected = mode == "audio", onClick = { mode = "audio" },
                shape = SegmentedButtonDefaults.itemShape(1, 2),
                icon = { Icon(Icons.Rounded.MusicNote, contentDescription = null, modifier = Modifier.size(18.dp)) },
                label = { Text("Audio only") },
            )
        }

        if (mode == "video") {
            SectionLabel("Quality")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val top = info.qualities.firstOrNull()
                val bestSub = top?.let { listOfNotNull(it.label, Fmt.size(it.size)).joinToString(" · ") } ?: "Highest available"
                OptionChip("Best", bestSub, quality == "best") { quality = "best" }
                info.qualities.forEach { q ->
                    OptionChip(q.label, Fmt.size(q.size)?.let { "≈ $it" }, quality == q.value, q.badge) { quality = q.value }
                }
            }
            Dropdown("Format", CONTAINERS, container, { container = it }, Modifier.fillMaxWidth())
        } else {
            SectionLabel("Audio format")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(
                    "mp3" to ("MP3" to "Works everywhere"),
                    "m4a" to ("M4A" to "AAC, great on Apple"),
                    "opus" to ("Opus" to "Best per megabyte"),
                    "best" to ("Original" to listOfNotNull("No conversion", Fmt.size(info.audioSize)?.let { "≈ $it" }).joinToString(" · ")),
                ).forEach { (value, text) ->
                    OptionChip(text.first, text.second, audio == value) { audio = value }
                }
            }
        }

        Button(
            onClick = { onDownload(choice) },
            enabled = !busy && info.warning == null,
            modifier = Modifier.fillMaxWidth().padding(top = 20.dp).height(52.dp),
        ) {
            if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
            else Icon(Icons.Rounded.Download, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text(Fmt.downloadButton(choice), style = MaterialTheme.typography.titleSmall)
        }
    }
}

private val PLAYLIST_QUALITIES = listOf(
    "best" to "Best available", "2160" to "Up to 4K", "1080" to "Up to 1080p",
    "720" to "Up to 720p", "480" to "Up to 480p", "360" to "Up to 360p",
)
private val AUDIO_FORMATS = listOf("mp3" to "MP3", "m4a" to "M4A", "opus" to "Opus", "best" to "Original")

@Composable
private fun PlaylistPicker(info: Info, prefs: Prefs.Values, busy: Boolean, onDownload: (List<Entry>, Choice) -> Unit) {
    var selected by remember(info) { mutableStateOf(info.entries.indices.toSet()) }
    var mode by remember(info) { mutableStateOf(if (info.post) "video" else prefs.mode) }
    var quality by remember(info) { mutableStateOf(prefs.playlistQuality) }
    var audio by remember(info) { mutableStateOf(prefs.audioFormat) }
    val noun = when {
        info.post -> "items"
        mode == "audio" -> "tracks"
        else -> "videos"
    }
    val byline = listOfNotNull(
        info.uploader,
        if (info.post) listOfNotNull(
            info.videos.takeIf { it > 0 }?.let { "$it video${if (it == 1) "" else "s"}" },
            info.photos.takeIf { it > 0 }?.let { "$it photo${if (it == 1) "" else "s"}" },
        ).joinToString(" · ").ifEmpty { null } else "${info.count} videos",
    ).joinToString(" · ")

    Column(Modifier.fillMaxWidth()) {
        MediaHeader(info.thumbnail, info.site ?: if (info.post) "Post" else "Playlist", info.title, byline, null)

        Row(
            Modifier.fillMaxWidth().padding(top = 10.dp).clickable {
                selected = if (selected.size == info.entries.size) emptySet() else info.entries.indices.toSet()
            },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TriStateCheckbox(
                state = when (selected.size) {
                    0 -> ToggleableState.Off
                    info.entries.size -> ToggleableState.On
                    else -> ToggleableState.Indeterminate
                },
                onClick = { selected = if (selected.size == info.entries.size) emptySet() else info.entries.indices.toSet() },
            )
            Text("Select all", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            if (info.truncated) {
                Text("Showing the first ${info.entries.size}", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        info.entries.forEachIndexed { i, e ->
            Row(
                Modifier.fillMaxWidth().clickable { selected = if (i in selected) selected - i else selected + i }.padding(vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Checkbox(checked = i in selected, onCheckedChange = { selected = if (it) selected + i else selected - i })
                Thumbnail(e.thumbnail, 64.dp, 36.dp, placeholder = if (e.photo) Icons.Rounded.Photo else Icons.Rounded.Movie)
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(e.title ?: e.url, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    e.uploader?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1) }
                }
                Text(if (e.photo) "Photo" else Fmt.duration(e.duration) ?: "", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(start = 8.dp))
            }
        }

        if (!info.post || info.videos > 0) {
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().padding(top = 14.dp)) {
                SegmentedButton(selected = mode == "video", onClick = { mode = "video" }, shape = SegmentedButtonDefaults.itemShape(0, 2),
                    label = { Text("Video") })
                SegmentedButton(selected = mode == "audio", onClick = { mode = "audio" }, shape = SegmentedButtonDefaults.itemShape(1, 2),
                    label = { Text("Audio only") })
            }
            if (mode == "video") Dropdown("Quality", PLAYLIST_QUALITIES, quality, { quality = it }, Modifier.fillMaxWidth())
            else Dropdown("Audio format", AUDIO_FORMATS, audio, { audio = it }, Modifier.fillMaxWidth())
        }

        val picked = info.entries.filterIndexed { i, _ -> i in selected }
        Button(
            onClick = { onDownload(picked, Choice(mode, quality, prefs.container, audio)) },
            enabled = !busy && picked.isNotEmpty(),
            modifier = Modifier.fillMaxWidth().padding(top = 20.dp).height(52.dp),
        ) {
            if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
            else Icon(Icons.Rounded.Download, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text(if (picked.isEmpty()) "Select $noun" else "Download ${picked.size} $noun", style = MaterialTheme.typography.titleSmall)
        }
    }
}

@Composable
private fun Notice(text: String) {
    Row(
        Modifier.fillMaxWidth().padding(top = 10.dp).background(MaterialTheme.colorScheme.errorContainer, MaterialTheme.shapes.small).padding(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Rounded.WarningAmber, contentDescription = null, tint = MaterialTheme.colorScheme.error)
        Spacer(Modifier.width(8.dp))
        Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onErrorContainer)
    }
}
