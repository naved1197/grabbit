package com.grabbit.android.signin

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.grabbit.android.ui.theme.GrabbitTheme
import kotlinx.coroutines.delay

/**
 * Sign in to one site inside Grabbit, so its cookies can go with that site's downloads.
 *
 * Signing in often means leaving for a moment (a code in Messages or email, approving the login in
 * the site's own app, a password manager). So this screen has its own task that shows in Recents,
 * keeps Grabbit running for a few minutes while you're away ([SignInKeepAlive], with a notification
 * that brings you back), and if Android stops Grabbit anyway, comes back on the same page.
 */
class SignInActivity : ComponentActivity() {
    private var webView: WebView? = null
    private var savedWeb: Bundle? = null
    private lateinit var site: Site
    private var beforeHash: String? = null
    private var finished = false

    @OptIn(ExperimentalMaterial3Api::class)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        site = SignIn.sites.firstOrNull { it.domain == intent.getStringExtra(EXTRA_SITE) } ?: run { finish(); return }
        // The session before signing in (as a hash: the cookie itself never leaves the cookie store),
        // kept across Android stopping the app, so a sign-in finished meanwhile is still noticed.
        beforeHash = if (savedInstanceState?.containsKey(KEY_BEFORE) == true) savedInstanceState.getString(KEY_BEFORE)
        else SignIn.sessionHash(site)
        savedWeb = savedInstanceState?.getBundle(KEY_WEB)
        setContent {
            GrabbitTheme {
                var progress by remember { mutableIntStateOf(0) }
                BackHandler {
                    val wv = webView
                    if (wv != null && wv.canGoBack()) wv.goBack() else done(signedIn = false)
                }
                // Close by itself once the site hands out a (new) signed-in session, so the download retries.
                LaunchedEffect(Unit) {
                    while (true) {
                        val now = SignIn.sessionHash(site)
                        if (now != null && now != beforeHash) {
                            Toast.makeText(this@SignInActivity, "Signed in to ${site.name}", Toast.LENGTH_SHORT).show()
                            done(signedIn = true)
                            break
                        }
                        delay(1_000)
                    }
                }
                Column(Modifier.fillMaxSize().systemBarsPadding().imePadding()) {
                    TopAppBar(
                        title = { Text("Sign in to ${site.name}") },
                        navigationIcon = {
                            IconButton(onClick = { done(signedIn = false) }) { Icon(Icons.Rounded.Close, contentDescription = "Close") }
                        },
                        actions = { TextButton(onClick = { done(signedIn = false) }) { Text("Done") } },
                    )
                    Text(
                        "Log in with your ${site.name} username and password. If you need a code from another app, " +
                            "go and get it: this page waits for you. Grabbit keeps the sign-in on your phone and sends " +
                            "it only to ${site.name}, with downloads from there.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 16.dp).padding(bottom = 8.dp),
                    )
                    if (progress in 1..99) LinearProgressIndicator({ progress / 100f }, Modifier.fillMaxWidth())
                    AndroidView(
                        modifier = Modifier.weight(1f).fillMaxWidth(),
                        factory = { ctx -> makeWebView(ctx) { progress = it } },
                    )
                }
            }
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun makeWebView(context: Context, onProgress: (Int) -> Unit): WebView {
        val wv = WebView(context)
        wv.layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        wv.setBackgroundColor(android.graphics.Color.WHITE)
        wv.settings.javaScriptEnabled = true
        wv.settings.domStorageEnabled = true
        // Some sites refuse sign-in from an app's WebView ("; wv"); look like the phone's browser.
        wv.settings.userAgentString = wv.settings.userAgentString.replace("; wv", "")
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(wv, true)
        wv.webViewClient = object : WebViewClient() {
            // Sites try to hand sign-in over to their own app (instagram://, intent://). That app's
            // sign-in can't reach Grabbit, so stay on the web page.
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val scheme = request.url.scheme?.lowercase()
                return scheme != "http" && scheme != "https"
            }

            override fun onPageFinished(view: WebView, url: String) {
                CookieManager.getInstance().flush() // keep a half-done sign-in if Android stops the app
                Log.i(TAG, "sign-in page: ${android.net.Uri.parse(url).path}")
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) Log.w(TAG, "sign-in page failed: ${error.errorCode} ${error.description}")
            }
        }
        wv.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) = onProgress(newProgress)
        }
        val restored = savedWeb?.let { wv.restoreState(it) }
        if (restored == null || restored.size == 0) wv.loadUrl(site.loginUrl)
        savedWeb = null
        webView = wv
        return wv
    }

    override fun onResume() {
        super.onResume()
        SignInKeepAlive.stop(this)
    }

    override fun onPause() {
        super.onPause()
        CookieManager.getInstance().flush()
        // Leaving for a code or the site's app: keep Grabbit running so this page is still here.
        if (!isFinishing && !finished) SignInKeepAlive.start(this, site)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(KEY_BEFORE, beforeHash)
        webView?.let { wv -> outState.putBundle(KEY_WEB, Bundle().also { wv.saveState(it) }) }
    }

    private fun done(signedIn: Boolean) {
        if (finished) return
        finished = true
        CookieManager.getInstance().flush()
        SignInKeepAlive.stop(this)
        SignIn.changed(signedIn)
        finishAndRemoveTask()
    }

    override fun onDestroy() {
        if (isFinishing) SignInKeepAlive.stop(this)
        webView?.destroy()
        webView = null
        super.onDestroy()
    }

    companion object {
        private const val TAG = "Grabbit"
        const val EXTRA_SITE = "site"
        private const val KEY_BEFORE = "before"
        private const val KEY_WEB = "web"

        /** Opens (or returns to) the sign-in screen in its own task. */
        fun intent(context: Context, site: Site) =
            Intent(context, SignInActivity::class.java).putExtra(EXTRA_SITE, site.domain)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
}
