"use strict";

/* ===================================================================== utils */

const $ = (sel, root = document) => root.querySelector(sel);
const SVG_NS = "http://www.w3.org/2000/svg";

/** Tiny element builder. Text is always set with textContent (titles come from other sites). */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function icon(name, cls = "icon") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const err = new Error((data && data.error) || `Something went wrong (${res.status}).`);
    err.code = data && data.code;
    throw err;
  }
  return data;
}

function fmtBytes(n) {
  if (n == null || !isFinite(n)) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1024; i++; }
  return `${n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)} ${units[i]}`;
}

function fmtDuration(sec) {
  if (sec == null || !isFinite(sec)) return "";
  sec = Math.round(sec);
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
  const pad = (x) => String(x).padStart(2, "0");
  return hh ? `${hh}:${pad(mm)}:${pad(ss)}` : `${mm}:${pad(ss)}`;
}

function fmtEta(sec) {
  if (sec == null || !isFinite(sec)) return "";
  if (sec < 60) return `${Math.max(1, Math.round(sec))}s left`;
  if (sec < 3600) return `${Math.round(sec / 60)} min left`;
  return `${(sec / 3600).toFixed(1)} h left`;
}

function siteName(s) {
  if (!s) return "";
  const map = { Youtube: "YouTube", YoutubeTab: "YouTube", TikTok: "TikTok", Twitter: "X / Twitter",
    Instagram: "Instagram", Reddit: "Reddit", Vimeo: "Vimeo", Facebook: "Facebook", Generic: "Web" };
  return map[s] || s.replace(/([a-z])([A-Z])/g, "$1 $2");
}

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
  },
};

function toast(message, kind = "") {
  const el = h("div", { class: `toast ${kind}` }, icon(kind === "error" ? "alert" : "check"), h("span", { text: message }));
  $("#toasts").append(el);
  setTimeout(() => el.remove(), kind === "error" ? 6000 : 3500);
}

function extractUrl(text) {
  if (!text) return "";
  const m = String(text).match(/https?:\/\/[^\s<>"']+/i);
  return m ? m[0] : String(text).trim();
}

function thumbBox(src, extraClass = "") {
  const box = h("div", { class: `thumb ${extraClass}` }, h("div", { class: "ph" }, icon("film")));
  if (src) {
    const img = h("img", { src, alt: "", loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
    img.addEventListener("error", () => box.classList.add("no-img"));
    box.prepend(img);
  } else {
    box.classList.add("no-img");
  }
  return box;
}

/* ===================================================================== state */

const prefs = store.get("grabbit.prefs", {});
const state = {
  config: { ffmpeg: true, file_ttl_min: 60, playlist_limit: 50 },
  jobs: new Map(), // id -> job
  order: [], // newest first
  nodes: new Map(), // id -> <li>
  autoSave: new Set(),
  pollTimer: null,
  lookupToken: 0,
};

function savePrefs() { store.set("grabbit.prefs", prefs); }

/* =================================================================== cookies */
// The visitor's cookies live only in this browser (localStorage) and are sent
// with their own requests. Shape: { text, count, domains, added }.

state.cookies = store.get("grabbit.cookies", null);
const cookieDialog = $("#cookie-dialog");
const cookieText = $("#cookie-text");
const cookieStatus = $("#cookie-status");
const cookieFile = $("#cookie-file");
const cookieDrop = $("#cookie-drop");
let afterCookiesSaved = null;
let checkedCookies = null; // result of the last successful /api/cookies/check

function cookiesForRequest() {
  return state.cookies && state.cookies.text ? state.cookies.text : undefined;
}

function domainList(domains, max = 2) {
  if (!domains || !domains.length) return "";
  const shown = domains.slice(0, max).join(", ");
  return domains.length > max ? `${shown} +${domains.length - max}` : shown;
}

function renderCookieBar() {
  const bar = $("#cookie-bar");
  bar.hidden = state.config.user_cookies === false;
  const on = !!(state.cookies && state.cookies.text);
  $("#cookie-btn").classList.toggle("on", on);
  $("#cookie-label").textContent = on
    ? `Cookies on · ${domainList(state.cookies.domains)}`
    : "Private or sign-in only video? Add cookies";
  $("#cookie-btn").setAttribute("aria-label", on ? `Cookies on for ${state.cookies.domains.join(", ")}. Edit cookies` : "Add cookies");
  $("#cookie-clear").hidden = !on;
}

function setCookieStatus(text, kind = "") {
  cookieStatus.className = `cookie-status ${kind}`;
  cookieStatus.textContent = text;
}

function openCookies(after) {
  afterCookiesSaved = typeof after === "function" ? after : null;
  checkedCookies = null;
  cookieText.value = "";
  cookieFile.value = "";
  setCookieStatus("");
  const current = $("#cookie-current");
  const on = !!(state.cookies && state.cookies.text);
  current.hidden = !on;
  if (on) {
    current.replaceChildren(icon("check"),
      h("span", { text: `Using ${state.cookies.count} cookie${state.cookies.count === 1 ? "" : "s"} for ${state.cookies.domains.join(", ")}. Add a new file to replace them.` }));
  }
  $("#cookie-remove").hidden = !on;
  cookieDialog.showModal();
}

async function checkCookies() {
  const raw = cookieText.value.trim();
  if (!raw) { setCookieStatus("Choose a cookies file or paste its contents first.", "err"); return null; }
  setCookieStatus("Checking…");
  try {
    const res = await api("/api/cookies/check", { method: "POST", body: { cookies: raw } });
    checkedCookies = { raw, ...res };
    setCookieStatus(`✓ ${res.count} cookie${res.count === 1 ? "" : "s"} for ${res.domains.join(", ")}`, "ok");
    return checkedCookies;
  } catch (err) {
    checkedCookies = null;
    setCookieStatus(err.message, "err");
    return null;
  }
}

async function readCookieFile(file) {
  if (!file) return;
  if (file.size > 1_000_000) { setCookieStatus("That file is too big for a cookies file.", "err"); return; }
  cookieText.value = await file.text();
  await checkCookies();
}

cookieFile.addEventListener("change", () => readCookieFile(cookieFile.files[0]));
cookieDrop.addEventListener("dragover", (e) => { e.preventDefault(); cookieDrop.classList.add("over"); });
cookieDrop.addEventListener("dragleave", () => cookieDrop.classList.remove("over"));
cookieDrop.addEventListener("drop", (e) => {
  e.preventDefault();
  cookieDrop.classList.remove("over");
  readCookieFile(e.dataTransfer.files[0]);
});
cookieText.addEventListener("input", () => { checkedCookies = null; setCookieStatus(""); });
cookieText.addEventListener("paste", () => setTimeout(checkCookies, 0));

$("#cookie-save").addEventListener("click", async () => {
  const raw = cookieText.value.trim();
  if (!raw && state.cookies) { cookieDialog.close(); return; }
  const res = checkedCookies && checkedCookies.raw === raw ? checkedCookies : await checkCookies();
  if (!res) return;
  state.cookies = { text: res.cookies, count: res.count, domains: res.domains, added: Date.now() };
  store.set("grabbit.cookies", state.cookies);
  renderCookieBar();
  cookieDialog.close();
  toast(`Cookies saved for ${domainList(res.domains, 3)}`);
  const next = afterCookiesSaved;
  afterCookiesSaved = null;
  if (next) next();
});

function removeCookies() {
  state.cookies = null;
  try { localStorage.removeItem("grabbit.cookies"); } catch { /* ignore */ }
  renderCookieBar();
  toast("Cookies removed from this browser");
}
$("#cookie-remove").addEventListener("click", () => { removeCookies(); cookieDialog.close(); });
$("#cookie-clear").addEventListener("click", removeCookies);
$("#cookie-btn").addEventListener("click", () => openCookies(() => { if (input.value.trim()) lookup(input.value); }));
cookieDialog.addEventListener("click", (e) => { if (e.target === cookieDialog) cookieDialog.close(); }); // backdrop

function cookieButtonLabel() {
  return state.cookies && state.cookies.text ? "Update cookies" : "Add cookies";
}

/* ==================================================================== lookup */

const form = $("#lookup-form");
const input = $("#url");
const goBtn = $("#go-btn");
const result = $("#result");
const formError = $("#form-error");

function showFormError(msg, code) {
  formError.replaceChildren(icon("alert"), h("span", { text: msg }));
  if ((code === "login_required" || code === "bad_cookies") && state.config.user_cookies !== false) {
    formError.append(h("button", {
      type: "button", class: "err-action",
      onclick: () => openCookies(() => lookup(input.value)),
    }, icon("cookie"), cookieButtonLabel()));
  }
  formError.hidden = false;
}

function setLoading(on) {
  goBtn.disabled = on;
  goBtn.classList.toggle("loading", on);
  goBtn.querySelector(".btn-label").textContent = on ? "Looking" : "Get video";
}

function renderSkeleton() {
  result.hidden = false;
  result.className = "card result skeleton";
  result.replaceChildren(
    h("div", { class: "media" },
      h("div", { class: "thumb" }),
      h("div", { class: "meta" },
        h("div", { class: "sk sk-line w40" }), h("div", { class: "sk sk-line w90" }), h("div", { class: "sk sk-line w60" }))),
    h("div", { class: "sk sk-tall" }),
  );
}

async function lookup(raw) {
  const url = extractUrl(raw);
  formError.hidden = true;
  if (!url) { showFormError("Paste a link to a video first."); input.focus(); return; }
  input.value = url;
  const token = ++state.lookupToken;
  setLoading(true);
  renderSkeleton();
  try {
    const info = await api("/api/info", { method: "POST", body: { url, cookies: cookiesForRequest() } });
    if (token !== state.lookupToken) return;
    if (info.kind === "playlist") renderPlaylist(info, url);
    else renderVideo(info, url);
    const params = new URLSearchParams(location.search);
    if (params.get("url") !== url) history.replaceState(null, "", `?url=${encodeURIComponent(url)}`);
  } catch (err) {
    if (token !== state.lookupToken) return;
    result.hidden = true;
    showFormError(err.message, err.code);
  } finally {
    if (token === state.lookupToken) setLoading(false);
  }
}

form.addEventListener("submit", (e) => { e.preventDefault(); lookup(input.value); });
input.addEventListener("paste", () => {
  // Look up as soon as a link is pasted.
  setTimeout(() => { if (/^\s*(https?:\/\/|www\.)/i.test(input.value)) lookup(input.value); }, 0);
});

const pasteBtn = $("#paste-btn");
if (navigator.clipboard && navigator.clipboard.readText) {
  pasteBtn.hidden = false;
  pasteBtn.addEventListener("click", async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) { input.value = extractUrl(text); lookup(input.value); }
      else input.focus();
    } catch {
      input.focus();
      toast("Your browser blocked clipboard access. Paste with Ctrl+V / ⌘V instead.", "error");
    }
  });
}

/* ============================================================== video card */

function renderVideo(info, url) {
  result.className = "card result";
  const choice = {
    mode: info.has_video === false ? "audio" : (prefs.mode || "video"),
    quality: "best",
    container: prefs.container || "mp4",
    audio_format: state.config.ffmpeg ? (prefs.audio_format || "mp3") : "best",
  };
  if (info.has_video === false) choice.mode = "audio";

  const thumb = thumbBox(info.thumbnail);
  if (info.duration) thumb.append(h("span", { class: "duration", text: fmtDuration(info.duration) }));

  const meta = h("div", { class: "meta" },
    info.site ? h("span", { class: "site-tag", text: siteName(info.site) }) : null,
    h("h2", { text: info.title }),
    info.uploader ? h("p", { class: "by", text: info.uploader }) : null,
  );

  // Mode tabs
  const tabVideo = h("button", { type: "button", role: "tab" }, icon("film"), "Video");
  const tabAudio = h("button", { type: "button", role: "tab" }, icon("music"), "Audio only");
  const tabs = h("div", { class: "segmented", role: "tablist", "aria-label": "Download type" }, tabVideo, tabAudio);
  if (info.has_video === false) tabVideo.disabled = true;

  // Video panel
  const qualities = [{ value: "best", label: "Best", sub: info.qualities?.[0] ? `${info.qualities[0].label}${info.qualities[0].size ? " · " + fmtBytes(info.qualities[0].size) : ""}` : "Highest available" }]
    .concat((info.qualities || []).map((q) => ({ value: q.value, label: q.label, badge: q.badge, sub: q.size ? `≈ ${fmtBytes(q.size)}` : "" })));
  const chipName = `q-${Math.random().toString(36).slice(2, 8)}`;
  const qualityChips = h("fieldset", { class: "chips" }, h("legend", { class: "visually-hidden", text: "Quality" }),
    qualities.map((q, i) => {
      const id = `${chipName}-${i}`;
      const radio = h("input", { type: "radio", name: chipName, id, value: q.value, checked: q.value === choice.quality });
      radio.addEventListener("change", () => { choice.quality = q.value; updateButton(); });
      return h("div", { class: "chip" }, radio,
        h("label", { for: id },
          h("span", { class: "q" }, q.label, q.badge ? h("span", { class: "tag", text: q.badge }) : null),
          q.sub ? h("span", { class: "sub", text: q.sub }) : null));
    }));

  const containerSelect = h("select", { id: "container", "aria-label": "Video format" },
    h("option", { value: "mp4", text: "MP4 · plays everywhere" }),
    h("option", { value: "webm", text: "WebM · VP9/AV1, smaller" }),
    h("option", { value: "mkv", text: "MKV · keeps original streams" }));
  containerSelect.value = choice.container;
  containerSelect.addEventListener("change", () => { choice.container = containerSelect.value; prefs.container = choice.container; savePrefs(); updateButton(); });

  const videoPanel = h("div", { class: "options-panel" },
    h("span", { class: "field-label", text: "Quality" }), qualityChips,
    state.config.ffmpeg ? h("div", { class: "row", style: null },
      h("label", { class: "select-wrap" }, h("span", { class: "field-label", text: "Format" }), containerSelect)) : null,
  );

  // Audio panel
  const audioFormats = state.config.ffmpeg
    ? [
        { value: "mp3", label: "MP3", sub: "Works everywhere" },
        { value: "m4a", label: "M4A", sub: "AAC, great on Apple" },
        { value: "opus", label: "Opus", sub: "Best per megabyte" },
        { value: "best", label: "Original", sub: info.audio_size ? `No conversion · ≈ ${fmtBytes(info.audio_size)}` : "No conversion" },
      ]
    : [{ value: "best", label: "Original", sub: info.audio_size ? `≈ ${fmtBytes(info.audio_size)}` : "As provided" }];
  const audioName = `a-${Math.random().toString(36).slice(2, 8)}`;
  const audioChips = h("fieldset", { class: "chips" }, h("legend", { class: "visually-hidden", text: "Audio format" }),
    audioFormats.map((a, i) => {
      const id = `${audioName}-${i}`;
      const radio = h("input", { type: "radio", name: audioName, id, value: a.value, checked: a.value === choice.audio_format });
      radio.addEventListener("change", () => { choice.audio_format = a.value; prefs.audio_format = a.value; savePrefs(); updateButton(); });
      return h("div", { class: "chip" }, radio,
        h("label", { for: id }, h("span", { class: "q", text: a.label }), h("span", { class: "sub", text: a.sub })));
    }));
  const audioPanel = h("div", { class: "options-panel" }, h("span", { class: "field-label", text: "Audio format" }), audioChips);

  const warning = info.warning
    ? h("div", { class: "notice" }, icon("alert"), h("span", { text: info.warning }))
    : null;
  const ffmpegNote = !state.config.ffmpeg
    ? h("div", { class: "notice info" }, icon("alert"), h("span", { text: "This server has no ffmpeg, so only formats that already include audio are offered." }))
    : null;

  const dlBtn = h("button", { type: "button", class: "btn primary block" }, icon("download"), h("span", { class: "btn-label" }));
  if (info.warning) dlBtn.disabled = true;

  function selectMode(mode) {
    choice.mode = mode;
    tabVideo.setAttribute("aria-selected", String(mode === "video"));
    tabAudio.setAttribute("aria-selected", String(mode === "audio"));
    videoPanel.hidden = mode !== "video";
    audioPanel.hidden = mode !== "audio";
    updateButton();
  }
  function updateButton() {
    let label;
    if (choice.mode === "audio") {
      const f = audioFormats.find((a) => a.value === choice.audio_format);
      label = `Download ${f && f.value !== "best" ? f.label : "audio"}`;
    } else {
      const q = qualities.find((x) => x.value === choice.quality);
      const fmt = state.config.ffmpeg ? choice.container.toUpperCase() : "video";
      label = `Download ${fmt}${q && q.value !== "best" ? " · " + q.label : ""}`;
    }
    dlBtn.querySelector(".btn-label").textContent = label;
  }
  tabVideo.addEventListener("click", () => { selectMode("video"); prefs.mode = "video"; savePrefs(); });
  tabAudio.addEventListener("click", () => { selectMode("audio"); prefs.mode = "audio"; savePrefs(); });

  dlBtn.addEventListener("click", async () => {
    dlBtn.disabled = true;
    try {
      await startJob({
        url: info.webpage_url || url,
        ...choice,
        meta: { title: info.title, thumbnail: info.thumbnail, uploader: info.uploader, duration: info.duration },
      });
      toast("Added to your downloads");
      if (window.matchMedia("(max-width: 720px)").matches) $("#downloads").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      toast(err.message, "error");
    } finally {
      dlBtn.disabled = !!info.warning;
    }
  });

  result.replaceChildren(
    h("div", { class: "media" }, thumb, meta),
    h("div", { class: "options" }, warning, ffmpegNote, tabs, videoPanel, audioPanel, dlBtn),
  );
  selectMode(choice.mode);
}

/* =========================================================== playlist card */

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function countText(info, entries) {
  if (!info.post) return [plural(info.count || entries.length, "video")];
  return [info.videos ? plural(info.videos, "video") : "", info.photos ? plural(info.photos, "photo") : ""];
}

function renderPlaylist(info, url) {
  result.className = "card result";
  const entries = info.entries || [];
  const selected = new Set(entries.map((_, i) => i));
  const choice = { mode: prefs.mode || "video", quality: prefs.pl_quality || "1080", container: prefs.container || "mp4",
    audio_format: state.config.ffmpeg ? (prefs.audio_format || "mp3") : "best" };

  const head = h("div", { class: "pl-head" },
    thumbBox(info.thumbnail),
    h("div", { class: "meta" },
      h("span", { class: "site-tag" }, siteName(info.site) || (info.post ? "Post" : "Playlist")),
      h("h2", { text: info.title }),
      h("p", { class: "by", text: [info.uploader, ...countText(info, entries)].filter(Boolean).join(" · ") })));

  const all = h("input", { type: "checkbox", checked: true });
  const allLabel = h("label", { class: "check" }, all, h("span", { text: "Select all" }));
  const countNote = h("span", { class: "by", text: info.truncated ? `Showing the first ${entries.length}` : "" });

  const boxes = [];
  const list = h("ol", { class: "pl-list" }, entries.map((e, i) => {
    const cb = h("input", { type: "checkbox", checked: true, "aria-label": e.title });
    boxes.push(cb);
    cb.addEventListener("change", () => { cb.checked ? selected.add(i) : selected.delete(i); sync(); });
    const row = h("li", { class: "pl-item" }, cb, thumbBox(e.thumbnail),
      h("div", {}, h("div", { class: "t", text: e.title }), e.uploader ? h("div", { class: "d", text: e.uploader }) : null),
      h("span", { class: "d", text: e.photo ? "Photo" : fmtDuration(e.duration) }));
    row.addEventListener("click", (ev) => { if (ev.target !== cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event("change")); } });
    return row;
  }));
  all.addEventListener("change", () => {
    boxes.forEach((cb, i) => { cb.checked = all.checked; all.checked ? selected.add(i) : selected.delete(i); });
    sync();
  });

  const modeSelect = h("select", { "aria-label": "Download type" },
    h("option", { value: "video", text: "Video" }), h("option", { value: "audio", text: "Audio only" }));
  modeSelect.value = choice.mode;
  const qualitySelect = h("select", { "aria-label": "Quality" },
    [["best", "Best available"], ["2160", "Up to 4K"], ["1080", "Up to 1080p"], ["720", "Up to 720p"], ["480", "Up to 480p"], ["360", "Up to 360p"]]
      .map(([v, t]) => h("option", { value: v, text: t })));
  qualitySelect.value = choice.quality;
  const audioSelect = h("select", { "aria-label": "Audio format" },
    (state.config.ffmpeg ? [["mp3", "MP3"], ["m4a", "M4A"], ["opus", "Opus"], ["best", "Original"]] : [["best", "Original"]])
      .map(([v, t]) => h("option", { value: v, text: t })));
  audioSelect.value = choice.audio_format;
  const qualityWrap = h("label", { class: "select-wrap" }, h("span", { class: "field-label", text: "Quality" }), qualitySelect);
  const audioWrap = h("label", { class: "select-wrap" }, h("span", { class: "field-label", text: "Audio format" }), audioSelect);

  modeSelect.addEventListener("change", () => { choice.mode = modeSelect.value; sync(); });
  qualitySelect.addEventListener("change", () => { choice.quality = qualitySelect.value; prefs.pl_quality = choice.quality; savePrefs(); });
  audioSelect.addEventListener("change", () => { choice.audio_format = audioSelect.value; });

  const dlBtn = h("button", { type: "button", class: "btn primary block" }, icon("download"), h("span", { class: "btn-label" }));
  function sync() {
    all.checked = selected.size === entries.length;
    all.indeterminate = selected.size > 0 && selected.size < entries.length;
    qualityWrap.hidden = choice.mode !== "video";
    audioWrap.hidden = choice.mode !== "audio";
    dlBtn.disabled = selected.size === 0;
    const n = selected.size;
    const noun = info.post && choice.mode !== "audio" ? "item" : choice.mode === "audio" ? "track" : "video";
    dlBtn.querySelector(".btn-label").textContent = n ? `Download ${n} ${noun}${n === 1 ? "" : "s"}` : `Select ${info.post ? "items" : "videos"}`;
  }

  dlBtn.addEventListener("click", async () => {
    dlBtn.disabled = true;
    const items = [...selected].sort((a, b) => a - b).map((i) => entries[i]).map((e) => ({
      url: e.url, item: e.item || undefined, mode: choice.mode, quality: choice.quality, container: choice.container,
      audio_format: choice.audio_format,
      // A post's rows are just "Video 2"; let the server name the file from the post itself.
      meta: { title: info.post ? undefined : e.title, thumbnail: e.thumbnail, uploader: e.uploader, duration: e.duration },
    }));
    try {
      const res = await api("/api/jobs/batch", { method: "POST", body: { ...choice, items, cookies: cookiesForRequest() } });
      res.jobs.slice().reverse().forEach((j) => addJob(j, true));
      schedulePoll(200);
      toast(`Added ${res.jobs.length} to your downloads`);
      if (res.errors.length) toast(res.errors[0].error, "error");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      sync();
    }
  });

  result.replaceChildren(
    head,
    h("div", { class: "pl-tools" }, allLabel, countNote),
    list,
    h("div", { class: "pl-actions" },
      h("label", { class: "select-wrap" }, h("span", { class: "field-label", text: "Type" }), modeSelect),
      qualityWrap, audioWrap, dlBtn),
  );
  sync();
}

/* ================================================================ downloads */

const ACTIVE = new Set(["queued", "starting", "downloading", "processing"]);
const jobList = $("#job-list");
const downloadsSection = $("#downloads");
const autoSaveToggle = $("#autosave");
autoSaveToggle.checked = prefs.autosave !== false;
autoSaveToggle.addEventListener("change", () => { prefs.autosave = autoSaveToggle.checked; savePrefs(); });

async function startJob(body) {
  const job = await api("/api/jobs", { method: "POST", body: { ...body, cookies: cookiesForRequest() } });
  addJob(job, true);
  schedulePoll(300);
  return job;
}

function addJob(job, fresh) {
  // Keep the request around so "Retry" can resubmit it.
  job.request = job.request || {
    url: job.url, mode: job.mode, quality: job.quality,
    container: job.mode === "video" ? job.format : "mp4",
    audio_format: job.mode === "audio" ? job.format : "mp3",
    meta: { title: job.title, thumbnail: job.thumbnail, uploader: job.uploader, duration: job.duration },
  };
  state.jobs.set(job.id, job);
  state.order = [job.id, ...state.order.filter((id) => id !== job.id)];
  if (fresh) state.autoSave.add(job.id);
  renderJob(job);
  persistJobs();
}

function mergeJob(update) {
  const job = state.jobs.get(update.id);
  if (!job) return;
  const was = job.status;
  if (update.status === "gone") {
    if (was === "gone") return;
    job.status = "gone";
  } else {
    Object.assign(job, update);
  }
  renderJob(job);
  if (was !== "finished" && job.status === "finished") onFinished(job);
  if (was !== job.status) persistJobs();
}

function onFinished(job) {
  if (state.autoSave.has(job.id) && autoSaveToggle.checked) saveFile(job);
  state.autoSave.delete(job.id);
}

function saveFile(job) {
  const a = h("a", { href: `/api/jobs/${encodeURIComponent(job.id)}/file`, download: job.filename || "" });
  document.body.append(a);
  a.click();
  a.remove();
}

function persistJobs() {
  const keep = state.order.slice(0, 40).map((id) => {
    const j = state.jobs.get(id);
    const { request, id: jid, title, thumbnail, uploader, status, filename, filesize, error, created, expires, mode, format } = j;
    return { request, id: jid, title, thumbnail, uploader, status, filename, filesize, error, created, expires, mode, format };
  });
  store.set("grabbit.jobs", keep);
}

function statusLine(job) {
  const parts = [];
  switch (job.status) {
    case "queued":
      parts.push(job.queue_position ? `Waiting in line · #${job.queue_position}` : "Waiting in line");
      break;
    case "starting":
      parts.push(job.stage || "Getting video details");
      break;
    case "downloading": {
      parts.push(job.progress != null ? `${Math.floor(job.progress)}%` : (job.stage || "Downloading"));
      const stream = /Downloading (video|audio) \((\d+) of (\d+)\)/.exec(job.stage || "");
      if (stream) parts.push(`${stream[1]} ${stream[2]}/${stream[3]}`);
      if (job.downloaded) parts.push(job.total ? `${fmtBytes(job.downloaded)} of ${fmtBytes(job.total)}` : fmtBytes(job.downloaded));
      if (job.speed) parts.push(`${fmtBytes(job.speed)}/s`);
      if (job.eta) parts.push(fmtEta(job.eta));
      break;
    }
    case "processing":
      parts.push(job.stage || "Processing");
      break;
    case "finished": {
      parts.push("Ready");
      if (job.filesize) parts.push(fmtBytes(job.filesize));
      if (job.format) parts.push(job.format === "best" ? "original" : job.format.toUpperCase());
      if (job.expires) {
        const mins = Math.max(0, Math.round((job.expires * 1000 - Date.now()) / 60000));
        parts.push(mins > 0 ? `available for ${mins} min` : "expiring");
      }
      break;
    }
    case "error": parts.push("Failed"); break;
    case "canceled": parts.push("Canceled"); break;
    case "gone": parts.push("Expired"); break;
    default: parts.push(job.status);
  }
  return parts;
}

function renderJob(job) {
  downloadsSection.hidden = state.order.length === 0;
  let li = state.nodes.get(job.id);
  if (!li) {
    li = h("li", { class: "job" },
      thumbBox(job.thumbnail),
      h("div", { class: "job-body" },
        h("p", { class: "job-title" }),
        h("p", { class: "job-sub" }),
        h("p", { class: "job-err", hidden: true }),
        h("div", { class: "bar" }, h("span"))),
      h("div", { class: "job-actions" }));
    state.nodes.set(job.id, li);
  }
  // keep DOM order in sync with state.order (newest first)
  const idx = state.order.indexOf(job.id);
  const next = state.order.slice(idx + 1).map((id) => state.nodes.get(id)).find((n) => n && n.parentNode === jobList);
  if (li.parentNode !== jobList || li.nextSibling !== (next || null)) jobList.insertBefore(li, next || null);

  li.dataset.status = job.status;
  const title = job.title || job.filename || job.url;
  li.querySelector(".job-title").textContent = title;
  li.querySelector(".job-title").title = title;

  const sub = li.querySelector(".job-sub");
  const parts = statusLine(job);
  sub.replaceChildren(h("span", { class: "stage", text: parts[0] }), ...parts.slice(1).map((p) => h("span", { text: `· ${p}` })));

  const err = li.querySelector(".job-err");
  err.hidden = !(job.status === "error" || job.status === "gone");
  err.textContent = job.status === "gone"
    ? `Files are kept for ${state.config.file_ttl_min} minutes. Download it again if you still need it.`
    : (job.error || "");

  const bar = li.querySelector(".bar");
  const determinate = job.status === "downloading" && job.progress != null;
  bar.classList.toggle("indeterminate", ACTIVE.has(job.status) && !determinate);
  bar.firstChild.style.width = determinate ? `${job.progress}%` : "";
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-label", `Progress for ${title}`);
  if (determinate) bar.setAttribute("aria-valuenow", String(Math.round(job.progress)));
  else bar.removeAttribute("aria-valuenow");

  const actions = li.querySelector(".job-actions");
  const needsLogin = job.status === "error" && job.error_code === "login_required" && state.config.user_cookies !== false;
  const want = job.status === "finished" ? "finished" : ACTIVE.has(job.status) ? "active" : needsLogin ? "login" : "done";
  if (actions.dataset.kind !== want) {
    actions.dataset.kind = want;
    actions.replaceChildren();
    if (want === "finished") {
      actions.append(
        h("a", { class: "btn primary small", href: `/api/jobs/${encodeURIComponent(job.id)}/file`, download: job.filename || "" }, icon("download"), "Save"),
        h("button", { type: "button", class: "icon-btn", "aria-label": "Remove from list", title: "Remove", onclick: () => removeJob(job.id) }, icon("trash")));
    } else if (want === "active") {
      actions.append(h("button", { type: "button", class: "icon-btn", "aria-label": "Cancel download", title: "Cancel", onclick: () => cancelJob(job.id) }, icon("x")));
    } else if (want === "login") {
      actions.append(
        h("button", { type: "button", class: "btn soft small", onclick: () => openCookies(() => retryJob(job.id)) }, icon("cookie"), cookieButtonLabel()),
        h("button", { type: "button", class: "icon-btn", "aria-label": "Remove from list", title: "Remove", onclick: () => removeJob(job.id) }, icon("trash")));
    } else {
      actions.append(
        h("button", { type: "button", class: "btn soft small", onclick: () => retryJob(job.id) }, icon("retry"), job.status === "gone" ? "Download again" : "Retry"),
        h("button", { type: "button", class: "icon-btn", "aria-label": "Remove from list", title: "Remove", onclick: () => removeJob(job.id) }, icon("trash")));
    }
  }
  if (want === "finished") {
    const save = actions.querySelector("a");
    if (save && job.filename) save.setAttribute("download", job.filename);
  }
}

async function cancelJob(id) {
  try { mergeJob(await api(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" })); }
  catch (err) { toast(err.message, "error"); }
}

async function removeJob(id) {
  api(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => {});
  state.jobs.delete(id);
  state.order = state.order.filter((x) => x !== id);
  state.autoSave.delete(id);
  state.nodes.get(id)?.remove();
  state.nodes.delete(id);
  downloadsSection.hidden = state.order.length === 0;
  persistJobs();
}

async function retryJob(id) {
  const job = state.jobs.get(id);
  if (!job || !job.request) return;
  try {
    const fresh = await api("/api/jobs", { method: "POST", body: { ...job.request, cookies: cookiesForRequest() } });
    removeJob(id);
    addJob(fresh, true);
    schedulePoll(300);
  } catch (err) {
    toast(err.message, "error");
  }
}

$("#clear-done").addEventListener("click", () => {
  for (const id of [...state.order]) {
    const s = state.jobs.get(id)?.status;
    if (s && !ACTIVE.has(s)) removeJob(id);
  }
});

function schedulePoll(delay = 1000) {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(poll, delay);
}

async function poll(all = false) {
  const ids = state.order.filter((id) => all || ACTIVE.has(state.jobs.get(id)?.status));
  if (!ids.length) return;
  try {
    const { jobs } = await api(`/api/jobs?ids=${ids.map(encodeURIComponent).join(",")}`);
    jobs.forEach(mergeJob);
  } catch { /* transient: try again */ }
  const stillActive = state.order.some((id) => ACTIVE.has(state.jobs.get(id)?.status));
  if (stillActive) schedulePoll(document.hidden ? 4000 : 900);
}

// Refresh "available for N min" labels once a minute.
setInterval(() => {
  for (const id of state.order) {
    const j = state.jobs.get(id);
    if (j && j.status === "finished") renderJob(j);
  }
}, 60000);

document.addEventListener("visibilitychange", () => { if (!document.hidden) schedulePoll(50); });

/* ==================================================================== theme */

$("#theme-toggle").addEventListener("click", () => {
  const root = document.documentElement;
  const current = root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = current === "dark" ? "light" : "dark";
  root.dataset.theme = next;
  try { localStorage.setItem("grabbit.theme", next); } catch { /* ignore */ }
  updateThemeLabel();
});
function updateThemeLabel() {
  const dark = (document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
  $("#theme-toggle").setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
}

/* ===================================================================== boot */

async function boot() {
  updateThemeLabel();
  try {
    state.config = await api("/api/config");
    $("#ytdlp-version").textContent = ` ${state.config.ytdlp_version}`;
    const ttl = state.config.file_ttl_min;
    $("#ttl-note").textContent = ttl % 60 === 0 ? `after ${ttl / 60 === 1 ? "an hour" : `${ttl / 60} hours`}` : `after ${ttl} minutes`;
  } catch { /* keep defaults */ }
  renderCookieBar();

  // Restore this browser's recent downloads.
  const saved = store.get("grabbit.jobs", []);
  for (const j of saved.reverse()) if (j && j.id) addJob(j, false);
  if (state.order.length) poll(true);

  // ?url= (bookmarklets) and Android "share to" (title/text/url params).
  const params = new URLSearchParams(location.search);
  const shared = params.get("url") || extractUrl(params.get("text") || "") || "";
  if (shared && /^https?:\/\//i.test(shared)) {
    input.value = shared;
    lookup(shared);
  } else if (!("ontouchstart" in window)) {
    input.focus();
  }
}

boot();
