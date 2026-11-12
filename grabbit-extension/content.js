// Grabbit content script: a download button on every video, audio and image,
// plus progress toasts for downloads that go through the Grabbit server.
(() => {
  "use strict";
  if (window.__grabbitLoaded) return;
  window.__grabbitLoaded = true;

  const DEFAULTS = { showOnImages: true, showOnVideos: true, minImageSize: 120 };
  let settings = { ...DEFAULTS };
  chrome.storage.sync.get(DEFAULTS).then((s) => { settings = s; }).catch(() => {});
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "sync") return;
    for (const [k, v] of Object.entries(changes)) if (k in DEFAULTS) settings[k] = v.newValue;
  });

  const isTop = window.top === window;

  /* ================================================================ media */

  function isDirect(url) {
    return /^(https?:|data:)/i.test(url || "") && !/\.(m3u8|mpd)(\?|#|$)/i.test(url);
  }

  function mediaSrc(el) {
    const s = el.currentSrc || el.src || (el.querySelector && el.querySelector("source[src]")?.src) || "";
    return s;
  }

  function bestFromSrcset(srcset) {
    let best = null;
    let bestScore = 0;
    const re = /(\S+?)(?:\s+(\d+(?:\.\d+)?)([wx]))?\s*(?:,\s*|$)/g;
    let m;
    while ((m = re.exec(srcset)) && m[0]) {
      const score = m[2] ? parseFloat(m[2]) * (m[3] === "x" ? 1000 : 1) : 1;
      if (score > bestScore) { bestScore = score; best = m[1]; }
    }
    return best;
  }

  function imageUrl(img) {
    let url = img.currentSrc || img.src || "";
    if (img.srcset) {
      const big = bestFromSrcset(img.srcset);
      if (big) url = new URL(big, location.href).href;
    }
    if (!url || (/^data:image\/(gif|svg)/i.test(url) && img.naturalWidth <= 2)) return null;
    try {
      const u = new URL(url, location.href);
      if (u.hostname === "pbs.twimg.com" && u.pathname.startsWith("/media/")) u.searchParams.set("name", "orig");
      return u.href;
    } catch {
      return url;
    }
  }

  function classify(node) {
    if (node instanceof HTMLVideoElement) {
      if (!settings.showOnVideos) return null;
      const r = node.getBoundingClientRect();
      return r.width >= 120 && r.height >= 70 ? { el: node, kind: "video" } : null;
    }
    if (node instanceof HTMLAudioElement) {
      const r = node.getBoundingClientRect();
      return r.width >= 100 && r.height >= 20 ? { el: node, kind: "audio" } : null;
    }
    if (node instanceof HTMLImageElement) {
      if (!settings.showOnImages) return null;
      const r = node.getBoundingClientRect();
      const min = settings.minImageSize || 120;
      return r.width >= min && r.height >= min && imageUrl(node) ? { el: node, kind: "image" } : null;
    }
    return null;
  }

  function inside(r, x, y) {
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  }

  /** The media element under the pointer, even beneath transparent overlays. */
  function mediaAt(x, y) {
    const stack = document.elementsFromPoint(x, y);
    for (const node of stack) {
      if (node === host) return "ui";
      const m = classify(node);
      if (m) return m;
    }
    // Players often cover <video> with pointer-events:none layers that
    // elementsFromPoint skips, so check videos by geometry too.
    for (const v of document.querySelectorAll("video")) {
      if (inside(v.getBoundingClientRect(), x, y)) {
        const m = classify(v);
        if (m) return m;
      }
    }
    return null;
  }

  /**
   * Walk up from the media to the first element that links to exactly one
   * post (matching ``re``, whose 2nd group is the post id). Stops once an
   * element links to several posts: that's the feed, past the post.
   */
  function nearestPostLink(el, re) {
    for (let n = el.parentElement, depth = 0; n && n !== document.body && depth < 30; n = n.parentElement, depth++) {
      let found = null;
      for (const a of n.querySelectorAll("a[href]")) {
        const m = re.exec(a.getAttribute("href"));
        if (!m) continue;
        if (found && found[2] !== m[2]) return null;
        found = found || m;
      }
      if (found) return found;
    }
    return null;
  }

  /** The link that identifies the post holding this media (for feeds with many videos). */
  function permalinkFor(el) {
    const h = location.hostname.replace(/^(www|m|mobile)\./, "");
    const abs = (a) => (a ? new URL(a.getAttribute("href"), location.href).href : null);
    const pick = (root, sel) => (root ? abs(root.querySelector(sel)) : null);
    try {
      if (h.endsWith("youtube.com")) {
        if (/^\/(watch|shorts\/|embed\/|live\/)/.test(location.pathname)) return location.href;
        const card = el.closest("ytd-rich-item-renderer, ytd-video-renderer, ytd-compact-video-renderer, ytd-grid-video-renderer, ytd-reel-item-renderer, yt-lockup-view-model, ytm-shorts-lockup-view-model");
        return pick(card, 'a[href*="/watch"], a[href*="/shorts/"]') || location.href;
      }
      if (h.endsWith("instagram.com")) {
        const re = /\/(p|reels?|tv)\/(?!audio\/)([\w-]+)/;
        // A feed post's timestamp links to it; failing that, the nearest
        // element around the media that links to exactly one post is the post.
        const time = el.closest("article")?.querySelector('a[href*="/p/"]:has(time), a[href*="/reel/"]:has(time)');
        const m = (time && re.exec(time.getAttribute("href"))) || nearestPostLink(el, re) || re.exec(location.pathname);
        return m ? `https://www.instagram.com/${m[1] === "reels" ? "reel" : m[1]}/${m[2]}/` : location.href;
      }
      if (h === "x.com" || h.endsWith("twitter.com")) {
        const tweet = el.closest("article");
        if (tweet) {
          const links = [...tweet.querySelectorAll('a[href*="/status/"]')];
          const a = links.find((l) => l.querySelector("time")) || links[0];
          if (a) return abs(a).replace(/\/(photo|video|analytics)\/\d*$/, "");
        }
        return location.href;
      }
      if (h.endsWith("tiktok.com")) {
        const item = el.closest('[data-e2e="recommend-list-item-container"], [class*="ItemContainer"], article');
        return pick(item, 'a[href*="/video/"]') || location.href;
      }
      if (h.endsWith("reddit.com")) {
        const post = el.closest("shreddit-post");
        if (post && post.getAttribute("permalink")) return new URL(post.getAttribute("permalink"), location.origin).href;
        return pick(el.closest("article, [data-testid='post-container']"), 'a[href*="/comments/"]') || location.href;
      }
      if (h.endsWith("facebook.com")) {
        return pick(el.closest('[role="article"]'), 'a[href*="/videos/"], a[href*="/reel/"], a[href*="/watch/?v="]') || location.href;
      }
    } catch { /* fall back to the page */ }
    return location.href;
  }

  /**
   * Which slide of a multi-photo/video post this media is on (1-based), or null.
   * Instagram lays a carousel out as <li>s inside a <ul>, each shifted
   * sideways by (position × slide width), and keeps ?img_index=N in the URL
   * on post pages.
   */
  function slideIndex(el) {
    if (!location.hostname.endsWith("instagram.com")) return null;
    const li = el.closest("li");
    const ul = li && li.parentElement;
    if (ul && ul.tagName === "UL") {
      const r = li.getBoundingClientRect();
      const dx = r.left - ul.getBoundingClientRect().left;
      const i = Math.round(dx / r.width);
      if (r.width > 50 && i >= 0 && Math.abs(dx - i * r.width) < 2) return i + 1;
    }
    const n = Number(new URLSearchParams(location.search).get("img_index"));
    return n >= 1 ? n : null;
  }

  function cleanTitle() {
    return document.title.replace(/^\(\d+\)\s*/, "").replace(/\s*[-|•]\s*(YouTube|Instagram|TikTok|X|Reddit|Facebook|Vimeo)\s*$/i, "").trim();
  }

  function nameFrom(url, kind) {
    const extFor = { image: "jpg", video: "mp4", audio: "mp3" };
    try {
      if (url.startsWith("data:")) {
        const mime = /^data:([^;,]+)/.exec(url)?.[1] || "";
        return `${kind}-${Date.now()}.${mime.split("/")[1]?.replace("jpeg", "jpg").replace("svg+xml", "svg") || extFor[kind]}`;
      }
      const u = new URL(url, location.href);
      let base = decodeURIComponent(u.pathname.split("/").filter(Boolean).pop() || "");
      const fmt = u.searchParams.get("format");
      if (base && !/\.[a-z0-9]{2,5}$/i.test(base)) base += `.${fmt || extFor[kind]}`;
      if (!base || base.length < 5) base = `${cleanTitle() || kind}.${fmt || extFor[kind]}`;
      return base;
    } catch {
      return `${kind}-${Date.now()}.${extFor[kind]}`;
    }
  }

  /* =================================================================== UI */

  const SVG_NS = "http://www.w3.org/2000/svg";
  const ICONS = {
    download: ["M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5", "M5 19h14"],
    chevron: ["M7 10l5 5 5-5"],
    check: ["M5 12.5l4.5 4.5L19 7.5"],
    x: ["M6 6l12 12M18 6 6 18"],
    film: ["M4 5h16v14H4z", "M8 5v14M16 5v14"],
    music: ["M9 18V5l11-2v13", "M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM20 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"],
    file: ["M6 3h8l4 4v14H6z", "M14 3v4h4"],
    alert: ["M12 9v4M12 17h.01", "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"],
  };

  function svg(name) {
    const s = document.createElementNS(SVG_NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("aria-hidden", "true");
    for (const d of ICONS[name]) {
      const p = document.createElementNS(SVG_NS, "path");
      p.setAttribute("d", d);
      s.append(p);
    }
    return s;
  }

  function h(tag, props = {}, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids) if (kid != null && kid !== false) n.append(kid);
    return n;
  }

  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; }
    .wrap { position: fixed; top: 0; left: 0; z-index: 2147483647; display: flex; align-items: flex-start;
      font: 600 13px/1 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; opacity: 0; pointer-events: none;
      transition: opacity .12s ease; }
    .wrap.show { opacity: 1; pointer-events: auto; }
    .btns { display: flex; border-radius: 999px; box-shadow: 0 2px 12px rgba(0,0,0,.35); overflow: hidden; }
    button { all: unset; box-sizing: border-box; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
      color: #fff; font: inherit; }
    .main, .more { height: 32px; background: rgba(18, 18, 28, .82); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
      transition: background .12s; }
    .main { padding: 0 13px 0 11px; }
    .main.icon-only { padding: 0 9px; }
    .more { padding: 0 8px; border-left: 1px solid rgba(255,255,255,.18); }
    .main:hover, .more:hover, .more[aria-expanded="true"] { background: #5b4cf0; }
    .main.ok { background: #138a4b; }
    .main.err { background: #c9322f; }
    svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; flex: none; }
    .menu { position: absolute; top: 38px; right: 0; min-width: 214px; padding: 6px; border-radius: 14px; background: #17181f;
      box-shadow: 0 12px 34px rgba(0,0,0,.45); display: none; }
    .menu.open { display: block; }
    .menu button { width: 100%; padding: 9px 10px; border-radius: 9px; font-weight: 600; }
    .menu button:hover, .menu button:focus-visible { background: rgba(255,255,255,.1); }
    .menu small { display: block; font-weight: 500; opacity: .6; margin-top: 2px; }
    .menu .col { display: grid; }
    .toasts { position: fixed; right: 16px; bottom: 16px; z-index: 2147483647; display: grid; gap: 8px; width: min(340px, calc(100vw - 32px));
      font: 500 13px/1.35 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
    .toast { position: relative; display: grid; gap: 6px; padding: 12px 38px 12px 14px; border-radius: 14px; background: #17181f;
      color: #fff; box-shadow: 0 10px 30px rgba(0,0,0,.4); animation: rise .18s ease-out; }
    .toast .t { font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: flex; gap: 7px; align-items: center; }
    .toast .t svg { width: 15px; height: 15px; color: #a99eff; }
    .toast .s { opacity: .8; overflow-wrap: anywhere; }
    .toast.error .s { color: #ff8f8a; opacity: 1; }
    .toast.done .t svg { color: #43c983; }
    .toast .bar { height: 4px; border-radius: 99px; background: rgba(255,255,255,.14); overflow: hidden; }
    .toast .bar i { display: block; height: 100%; width: 0; border-radius: inherit; background: #8576ff; transition: width .35s ease; }
    .toast .bar.indet i { width: 35%; animation: slide 1.1s ease-in-out infinite; }
    .toast.done .bar i { width: 100%; background: #43c983; animation: none; }
    .toast.error .bar { display: none; }
    .toast .x { position: absolute; top: 9px; right: 9px; width: 24px; height: 24px; justify-content: center; border-radius: 7px; opacity: .6; }
    .toast .x:hover { opacity: 1; background: rgba(255,255,255,.1); }
    .toast .x svg { width: 14px; height: 14px; }
    @keyframes slide { from { transform: translateX(-110%); } to { transform: translateX(300%); } }
    @keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
  `;

  const host = document.createElement("grabbit-ui");
  // CSSOM (not a style attribute), so strict page CSPs can't block it.
  host.style.cssText = "all: initial; position: fixed; top: 0; left: 0; width: 0; height: 0; z-index: 2147483647;";
  const root = host.attachShadow({ mode: "open" });
  try {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(CSS); // constructable sheets aren't blocked by page CSP
    root.adoptedStyleSheets = [sheet];
  } catch {
    root.append(h("style", { text: CSS }));
  }

  const mainBtn = h("button", { class: "main", type: "button", title: "Download" });
  const moreBtn = h("button", { class: "more", type: "button", title: "More download options", "aria-haspopup": "menu", "aria-expanded": "false" }, svg("chevron"));
  const menu = h("div", { class: "menu", role: "menu" });
  const wrap = h("div", { class: "wrap" }, h("div", { class: "btns" }, mainBtn, moreBtn), menu);
  const toasts = h("div", { class: "toasts" });
  root.append(wrap, toasts);

  function mount() {
    if (!host.isConnected) (document.documentElement || document.body).append(host);
  }

  // Keep page handlers (e.g. "open post on click") away from our buttons.
  for (const type of ["pointerdown", "pointerup", "mousedown", "mouseup", "click", "dblclick", "contextmenu", "touchstart"]) {
    wrap.addEventListener(type, (e) => e.stopPropagation());
    toasts.addEventListener(type, (e) => e.stopPropagation());
  }

  let target = null;
  let hideTimer = 0;
  let rafPending = false;
  let px = -1;
  let py = -1;
  let menuOpen = false;
  let flashTimer = 0;

  function setMain(kind) {
    mainBtn.className = "main";
    mainBtn.replaceChildren(svg("download"));
    if (kind === "image") {
      mainBtn.classList.add("icon-only");
      mainBtn.title = "Download image";
      mainBtn.setAttribute("aria-label", "Download image");
    } else {
      mainBtn.append(document.createTextNode("Download"));
      mainBtn.title = kind === "audio" ? "Download audio" : "Download video";
      mainBtn.setAttribute("aria-label", mainBtn.title);
    }
    moreBtn.hidden = kind === "image";
    mainBtn.parentElement.style.borderRadius = "999px";
  }

  function show(hit) {
    mount();
    target = hit;
    closeMenu();
    setMain(hit.kind);
    place();
    wrap.classList.add("show");
  }

  function hide() {
    if (menuOpen) return;
    target = null;
    wrap.classList.remove("show");
  }

  function place() {
    if (!target) return;
    const r = target.el.getBoundingClientRect();
    if (!r.width || !r.height || r.bottom < 0 || r.top > innerHeight || !target.el.isConnected) { hide(); return; }
    const w = wrap.offsetWidth || 120;
    const x = Math.max(8, Math.min(r.right, innerWidth) - w - 10);
    const y = Math.min(Math.max(r.top, 0) + 10, Math.max(r.bottom - 42, 8));
    wrap.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  }

  function scan() {
    rafPending = false;
    if (menuOpen || px < 0) return;
    const hit = mediaAt(px, py);
    if (hit === "ui") { clearTimeout(hideTimer); return; }
    if (hit) {
      clearTimeout(hideTimer);
      if (!target || hit.el !== target.el) show(hit);
      else place();
    } else if (target) {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(hide, 350);
    }
  }

  document.addEventListener("mousemove", (e) => {
    px = e.clientX;
    py = e.clientY;
    if (!rafPending) { rafPending = true; requestAnimationFrame(scan); }
  }, { capture: true, passive: true });
  addEventListener("scroll", () => { if (target) place(); }, { capture: true, passive: true });
  addEventListener("resize", () => { if (target) place(); }, { passive: true });
  document.addEventListener("mouseleave", () => { if (!menuOpen) { clearTimeout(hideTimer); hideTimer = setTimeout(hide, 350); } });

  function flash(ok, text) {
    clearTimeout(flashTimer);
    mainBtn.className = `main ${ok ? "ok" : "err"}`;
    mainBtn.replaceChildren(svg(ok ? "check" : "alert"), document.createTextNode(text));
    flashTimer = setTimeout(() => { if (target) setMain(target.kind); place(); }, ok ? 1400 : 2600);
    place();
  }

  /* --------------------------------------------------------------- menu */

  function closeMenu() {
    menuOpen = false;
    menu.classList.remove("open");
    moreBtn.setAttribute("aria-expanded", "false");
  }

  function openMenu() {
    if (!target) return;
    const t = target;
    const src = mediaSrc(t.el);
    const items = [
      ["film", "Best quality", "Video, highest resolution", () => viaGrabbit(t, "video", "best")],
      ["film", "720p", "Smaller file", () => viaGrabbit(t, "video", "720")],
      ["music", "Audio only", "MP3", () => viaGrabbit(t, "audio")],
    ];
    if (isDirect(src)) items.push(["file", "This file as is", "Direct download, no server", () => direct(src, nameFrom(src, t.kind))]);
    menu.replaceChildren(...items.map(([icon, label, sub, fn]) => h("button", {
      type: "button", role: "menuitem",
      onclick: (e) => { e.preventDefault(); closeMenu(); fn(); },
    }, svg(icon), h("span", { class: "col" }, label, h("small", { text: sub })))));
    menuOpen = true;
    menu.classList.add("open");
    moreBtn.setAttribute("aria-expanded", "true");
    menu.querySelector("button")?.focus({ preventScroll: true });
  }

  moreBtn.addEventListener("click", (e) => { e.preventDefault(); menuOpen ? closeMenu() : openMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && menuOpen) { closeMenu(); hide(); } }, true);
  document.addEventListener("pointerdown", (e) => { if (menuOpen && !e.composedPath().includes(host)) { closeMenu(); hide(); } }, true);

  /* ------------------------------------------------------------ actions */

  function send(msg) {
    return chrome.runtime.sendMessage(msg).catch((err) => ({ ok: false, error: String(err.message || err) }));
  }

  async function direct(url, filename) {
    if (url.startsWith("blob:")) {
      const a = h("a", { href: url, download: filename });
      a.style.display = "none";
      document.documentElement.append(a);
      a.click();
      a.remove();
      flash(true, "Saved");
      return;
    }
    const r = await send({ type: "download-direct", url, filename });
    if (r && r.ok) flash(true, "Saved");
    else {
      flash(false, "Failed");
      toast({ id: `d-${Date.now()}`, title: filename, text: (r && r.error) || "Download failed", state: "error" });
    }
  }

  async function viaGrabbit(t, mode, quality) {
    const url = permalinkFor(t.el);
    const sameAsPage = url === location.href;
    const reqId = crypto.randomUUID();
    if (location.hostname.endsWith("instagram.com") && !/^\/(p|reels?|tv|stories)\//.test(new URL(url).pathname)) {
      // The feed's own address isn't a video; sending it would only fail on the server.
      flash(false, "Failed");
      toast({ id: reqId, title: "instagram.com", state: "error",
        text: "Couldn't tell which post this is. Open the post (click its date), then click Download again." });
      return;
    }
    toast({ id: reqId, title: sameAsPage ? cleanTitle() || location.hostname : location.hostname, text: "Sending to Grabbit…", state: "working" });
    flash(true, "Sent");
    const r = await send({
      type: "grabbit", reqId, url, mode, quality,
      title: sameAsPage ? cleanTitle() : null,
      thumbnail: (t.el.poster && !t.el.poster.startsWith("data:")) ? t.el.poster : null,
      // Hints so the server picks the right item of a multi-item post.
      item: slideIndex(t.el),
      duration: Number.isFinite(t.el.duration) && t.el.duration > 0 ? t.el.duration : null,
    });
    if (!r || !r.ok) toast({ id: reqId, title: "Grabbit", text: (r && r.error) || "Couldn't start the download.", state: "error" });
  }

  async function blobIsFile(url) {
    // Blob URLs made from a whole file can be saved directly; MediaSource
    // streams (YouTube, Instagram…) can't be fetched and need the server.
    try {
      const res = await fetch(url);
      const blob = await res.blob();
      return blob.size > 0 ? URL.createObjectURL(blob) : null;
    } catch {
      return null;
    }
  }

  mainBtn.addEventListener("click", async (e) => {
    e.preventDefault();
    if (!target) return;
    const t = target;
    if (t.kind === "image") {
      const url = imageUrl(t.el);
      if (url) direct(url, nameFrom(url, "image"));
      return;
    }
    const src = mediaSrc(t.el);
    if (t.el.mediaKeys) {
      toast({ id: `drm-${Date.now()}`, title: "Protected video", text: "This video is DRM-protected and can't be downloaded.", state: "error" });
      return;
    }
    if (isDirect(src)) return direct(src, nameFrom(src, t.kind));
    if (src.startsWith("blob:")) {
      const fileUrl = await blobIsFile(src);
      if (fileUrl) return direct(fileUrl, `${cleanTitle() || t.kind}.${t.kind === "audio" ? "mp3" : "mp4"}`);
    }
    viaGrabbit(t, t.kind === "audio" ? "audio" : "video");
  });

  /* ------------------------------------------------------------- toasts */

  const toastNodes = new Map();

  function toast({ id, title, text, state, progress }) {
    if (!isTop) return; // only the top frame shows toasts
    mount();
    let n = toastNodes.get(id);
    if (!n) {
      const close = h("button", { class: "x", type: "button", title: "Dismiss", "aria-label": "Dismiss" }, svg("x"));
      n = h("div", { class: "toast", role: "status" },
        h("div", { class: "t" }, svg("download"), h("span")),
        h("div", { class: "s" }),
        h("div", { class: "bar" }, h("i")),
        close);
      close.addEventListener("click", () => { n.remove(); toastNodes.delete(id); });
      toastNodes.set(id, n);
      toasts.append(n);
      while (toasts.children.length > 4) {
        const first = toasts.firstElementChild;
        for (const [k, v] of toastNodes) if (v === first) toastNodes.delete(k);
        first.remove();
      }
    }
    n.className = `toast ${state || "working"}`;
    if (title) n.querySelector(".t span").textContent = title;
    n.querySelector(".s").textContent = text || "";
    const bar = n.querySelector(".bar");
    bar.classList.toggle("indet", state === "working" && progress == null);
    bar.firstChild.style.width = progress != null ? `${progress}%` : "";
    clearTimeout(n._timer);
    if (state === "done" || state === "error") {
      n._timer = setTimeout(() => { n.remove(); toastNodes.delete(id); }, state === "done" ? 5000 : 12000);
    }
  }

  /* ----------------------------------------------------------- messages */

  function listMedia() {
    const seen = new Set();
    const images = [];
    for (const img of document.images) {
      if (img.naturalWidth < 100 || img.naturalHeight < 100) continue;
      const url = imageUrl(img);
      if (!url || url.startsWith("blob:") || seen.has(url)) continue;
      seen.add(url);
      images.push({ url, name: nameFrom(url, "image"), w: img.naturalWidth, h: img.naturalHeight });
      if (images.length >= 400) break;
    }
    const videos = [];
    for (const v of document.querySelectorAll("video")) {
      const src = mediaSrc(v);
      const page = permalinkFor(v);
      const key = isDirect(src) ? src : page;
      if (seen.has(key) || (!src && !v.poster)) continue;
      seen.add(key);
      videos.push({
        src: isDirect(src) ? src : null, page, poster: v.poster && !v.poster.startsWith("data:") ? v.poster : null,
        w: v.videoWidth || null, h: v.videoHeight || null, duration: Number.isFinite(v.duration) ? v.duration : null,
        name: isDirect(src) ? nameFrom(src, "video") : null, item: slideIndex(v),
      });
    }
    const audio = [];
    for (const a of document.querySelectorAll("audio")) {
      const src = mediaSrc(a);
      if (!src || seen.has(src)) continue;
      seen.add(src);
      audio.push({ src: isDirect(src) ? src : null, page: location.href, name: isDirect(src) ? nameFrom(src, "audio") : null,
        duration: Number.isFinite(a.duration) ? a.duration : null });
    }
    return { url: location.href, title: cleanTitle() || document.title, images, videos, audio };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "grabbit-status") toast(msg);
    else if (msg && msg.type === "list-media" && isTop) { sendResponse(listMedia()); return false; }
    return false;
  });
})();
