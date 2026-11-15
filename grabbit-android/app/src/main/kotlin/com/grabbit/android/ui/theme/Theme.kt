package com.grabbit.android.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

// Grabbit's palette, from grabbit-web/app/static/styles.css.
private val Light = lightColorScheme(
    primary = Color(0xFF5B4CF0),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFEEECFF),
    onPrimaryContainer = Color(0xFF1F1670),
    secondary = Color(0xFF5D6072),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFEEECFF),
    onSecondaryContainer = Color(0xFF2A2470),
    background = Color(0xFFF6F6FB),
    onBackground = Color(0xFF14151C),
    surface = Color(0xFFF6F6FB),
    onSurface = Color(0xFF14151C),
    surfaceVariant = Color(0xFFECECF4),
    onSurfaceVariant = Color(0xFF5D6072),
    surfaceContainerLowest = Color.White,
    surfaceContainerLow = Color.White,
    surfaceContainer = Color(0xFFF1F1F8),
    surfaceContainerHigh = Color(0xFFECECF4),
    surfaceContainerHighest = Color(0xFFE6E6EF),
    outline = Color(0xFFC9CAD6),
    outlineVariant = Color(0xFFE2E2EC),
    error = Color(0xFFC9322F),
    onError = Color.White,
    errorContainer = Color(0xFFFDECEC),
    onErrorContainer = Color(0xFF7A1715),
)

private val Dark = darkColorScheme(
    primary = Color(0xFF8576FF),
    onPrimary = Color(0xFF10093A),
    primaryContainer = Color(0xFF241F4D),
    onPrimaryContainer = Color(0xFFE2DEFF),
    secondary = Color(0xFFA3A6B8),
    onSecondary = Color(0xFF14151C),
    secondaryContainer = Color(0xFF241F4D),
    onSecondaryContainer = Color(0xFFE2DEFF),
    background = Color(0xFF0C0D12),
    onBackground = Color(0xFFECEDF3),
    surface = Color(0xFF0C0D12),
    onSurface = Color(0xFFECEDF3),
    surfaceVariant = Color(0xFF1C1E27),
    onSurfaceVariant = Color(0xFFA3A6B8),
    surfaceContainerLowest = Color(0xFF0C0D12),
    surfaceContainerLow = Color(0xFF15161D),
    surfaceContainer = Color(0xFF181920),
    surfaceContainerHigh = Color(0xFF1C1E27),
    surfaceContainerHighest = Color(0xFF23252F),
    outline = Color(0xFF3A3D4C),
    outlineVariant = Color(0xFF2A2C38),
    error = Color(0xFFFF6B67),
    onError = Color(0xFF2B0505),
    errorContainer = Color(0xFF3A1414),
    onErrorContainer = Color(0xFFFFD9D7),
)

/** The headline gradient: accent to violet, like the website's "any video." */
val GrabbitGradient = Brush.linearGradient(listOf(Color(0xFF6A5AF9), Color(0xFFC04CF0)))

private val GrabbitShapes = Shapes(
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(18.dp),
)

@Composable
fun GrabbitTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) Dark else Light,
        shapes = GrabbitShapes,
        content = content,
    )
}
