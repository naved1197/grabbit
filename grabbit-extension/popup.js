"use strict";

const DEFAULTS = {
  server: "http://localhost:8000", sendCookies: true, quality: "best", container: "mp4", audioFormat: "mp3",
  showOnImages: true, showOnVideos: true, minImageSize: 120,
};
const ACTIVE = new Set(["queued", "starting", "downloading", "processing"]);
const $ = (s) => document.querySelector(s);

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

const send = (msg) => chrome.runtime.sendMessage(msg);
const fmtDur = (s) => {
  if (s == null || !isFinite(s)) return "";
  s = Math.round(s);
  const m = Math.floor(s / 60);
  return `${m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}` : m}:${String(s % 60).padStart(2, "0")}`;
};

let tab = null;
let media = { videos: [], images: [], audio: [] };
let currentTab = "videos";

/* ----------------------------------------------------------------- page */

async function getTab() {
  const id = Number(new URLSearchParams(location.search).get("tabId"));
  if (id) return chrome.tabs.get(id);
  const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
  return t;
}

async function loadMedia() {
  const ask = () => chrome.tabs.sendMessage(tab.id, { type: "list-media" }, { frameId: 0 });
  try {
    return await ask();
  } catch {
    // Content script missing (tab opened before install): inject and retry.
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["content.js"] });
      return await ask();
    } catch {
      return null;
    }
  }
}

function renderPanel() {
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === currentTab));
  const panel = $("#panel");
  const note = $("#found-note");
  const list = media[currentTab] || [];
  note.hidden = list.length > 0;
  note.textContent = {
    videos: "No videos found on this page. Try “Download video” above.",
    images: "No large images on this page.",
    audio: "No audio players found on this page.",
  }[currentTab];

  if (currentTab === "images") {
    const grid = h("div", { class: "images" }, ...list.map((img) => {
      const b = h("button", { class: "img", type: "button", title: `Download ${img.name}` },
        h("img", { src: img.url, alt: "", loading: "lazy", referrerpolicy: "no-referrer" }),
        h("span", { class: "dim", text: `${img.w}×${img.h}` }));
      b.addEventListener("click", async () => {
        const r = await send({ type: "download-direct", url: img.url, filename: img.name });
        if (r && r.ok) b.classList.add("done");
      });
      return b;
    }));
    const all = list.length > 1
      ? h("button", { class: "btn soft", type: "button", text: `Download all ${list.length} images`,
          onclick: async (e) => {
            e.target.disabled = true;
            const host = new URL(tab.url).hostname.replace(/^www\./, "");
            const r = await send({ type: "download-many", items: list.map((i) => ({ url: i.url, filename: `${host} images/${i.name}` })) });
            e.target.textContent = `Saved ${r.count} images`;
          } })
      : null;
    panel.replaceChildren(grid, all || "");
    return;
  }

  panel.replaceChildren(...list.map((m, i) => {
    const kind = currentTab === "audio" ? "Audio" : "Video";
    const thumb = h("div", { class: "thumb" });
    if (m.poster) thumb.append(h("img", { src: m.poster, alt: "", referrerpolicy: "no-referrer" }));
    else thumb.textContent = currentTab === "audio" ? "♪" : "▶";
    const details = [m.w && m.h ? `${m.w}×${m.h}` : "", fmtDur(m.duration), m.src ? "direct file" : "stream → Grabbit"].filter(Boolean).join(" · ");
    const btn = h("button", { class: "btn soft small", type: "button", text: "Download" });
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      if (m.src) {
        const r = await send({ type: "download-direct", url: m.src, filename: m.name });
        btn.textContent = r && r.ok ? "Saved" : "Failed";
      } else {
        await send({ type: "grabbit-tab", tabId: tab.id, url: m.page, mode: currentTab === "audio" ? "audio" : "video",
          title: m.page === tab.url ? tab.title : null, thumbnail: m.poster, item: m.item, duration: m.duration });
        btn.textContent = "Sent";
        refreshJobs();
      }
    });
    return h("div", { class: "item" }, thumb, h("div", { class: "label" }, `${kind} ${i + 1}`, h("small", { text: details })), btn);
  }));
}

/* ----------------------------------------------------------------- jobs */

function jobText(j) {
  switch (j.status) {
    case "queued": return "Waiting in line…";
    case "starting": return "Getting video details…";
    case "downloading": return j.progress != null ? `Downloading · ${Math.floor(j.progress)}%` : "Downloading…";
    case "processing": return `${j.stage || "Processing"}…`;
    case "finished": return j.saveError ? `Couldn't save: ${j.saveError}` : "Saved to Downloads";
    case "canceled": return "Canceled";
    default: return j.message || j.error || "Failed";
  }
}

async function refreshJobs() {
  const jobs = (await send({ type: "jobs" })) || [];
  $("#jobs-section").hidden = jobs.length === 0;
  $("#jobs").replaceChildren(...jobs.slice(0, 8).map((j) => {
    const state = j.status === "finished" && !j.saveError ? "done" : ACTIVE.has(j.status) ? "working" : "error";
    const bar = h("div", { class: `bar ${state === "working" && j.progress == null ? "indet" : ""}` }, h("i"));
    if (j.status === "downloading" && j.progress != null) bar.firstChild.style.width = `${j.progress}%`;
    const cancel = ACTIVE.has(j.status)
      ? h("button", { class: "link", type: "button", text: "Cancel", onclick: () => send({ type: "cancel-job", id: j.id }) })
      : null;
    return h("li", { class: `job ${state}` },
      h("div", { class: "t", text: j.title || j.pageUrl, title: j.pageUrl || "" }),
      h("div", { class: "s" }, h("span", { text: jobText(j) }), cancel),
      bar);
  }));
}

/* ------------------------------------------------------------- settings */

async function loadSettings() {
  const s = await chrome.storage.sync.get(DEFAULTS);
  for (const [k, v] of Object.entries(s)) {
    const el = document.getElementById(k);
    if (!el) continue;
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v;
  }
  return s;
}

function bindSettings() {
  for (const k of Object.keys(DEFAULTS)) {
    const el = document.getElementById(k);
    if (!el) continue;
    el.addEventListener("change", async () => {
      let v = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) || DEFAULTS[k] : el.value.trim();
      if (k === "server") v = v.replace(/\/+$/, "") || DEFAULTS.server;
      await chrome.storage.sync.set({ [k]: v });
      if (k === "server") checkServer();
    });
  }
  $("#test-server").addEventListener("click", async () => {
    const server = $("#server").value.trim().replace(/\/+$/, "");
    await chrome.storage.sync.set({ server: server || DEFAULTS.server });
    checkServer(true);
  });
}

async function checkServer(fromTest = false) {
  const pill = $("#server-pill");
  const res = await send({ type: "server-status" });
  pill.className = `pill ${res.ok ? "ok" : "bad"}`;
  pill.textContent = res.ok ? "Server connected" : "Server offline";
  $("#offline").hidden = res.ok;
  $("#offline-url").textContent = res.server;
  const out = $("#server-result");
  if (fromTest || out.textContent) {
    out.className = res.ok ? "ok" : "bad";
    out.textContent = res.ok ? `Connected · yt-dlp ${res.version}${res.ffmpeg ? "" : " · no ffmpeg on server"}` : "No Grabbit server answered at that address.";
  }
}

/* ----------------------------------------------------------------- boot */

async function boot() {
  tab = await getTab();
  await loadSettings();
  bindSettings();
  checkServer();
  refreshJobs();
  setInterval(refreshJobs, 1000);

  $("#offline-settings").addEventListener("click", () => { $("#settings").open = true; $("#server").focus(); });
  $("#clear-jobs").addEventListener("click", async () => { await send({ type: "clear-jobs" }); refreshJobs(); });
  for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => { currentTab = b.dataset.tab; renderPanel(); });

  const web = tab && /^https?:/.test(tab.url || "");
  $("#page-host").textContent = web ? new URL(tab.url).hostname.replace(/^www\./, "") : "This page";
  $("#page-title").textContent = web ? tab.title || tab.url : "Grabbit works on regular websites (not browser pages).";
  for (const id of ["#page-video", "#page-audio"]) $(id).disabled = !web;
  const startPage = (mode) => async (e) => {
    e.target.disabled = true;
    await send({ type: "grabbit-tab", tabId: tab.id, mode });
    e.target.textContent = "Sent to Grabbit ✓";
    refreshJobs();
  };
  $("#page-video").addEventListener("click", startPage("video"));
  $("#page-audio").addEventListener("click", startPage("audio"));

  if (!web) { $("#found").hidden = true; return; }
  const res = await loadMedia();
  if (!res) {
    $("#found-note").hidden = false;
    $("#found-note").textContent = "Reload this page to see the media on it.";
    return;
  }
  media = { videos: res.videos || [], images: res.images || [], audio: res.audio || [] };
  $("#n-videos").textContent = media.videos.length;
  $("#n-images").textContent = media.images.length;
  $("#n-audio").textContent = media.audio.length;
  currentTab = media.videos.length ? "videos" : media.images.length ? "images" : media.audio.length ? "audio" : "videos";
  renderPanel();
}

boot();
