package com.grabbit.android.signin

import android.webkit.CookieManager
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.net.URI

/**
 * Signed-in downloads, the Android way: the person signs in to a site once inside Grabbit, and its
 * cookies go with lookups and downloads for that site (what the extension's "Use my logins" does).
 */
/** [session]: cookies a site only sets once you're signed in (just visiting sets others). */
data class Site(
    val name: String,
    val domain: String,
    val loginUrl: String,
    val session: List<String>,
    val aliases: List<String> = emptyList(),
) {
    val hosts get() = listOf(domain) + aliases
}

object SignIn {
    val sites = listOf(
        Site("Instagram", "instagram.com", "https://www.instagram.com/accounts/login/", listOf("sessionid"), listOf("instagr.am")),
        Site("X", "x.com", "https://x.com/i/flow/login", listOf("auth_token"), listOf("twitter.com")),
        Site("TikTok", "tiktok.com", "https://www.tiktok.com/login", listOf("sessionid", "sid_tt")),
        Site("Facebook", "facebook.com", "https://m.facebook.com/login/", listOf("c_user"), listOf("fb.watch")),
        Site("Reddit", "reddit.com", "https://www.reddit.com/login/", listOf("reddit_session"), listOf("redd.it")),
        Site("Vimeo", "vimeo.com", "https://vimeo.com/log_in", listOf("vimeo")),
    )

    /** Whether these `Cookie:` headers include one of the site's signed-in cookies. */
    fun hasSession(headers: Map<String, String?>, names: List<String>): Boolean = headers.values.any { header ->
        header.orEmpty().split(';').any { part -> part.substringBefore('=').trim() in names && part.substringAfter('=', "").isNotBlank() }
    }

    fun hostOf(url: String): String =
        runCatching { URI(url.trim()).host }.getOrNull().orEmpty().lowercase().removePrefix("www.").removePrefix("m.")

    fun siteFor(url: String): Site? {
        val host = hostOf(url)
        if (host.isEmpty()) return null
        return sites.firstOrNull { s -> s.hosts.any { host == it || host.endsWith(".$it") } }
    }

    private fun cookieHeader(host: String): String? =
        runCatching { CookieManager.getInstance().getCookie("https://$host") }.getOrNull()?.takeIf { it.isNotBlank() }

    fun isSignedIn(site: Site): Boolean = hasSession(site.hosts.associateWith { cookieHeader(it) }, site.session)

    /** A hash of the site's session cookie (only compared, to notice a fresh sign-in; the cookie is never shown or logged). */
    fun sessionHash(site: Site): String? = site.hosts.mapNotNull { cookieHeader(it) }.flatMap { it.split(';') }
        .map { it.trim() }
        .firstOrNull { part -> part.substringBefore('=') in site.session && part.substringAfter('=', "").isNotBlank() }
        ?.let { s -> java.security.MessageDigest.getInstance("SHA-256").digest(s.toByteArray()).joinToString("") { "%02x".format(it) } }

    private val _changes = kotlinx.coroutines.flow.MutableStateFlow(0)
    /** Bumps when a sign-in screen closes or a site is signed out, so open screens refresh or retry. */
    val changes: kotlinx.coroutines.flow.StateFlow<Int> = _changes

    private val _lastSignedIn = kotlinx.coroutines.flow.MutableStateFlow(false)
    /** Whether the last sign-in screen ended signed in (to retry a lookup automatically). */
    val lastSignedIn: kotlinx.coroutines.flow.StateFlow<Boolean> = _lastSignedIn

    fun changed(signedIn: Boolean) {
        _lastSignedIn.value = signedIn
        _changes.value++
    }

    /** The cookies to send for [url], or null when the person hasn't signed in to that site here. */
    fun cookiesFor(url: String): String? {
        val site = siteFor(url) ?: return null
        val headers = site.hosts.associateWith { cookieHeader(it) }
        return if (hasSession(headers, site.session)) rows(headers) else null
    }

    /**
     * Cookie-Editor style JSON (grabbit-web's cookies.py reads it) from `Cookie:` headers per host.
     * WebView only reveals name=value, so every cookie is sent for the whole site. Sorted, so the
     * lookup and the download send byte-identical text (the engine's info cache is keyed on it).
     */
    fun rows(headers: Map<String, String?>): String? {
        val rows = headers.toSortedMap().flatMap { (host, header) ->
            header.orEmpty().split(';').mapNotNull { part ->
                val eq = part.indexOf('=')
                if (eq <= 0) return@mapNotNull null
                val name = part.substring(0, eq).trim()
                val value = part.substring(eq + 1).trim()
                if (name.isEmpty()) null else Triple(host, name, value)
            }
        }.distinctBy { it.first to it.second }.sortedWith(compareBy({ it.first }, { it.second }))
        if (rows.isEmpty()) return null
        return JsonArray(rows.map { (host, name, value) ->
            buildJsonObject {
                put("domain", ".$host")
                put("hostOnly", false)
                put("path", "/")
                put("secure", true)
                put("expirationDate", 0)
                put("name", name)
                put("value", value)
            }
        }).toString()
    }

    /** WebView can't delete one site's cookies, so expire each one by name. */
    fun signOut(site: Site) {
        val cm = CookieManager.getInstance()
        for (host in site.hosts) {
            val header = cookieHeader(host) ?: continue
            header.split(';').map { it.substringBefore('=').trim() }.filter { it.isNotEmpty() }.forEach { name ->
                for (domain in listOf(host, ".$host", "www.$host")) {
                    cm.setCookie("https://$host", "$name=; Max-Age=0; Path=/; Domain=$domain")
                }
                cm.setCookie("https://$host", "$name=; Max-Age=0; Path=/")
            }
        }
        cm.flush()
        changed(signedIn = false)
    }
}
