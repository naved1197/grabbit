package com.grabbit.android.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ExpandMore
import androidx.compose.material.icons.rounded.Movie
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil3.compose.SubcomposeAsyncImage

/** A thumbnail with a film placeholder, and an optional duration badge like the website's. */
@Composable
fun Thumbnail(url: String?, width: Dp, height: Dp, badge: String? = null, placeholder: ImageVector = Icons.Rounded.Movie) {
    Box(
        Modifier.size(width, height).clip(RoundedCornerShape(10.dp)).background(MaterialTheme.colorScheme.surfaceVariant),
        contentAlignment = Alignment.Center,
    ) {
        val fallback = @Composable {
            Icon(placeholder, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(width / 4))
        }
        if (url.isNullOrBlank()) fallback() else SubcomposeAsyncImage(
            model = url, contentDescription = null, contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize(),
            loading = { fallback() }, error = { fallback() },
        )
        if (badge != null) {
            Text(
                badge,
                color = Color.White,
                fontSize = 11.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.align(Alignment.BottomEnd).padding(4.dp)
                    .background(Color.Black.copy(alpha = 0.72f), RoundedCornerShape(6.dp)).padding(horizontal = 5.dp, vertical = 1.dp),
            )
        }
    }
}

/** A selectable card with a title, an optional badge and a subtitle (the website's quality chips). */
@Composable
fun OptionChip(title: String, subtitle: String?, selected: Boolean, badge: String? = null, enabled: Boolean = true, onClick: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    val border = if (selected) BorderStroke(2.dp, colors.primary) else BorderStroke(1.dp, colors.outlineVariant)
    Column(
        Modifier.clip(MaterialTheme.shapes.small)
            .background(if (selected) colors.primaryContainer else colors.surfaceContainerLow)
            .border(border, MaterialTheme.shapes.small)
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold,
                color = if (selected) colors.onPrimaryContainer else colors.onSurface)
            if (badge != null) {
                Text(badge, fontSize = 10.sp, fontWeight = FontWeight.Bold, color = colors.onPrimary,
                    modifier = Modifier.background(colors.primary, RoundedCornerShape(5.dp)).padding(horizontal = 4.dp))
            }
        }
        if (subtitle != null) {
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant, maxLines = 1)
        }
    }
}

/** A labelled drop-down, for format choices. */
@Composable
fun <T> Dropdown(label: String, options: List<Pair<T, String>>, selected: T, onSelect: (T) -> Unit, modifier: Modifier = Modifier) {
    var open by remember { mutableStateOf(false) }
    Column(modifier) {
        SectionLabel(label)
        Box {
            OutlinedCard(
                onClick = { open = true },
                modifier = Modifier.fillMaxWidth(),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
            ) {
                Row(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(options.firstOrNull { it.first == selected }?.second ?: "", modifier = Modifier.weight(1f),
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Icon(Icons.Rounded.ExpandMore, contentDescription = null)
                }
            }
            DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                options.forEach { (value, text) ->
                    DropdownMenuItem(text = { Text(text) }, onClick = { onSelect(value); open = false })
                }
            }
        }
    }
}

@Composable
fun SectionLabel(text: String) {
    Text(text, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 14.dp, bottom = 8.dp))
}

@Composable
fun SiteTag(text: String) {
    Text(
        text.uppercase(),
        fontSize = 10.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = 0.6.sp,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(bottom = 2.dp),
    )
}
