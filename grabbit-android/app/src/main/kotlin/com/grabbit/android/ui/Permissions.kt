package com.grabbit.android.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import com.grabbit.android.app

/**
 * Before the first download, ask once to show notifications (Android 13+): that's where progress
 * and "Saved" appear. Whatever the answer, [then] runs; downloads work without notifications too.
 */
@Composable
fun rememberNotificationAsk(): (then: () -> Unit) -> Unit {
    val context = LocalContext.current
    val pending = remember { arrayOfNulls<() -> Unit>(1) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        pending[0]?.invoke()
        pending[0] = null
    }
    return remember(launcher) {
        { then ->
            val prefs = context.app.prefs
            val needed = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            if (needed && !prefs.askedNotifications) {
                prefs.askedNotifications = true
                pending[0] = then
                launcher.launch(Manifest.permission.POST_NOTIFICATIONS)
            } else {
                then()
            }
        }
    }
}

/** After a sign-in screen closes signed in, look the link up again if it failed for want of one. */
@Composable
fun RetryAfterSignIn(vm: LookupViewModel) {
    val changes by com.grabbit.android.signin.SignIn.changes.collectAsStateWithLifecycle()
    val seen = remember { intArrayOf(com.grabbit.android.signin.SignIn.changes.value) }
    LaunchedEffect(changes) {
        if (changes == seen[0]) return@LaunchedEffect
        seen[0] = changes
        val s = vm.state.value
        if (com.grabbit.android.signin.SignIn.lastSignedIn.value &&
            s is LookupViewModel.State.Failed && s.code == "login_required"
        ) vm.retry()
    }
}
