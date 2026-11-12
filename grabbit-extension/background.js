// Grabbit background worker: direct downloads, Grabbit server jobs, cookies, menus.
"use strict";

const DEFAULTS = {
  server: "http://localhost:8000",
  sendCookies: true,
  quality: "best",
  container: "mp4",
  audioFormat: "mp3",
  showOnImages: true,
  showOnVideos: true,
  minImageSize: 120,
};
const ACTIVE = new Set(["queued", "starting", "downloading", "processing"]);
const MAX_JOBS_KEPT = 30;

const getSettings = () => chrome.storage.sync.get(DEFAULTS);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const serverBase = (s) => String(s || "").trim().replace(/\/+$/, "");

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return "video"; }
}

/* ------------------------------------------------------------ file names */

function safeName(name, fallback = "download") {
  let n = String(name || "").normalize("NFC")
    .replace(/[\u0000-\u001f\u007f<>:"/\\|?*​-‏‪-‮]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^[.\s]+|[.\s]+$/g, "");
  if (!n) n = fallback;
  if (/^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$/i.test(n)) n = `_${n}`;
  if (n.length > 150) {
    const dot = n.lastIndexOf(".");
    const ext = dot > 0 && n.length - dot <= 6 ? n.slice(dot) : "";
    n = n.slice(0, 150 - ext.length).trim() + ext;
  }
  return n;
}

/** "folder/file.jpg" -> each part made safe; Chrome saves it under Downloads. */
function safePath(path) {
  const parts = String(path).split("/").map((p) => safeName(p, "")).filter(Boolean);
  return parts.length ? parts.join("/") : safeName("");
}

async function directDownload(url, filename) {
  const opts = { url, conflictAction: "uniquify" };
  if (filename) opts.filename = safePath(filename);
  try {
    return { ok: true, id: await chrome.downloads.download(opts) };
  } catch (err) {
    if (opts.filename) {
      // Some names are still rejected on some systems: let Chrome pick one.
      try {
        delete opts.filename;
        return { ok: true, id: await chrome.downloads.download(opts) };
      } catch { /* fall through */ }
    }
    return { ok: false, error: String((err && err.message) || err) };
  }
}

/* --------------------------------------------------------------- cookies */

const ALIASES = {
  "youtu.be": ["youtube.com"],
  "youtube-nocookie.com": ["youtube.com"],
  "x.com": ["twitter.com"],
  "twitter.com": ["x.com"],
  "instagr.am": ["instagram.com"],
  "fb.watch": ["facebook.com"],
  "redd.it": ["reddit.com"],
};

function siteDomain(host) {
  if (/^[\d.]+$/.test(host) || host.includes(":") || !host.includes(".")) return host;
  const p = host.split(".");
  const keep = p.length >= 3 && p[p.length - 2].length <= 3 && p[p.length - 1].length === 2 ? 3 : 2;
  return p.slice(-keep).join(".");
}

/** The browser's cookies for the video's site, as a Netscape cookies.txt string. */
async function cookiesFor(url) {
  let host;
  try { host = new URL(url).hostname; } catch { return undefined; }
  const base = siteDomain(host);
  const domains = new Set([base, ...(ALIASES[base] || [])]);
  const seen = new Map();
  for (const domain of domains) {
    for (const c of await chrome.cookies.getAll({ domain })) seen.set(`${c.domain}|${c.path}|${c.name}`, c);
  }
  if (!seen.size) return undefined;
  const lines = [...seen.values()].map((c) => {
    const domain = c.hostOnly ? c.domain.replace(/^\./, "") : (c.domain.startsWith(".") ? c.domain : `.${c.domain}`);
    return [domain, c.hostOnly ? "FALSE" : "TRUE", c.path || "/", c.secure ? "TRUE" : "FALSE",
      c.expirationDate ? Math.floor(c.expirationDate) : 0, c.name, c.value].join("\t");
  });
  return `# Netscape HTTP Cookie File\n${lines.join("\n")}\n`;
}

/* ------------------------------------------------------------ job store */

async function getJobs() {
  const { jobs } = await chrome.storage.session.get({ jobs: {} });
  return jobs;
}

async function putJob(job) {
  const jobs = await getJobs();
  jobs[job.id] = job;
  const ids = Object.keys(jobs).sort((a, b) => jobs[b].started - jobs[a].started);
  for (const id of ids.slice(MAX_JOBS_KEPT)) delete jobs[id];
  await chrome.storage.session.set({ jobs });
}

function statusText(job) {
  switch (job.status) {
    case "queued": return job.queue_position ? `Waiting in line (#${job.queue_position})…` : "Waiting in line…";
    case "starting": return "Getting video details…";
    case "downloading": {
      const parts = [job.progress != null ? `${Math.floor(job.progress)}%` : "Downloading…"];
      if (job.speed) parts.push(`${fmtBytes(job.speed)}/s`);
      if (job.eta) parts.push(job.eta < 60 ? `${Math.round(job.eta)}s left` : `${Math.round(job.eta / 60)} min left`);
      return parts.join(" · ");
    }
    case "processing": return `${job.stage || "Processing"}…`;
    case "finished": return job.saveError ? `Couldn't save: ${job.saveError}` : "Saved to your Downloads folder";
    case "canceled": return "Canceled";
    default:
      if (job.error_code === "login_required") {
        const site = siteDomain(hostOf(job.pageUrl || ""));
        return job.sentCookies
          ? `This video needs a signed-in account. Sign in to ${site} in Chrome with an account that can see it, then try again.`
          : `This video needs a signed-in account. Turn on “Use my logins” in the Grabbit menu, then try again.`;
      }
      return job.error || "Failed";
  }
}

function fmtBytes(n) {
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1024; i++; }
  return `${n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)} ${u[i]}`;
}

function tell(job, extra = {}) {
  if (job.tabId == null) return;
  const state = job.status === "finished" ? (job.saveError ? "error" : "done") : ACTIVE.has(job.status) || job.status === "sending" ? "working" : "error";
  chrome.tabs.sendMessage(job.tabId, {
    type: "grabbit-status", id: job.reqId, title: job.title, text: statusText(job), state,
    progress: job.status === "downloading" ? job.progress : null, loginNeeded: job.error_code === "login_required",
    ...extra,
  }, { frameId: 0 }).catch(() => {});
}

/* ------------------------------------------------------------ server jobs */

async function startJob(req, tab) {
  const s = await getSettings();
  const server = serverBase(s.server);
  const pending = { id: `pending-${req.reqId}`, reqId: req.reqId, tabId: tab && tab.id, title: req.title || hostOf(req.url), status: "sending" };
  tell(pending, { text: "Sending to Grabbit…" });

  let cookies;
  if (s.sendCookies) cookies = await cookiesFor(req.url).catch(() => undefined);
  const body = {
    url: req.url,
    mode: req.mode === "audio" ? "audio" : "video",
    quality: req.quality || s.quality,
    container: s.container,
    audio_format: s.audioFormat,
    item: Number.isInteger(req.item) && req.item >= 1 ? req.item : undefined,
    meta: { title: req.title || undefined, thumbnail: req.thumbnail || undefined,
      duration: Number.isFinite(req.duration) && req.duration > 0 ? req.duration : undefined },
    cookies,
  };
  let res, data;
  try {
    res = await fetch(`${server}/api/jobs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    data = await res.json().catch(() => ({}));
  } catch {
    const msg = `Can't reach your Grabbit server (${server}). Make sure it's running, or change the address in the Grabbit menu.`;
    tell(pending, { text: msg, state: "error" });
    return { ok: false, error: msg };
  }
  if (!res.ok) {
    const msg = data.error || `Grabbit refused the download (${res.status}).`;
    tell(pending, { text: msg, state: "error" });
    return { ok: false, error: msg };
  }
  const job = { ...data, server, reqId: req.reqId, tabId: tab && tab.id, title: data.title || req.title || hostOf(req.url),
    pageUrl: req.url, sentCookies: !!cookies, saved: false, started: Date.now() };
  await putJob(job);
  tell(job);
  pollJobs();
  return { ok: true, id: job.id };
}

let polling = false;

async function pollJobs() {
  if (polling) return;
  polling = true;
  try {
    for (;;) {
      const jobs = await getJobs();
      const live = Object.values(jobs).filter((j) => ACTIVE.has(j.status) || (j.status === "finished" && !j.saved));
      await updateBadge(live.length);
      if (!live.length) break;
      const byServer = new Map();
      for (const j of live) byServer.set(j.server, [...(byServer.get(j.server) || []), j]);
      for (const [server, list] of byServer) {
        try {
          const res = await fetch(`${server}/api/jobs?ids=${list.map((j) => encodeURIComponent(j.id)).join(",")}`);
          const { jobs: updates } = await res.json();
          for (const u of updates) await applyUpdate(jobs[u.id], u);
        } catch { /* server hiccup: try again next round */ }
      }
      await sleep(1000);
    }
  } finally {
    polling = false;
  }
}

async function applyUpdate(job, u) {
  if (!job) return;
  if (u.status === "gone") {
    job.status = "error";
    job.error = "The download expired on the server.";
  } else {
    Object.assign(job, u, { title: job.title && job.title !== hostOf(job.pageUrl) ? job.title : (u.title || job.title) });
  }
  if (job.status === "finished" && !job.saved) {
    job.saved = true;
    const r = await directDownload(`${job.server}/api/jobs/${encodeURIComponent(job.id)}/file`, job.filename);
    if (!r.ok) job.saveError = r.error;
  }
  job.message = statusText(job); // the popup shows the same wording as the page toast
  await putJob(job);
  tell(job);
}

async function updateBadge(n) {
  await chrome.action.setBadgeBackgroundColor({ color: "#5b4cf0" });
  await chrome.action.setBadgeText({ text: n ? String(n) : "" });
}

async function serverStatus(server) {
  const base = serverBase(server || (await getSettings()).server);
  try {
    const res = await fetch(`${base}/api/config`, { signal: AbortSignal.timeout(4000) });
    if (!res.ok) throw new Error(String(res.status));
    const cfg = await res.json();
    if (!cfg.ytdlp_version) throw new Error("not grabbit");
    return { ok: true, server: base, version: cfg.ytdlp_version, ffmpeg: cfg.ffmpeg };
  } catch {
    return { ok: false, server: base };
  }
}

/* --------------------------------------------------------- context menus */

function isDirect(url) {
  return /^(https?:|data:)/i.test(url || "") && !/\.(m3u8|mpd)(\?|#|$)/i.test(url);
}

function upgradeImageUrl(url) {
  try {
    const u = new URL(url);
    if (u.hostname === "pbs.twimg.com" && u.pathname.startsWith("/media/")) { u.searchParams.set("name", "orig"); return u.href; }
  } catch { /* keep as is */ }
  return url;
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    const contexts = ["page", "video", "audio", "link", "frame"];
    chrome.contextMenus.create({ id: "grabbit-video", title: "Download video with Grabbit", contexts });
    chrome.contextMenus.create({ id: "grabbit-audio", title: "Download audio (MP3) with Grabbit", contexts });
    chrome.contextMenus.create({ id: "grabbit-image", title: "Download image (full size)", contexts: ["image"] });
  });
  // Let the Grabbit desktop app know the extension is installed (its setup page is waiting for this).
  // Other Grabbit servers don't have this route and just answer 404.
  getSettings().then((s) => fetch(`${serverBase(s.server)}/desktop/hello`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version: chrome.runtime.getManifest().version }),
  })).catch(() => {});
  // Make buttons work in tabs that were already open, without a reload.
  chrome.tabs.query({ url: ["http://*/*", "https://*/*"] }).then((tabs) => {
    for (const t of tabs) {
      chrome.scripting.executeScript({ target: { tabId: t.id, allFrames: true }, files: ["content.js"] }).catch(() => {});
    }
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === "grabbit-image") {
    const r = await directDownload(upgradeImageUrl(info.srcUrl));
    if (!r.ok) tell({ reqId: crypto.randomUUID(), tabId: tab && tab.id, title: "Image", status: "error", error: r.error });
    return;
  }
  const media = info.mediaType === "video" || info.mediaType === "audio";
  const url = info.linkUrl || (media && isDirect(info.srcUrl) ? info.srcUrl : null) || info.frameUrl || info.pageUrl || (tab && tab.url);
  startJob({
    url,
    mode: info.menuItemId === "grabbit-audio" ? "audio" : "video",
    reqId: crypto.randomUUID(),
    title: tab && url === tab.url ? tab.title : null,
  }, tab);
});

/* -------------------------------------------------------------- messages */

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    switch (msg && msg.type) {
      case "download-direct":
        return directDownload(msg.url, msg.filename);
      case "download-many": {
        let ok = 0;
        for (const item of msg.items || []) if ((await directDownload(item.url, item.filename)).ok) ok++;
        return { ok: true, count: ok };
      }
      case "grabbit":
        return startJob(msg, sender.tab);
      case "grabbit-tab": {
        const tab = await chrome.tabs.get(msg.tabId);
        return startJob({ ...msg, url: msg.url || tab.url, reqId: crypto.randomUUID(), title: msg.title || tab.title }, tab);
      }
      case "server-status":
        return serverStatus(msg.server);
      case "jobs":
        return Object.values(await getJobs()).sort((a, b) => b.started - a.started);
      case "clear-jobs": {
        const jobs = await getJobs();
        for (const [id, j] of Object.entries(jobs)) if (!ACTIVE.has(j.status)) delete jobs[id];
        await chrome.storage.session.set({ jobs });
        return { ok: true };
      }
      case "cancel-job": {
        const jobs = await getJobs();
        const job = jobs[msg.id];
        if (job) await fetch(`${job.server}/api/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST" }).catch(() => {});
        return { ok: true };
      }
      default:
        return { ok: false, error: "unknown message" };
    }
  })().then(sendResponse, (err) => sendResponse({ ok: false, error: String((err && err.message) || err) }));
  return true; // async response
});

// Resume progress tracking if the worker was restarted mid-download.
pollJobs();
