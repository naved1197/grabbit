// Grabbit desktop setup page: shows status and walks through adding the extension.
"use strict";

const $ = (sel) => document.querySelector(sel);
let status = null;
let chosen = null;      // browser id
let pollTimer = null;
let wasConnected = null;

async function api(path, body) {
  const opts = body === undefined ? { cache: "no-store" } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 2600);
}

function browser() {
  return (status.browsers || []).find((b) => b.id === chosen) || null;
}

function renderBrowser() {
  const b = browser();
  for (const el of document.querySelectorAll(".b-name")) el.textContent = b ? b.name : "your browser";
  $("#ext-url").textContent = b ? b.extensions_url : "chrome://extensions";
  $("#dev-hint").textContent = b ? b.dev_mode_hint : "top right of the page";
  $("#open-ext").disabled = !b;
  for (const btn of document.querySelectorAll("#browser-pick button")) {
    btn.setAttribute("aria-checked", String(btn.dataset.id === chosen));
  }
}

function render() {
  const mac = status.platform === "mac";
  const connected = status.extension.connected;

  $("#version").textContent = `Version ${status.version} · yt-dlp ${status.ytdlp.version || "?"}` +
    (status.ytdlp.update_ready ? ` (update ${status.ytdlp.update_ready} ready)` : "");
  $("#where").textContent = mac
    ? "Look for the Grabbit icon in your menu bar, at the top right of the screen."
    : "Look for the Grabbit icon in the system tray, next to the clock (it may be under the ^ arrow).";
  $("#foot").textContent = mac
    ? "To quit Grabbit, click its menu bar icon and choose Quit Grabbit."
    : "To quit Grabbit, right-click its tray icon and choose Quit Grabbit.";

  const auto = status.autostart;
  $("#autostart-row").hidden = !auto.supported;
  $("#autostart").checked = auto.enabled;

  $("#ext-path").textContent = status.extension_dir;
  $(".paste-key").textContent = mac ? "(⌘V)" : "(Ctrl+V)";
  $(".enter-key").textContent = mac ? "Return" : "Enter";
  $("#picker-hint").textContent = mac
    ? "In the window that opens, press ⌘⇧G, paste the path, press Return, then click Select."
    : "In the window that opens, paste the path into the Folder box, then click Select Folder.";

  // Browser choice
  const list = status.browsers || [];
  $("#no-browser").hidden = list.length > 0;
  const pick = $("#browser-pick");
  pick.hidden = list.length < 2;
  if (!chosen || !list.some((b) => b.id === chosen)) chosen = list.length ? list[0].id : null;
  if (pick.childElementCount !== list.length) {
    pick.replaceChildren(...list.map((b) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "radio");
      btn.dataset.id = b.id;
      btn.textContent = b.name;
      btn.addEventListener("click", () => { chosen = b.id; renderBrowser(); });
      return btn;
    }));
  }
  renderBrowser();

  // Done or waiting
  $("#done").hidden = !connected;
  $("#waiting").hidden = connected;
  $("#steps-title").textContent = connected ? "Add Grabbit to another browser" : "Add the Download button to your browser";
  $("#steps-hint").textContent = connected ? "Only needed for a second browser." : "One time only. Takes about 30 seconds.";
  if (wasConnected === null) $("#steps-wrap").open = !connected;   // first render
  else if (connected && !wasConnected) {
    $("#steps-wrap").open = false;
    $("#done").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  wasConnected = connected;
  document.title = connected ? "Grabbit is ready" : "Set Up Grabbit";
}

async function refresh() {
  try {
    status = await api("/desktop/status");
    render();
  } catch {
    $("#where").textContent = "Grabbit isn't answering. Open the Grabbit app again.";
  }
  clearTimeout(pollTimer);
  pollTimer = setTimeout(refresh, status && status.extension.connected ? 15000 : 2000);
}

/** Copy text; if the browser refuses, select it on the page so Ctrl/⌘+C works. */
async function copy(text, selectEl) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const range = document.createRange();
    range.selectNodeContents(selectEl);
    const sel = getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    toast(status.platform === "mac" ? "Press ⌘C to copy" : "Press Ctrl+C to copy");
    return false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("#open-ext").addEventListener("click", async () => {
    const b = browser();
    if (!b) return;
    // Browsers don't let other programs or web pages open their Extensions page,
    // so copy the address for the person to paste.
    const copied = await copy(b.extensions_url, $("#ext-url"));
    try {
      await api("/desktop/open-browser", { browser: b.id });
      if (copied) toast(`Address copied. Paste it into ${b.name}'s address bar.`);
    } catch {
      toast(`Couldn't open ${b.name}. Open it yourself and go to ${b.extensions_url}`);
    }
  });
  $("#copy-path").addEventListener("click", async () => {
    if (await copy(status.extension_dir, $("#ext-path"))) toast("Folder path copied");
  });
  $("#show-folder").addEventListener("click", () => api("/desktop/reveal", {}).catch(() => {}));
  $("#autostart").addEventListener("change", async (e) => {
    try {
      const r = await api("/desktop/autostart", { enabled: e.target.checked });
      e.target.checked = r.enabled;
    } catch {
      e.target.checked = !e.target.checked;
    }
  });
  refresh();
});
