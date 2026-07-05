// mcu-interface web UI (SPEC 9.1). Vanilla JS, no build step, no network fetches
// beyond this daemon. All API calls are root-relative so the page works unchanged
// whether it is served from 127.0.0.1 or across the LAN (bind hwbridged to 0.0.0.0).
//
// Build progress: status/setup bar is live. Terminal, CAN table and command box
// are wired in later steps.

"use strict";

const $ = (id) => document.getElementById(id);

// ---- API helpers (same-origin, root-relative) --------------------------------------

async function api(method, path, body) {
  const opt = { method, cache: "no-store" };
  if (body !== undefined) {
    opt.headers = { "Content-Type": "application/json" };
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch { /* empty body */ }
  if (!r.ok) {
    const msg = (data && data.error) ? data.error : `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return data;
}

// ---- theme -------------------------------------------------------------------------

const root = document.documentElement;
(function initTheme() {
  const saved = localStorage.getItem("theme");
  if (saved === "dark" || saved === "light") root.setAttribute("data-theme", saved);
})();
$("themeBtn").addEventListener("click", () => {
  const cur = root.getAttribute("data-theme")
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  localStorage.setItem("theme", next);
});

// ---- status / setup bar ------------------------------------------------------------

function fmtUptime(sec) {
  sec = Math.max(0, Math.floor(sec));
  const d = Math.floor(sec / 86400); sec %= 86400;
  const h = Math.floor(sec / 3600); sec %= 3600;
  const m = Math.floor(sec / 60); const s = sec % 60;
  if (d) return `up ${d}d${h}h`;
  if (h) return `up ${h}h${m}m`;
  if (m) return `up ${m}m${s}s`;
  return `up ${s}s`;
}

// Uptime is ticked locally every second from the last poll, so the clock reads
// smoothly without polling /status once a second. uptimeBase is the server uptime
// at the moment (uptimeAt) it was fetched.
let uptimeBase = null;
let uptimeAt = 0;

function tickUptime() {
  if (uptimeBase === null) return;
  $("daemonUptime").textContent = fmtUptime(uptimeBase + (Date.now() - uptimeAt) / 1000);
}

function setDaemonOnline(online) {
  $("daemonDot").className = "dot " + (online ? "" : "crit");
  if (!online) {
    uptimeBase = null;
    $("daemonVer").textContent = "daemon unreachable";
    $("daemonHost").textContent = location.host;
    $("daemonUptime").textContent = "";
  }
}

function renderDaemon(s) {
  $("daemonVer").textContent = "hwbridged " + s.version;
  $("daemonHost").textContent = location.host;
  uptimeBase = s.uptime_s;
  uptimeAt = Date.now();
  tickUptime();
}

function renderPorts(ports) {
  const host = $("ports");
  host.textContent = "";
  for (const pt of ports) {
    const chip = document.createElement("div");
    chip.className = "chip" + (pt.connected ? "" : " disc");

    const dot = document.createElement("span");
    dot.className = "dot" + (pt.connected ? "" : " off");
    chip.appendChild(dot);

    const alias = document.createElement("span");
    alias.className = "alias";
    alias.textContent = pt.alias;
    chip.appendChild(alias);

    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = `${pt.device} @${pt.baud}`;
    chip.appendChild(meta);

    const x = document.createElement("button");
    x.className = "x";
    x.title = `Detach ${pt.alias}`;
    x.textContent = "×";
    x.addEventListener("click", () => detachPort(pt.alias));
    chip.appendChild(x);

    host.appendChild(chip);
  }
}

async function refreshStatus() {
  try {
    const s = await api("GET", "/status");
    renderDaemon(s);
    renderPorts(s.ports || []);
    setKnownPorts((s.ports || []).map((p) => p.alias));
    setDaemonOnline(true);
  } catch {
    setDaemonOnline(false);
  }
}

async function detachPort(alias) {
  try {
    await api("DELETE", "/ports/" + encodeURIComponent(alias));
  } catch (e) {
    // Surface the failure without a modal: flash the daemon chip red briefly.
    console.warn("detach failed:", e.message);
  }
  refreshStatus();
}

// ---- attach dialog -----------------------------------------------------------------

const dlg = $("attachDlg");

async function openAttach() {
  $("dlgErr").textContent = "";
  $("aliasInput").value = "";
  await populateDevices();
  syncBaudCustom();
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
}

function closeAttach() {
  if (typeof dlg.close === "function") dlg.close();
  else dlg.removeAttribute("open");
}

async function populateDevices() {
  const sel = $("devSel");
  sel.textContent = "";
  let devices = [];
  try {
    const body = await api("GET", "/devices");
    devices = body.devices || [];
  } catch (e) {
    $("dlgErr").textContent = "could not list devices: " + e.message;
  }
  for (const d of devices) {
    const opt = document.createElement("option");
    opt.value = d.by_id || d.device;
    const desc = d.description || d.vid_pid || "";
    opt.textContent = desc ? `${d.device}  -  ${desc}` : d.device;
    sel.appendChild(opt);
  }
  const sim = document.createElement("option");
  sim.value = "socket://127.0.0.1:9900";
  sim.textContent = "socket://127.0.0.1:9900  -  simulator (tcp)";
  sel.appendChild(sim);
  const custom = document.createElement("option");
  custom.value = "custom";
  custom.textContent = "custom...";
  sel.appendChild(custom);
  syncDevCustom();
}

function syncDevCustom() {
  $("devCustom").style.display = $("devSel").value === "custom" ? "" : "none";
}
function syncBaudCustom() {
  $("baudCustom").style.display = $("baudSel").value === "custom" ? "" : "none";
}

function chosenDevice() {
  const v = $("devSel").value;
  return v === "custom" ? $("devCustom").value.trim() : v;
}
function chosenBaud() {
  const v = $("baudSel").value;
  return v === "custom" ? parseInt($("baudCustom").value, 10) : parseInt(v, 10);
}

async function submitAttach() {
  const device = chosenDevice();
  const baud = chosenBaud();
  const alias = $("aliasInput").value.trim();
  if (!alias) { $("dlgErr").textContent = "alias is required"; return; }
  if (!device) { $("dlgErr").textContent = "device is required"; return; }
  if (!Number.isFinite(baud) || baud <= 0) { $("dlgErr").textContent = "baud must be a positive number"; return; }
  try {
    await api("POST", "/ports", { alias, device, baud });
    closeAttach();
    refreshStatus();
  } catch (e) {
    $("dlgErr").textContent = e.message;
  }
}

$("attachBtn").addEventListener("click", openAttach);
$("dlgCancel").addEventListener("click", closeAttach);
$("dlgClose").addEventListener("click", closeAttach);
$("dlgAttach").addEventListener("click", submitAttach);
$("devSel").addEventListener("change", syncDevCustom);
$("baudSel").addEventListener("change", syncBaudCustom);
dlg.addEventListener("cancel", (e) => { e.preventDefault(); closeAttach(); });

// ---- sidebar: section switch, collapse, resize (layout, no API) --------------------

const sidebar = $("sidebar");
const ws = $("workspace");

function setView(v) {
  sidebar.setAttribute("data-view", v);
  document.querySelectorAll("#sideSeg button").forEach((x) => x.classList.toggle("on", x.dataset.view === v));
}
document.querySelectorAll("#sideSeg button").forEach((b) =>
  b.addEventListener("click", () => setView(b.dataset.view)));

$("collapseBtn").addEventListener("click", () => ws.classList.add("collapsed"));
$("reopenBtn").addEventListener("click", () => ws.classList.remove("collapsed"));
$("popoutBtn").addEventListener("click", () => {
  ws.classList.remove("collapsed");
  setView("plots");
  ws.style.setProperty("--side-w", Math.round(ws.clientWidth * 0.55) + "px");
});

const resizer = $("resizer");
let dragging = false;
// Leave room for the terminal's 320px min column and the 6px divider, or the grid
// overflows the viewport and the page scrolls sideways.
const clampW = (x) => Math.max(260, Math.min(x, ws.clientWidth - 326));
resizer.addEventListener("pointerdown", (e) => {
  dragging = true; resizer.classList.add("drag"); resizer.setPointerCapture(e.pointerId);
});
resizer.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  const w = clampW(ws.getBoundingClientRect().right - e.clientX);
  ws.style.setProperty("--side-w", w + "px");
});
resizer.addEventListener("pointerup", (e) => {
  dragging = false; resizer.classList.remove("drag");
  try { resizer.releasePointerCapture(e.pointerId); } catch { /* not captured */ }
});
resizer.addEventListener("dblclick", () => ws.style.setProperty("--side-w", "360px"));

// ---- terminal: shared line buffer + dynamically added, per-pane filtered views -----
//
// One WebSocket (all ports) and one client-side ring buffer feed every pane; a pane is
// just a filtered projection of that buffer, so adding a pane costs nothing on the wire.
//
// Rendering is batched: incoming lines are queued and flushed once per animation frame
// (one reflow per pane per frame), and paused panes append nothing at all - the buffer
// keeps filling but the frozen pane does no DOM work, so CPU drops to idle when paused.

const ALL_CHANS = ["debug", "cmd", "resp", "event", "marker", "sys"];
const TAG = { debug: "dbg", cmd: "cmd", resp: "resp", event: "evt", marker: "mrk", sys: "sys" };
const BUFFER_MAX = 5000;   // shared backlog kept in memory
const VIEW_MAX = 5000;     // DOM lines kept per pane
const MAX_PANES = 5;       // enough for real use; one socket feeds them all
const REGEX_DEBOUNCE_MS = 200;
const FLUSH_MS = 33;       // ~30 fps: batch appends into one render per frame per pane
const LINE_H = 18;         // fixed row height (must match .ln height in style.css)
const OVERSCAN = 8;        // rows rendered above/below the viewport for smooth scrolling
const PORT_COLORS = ["#46c8d8", "#e0a458", "#b48ce8", "#5bd18b", "#ef7a5e", "#6fb2ff"];

const buffer = [];
let maxId = 0;
const panes = [];
let knownAliases = [];
let relTime = false;   // global: relative vs absolute timestamps for all panes
let anchorTs = null;   // fixed zero for relative time (first line the UI ever buffered)

function pushBuffer(row) {
  if (anchorTs === null) anchorTs = row.ts;
  buffer.push(row);
  if (row.id > maxId) maxId = row.id;
  if (buffer.length > BUFFER_MAX) buffer.shift();
}

const portColorCache = new Map();
function portColor(alias) {
  const s = alias || "";
  let c = portColorCache.get(s);
  if (c) return c;
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  c = PORT_COLORS[h % PORT_COLORS.length];
  portColorCache.set(s, c);
  return c;
}

function pad2(n) { return String(n).padStart(2, "0"); }

function fmtTs(pane, row) {
  if (relTime) {
    const base = anchorTs == null ? row.ts : anchorTs;
    return "+" + (row.ts - base).toFixed(3) + "s";
  }
  const d = new Date(row.ts * 1000);
  const ms = String(d.getMilliseconds()).padStart(3, "0");
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}.${ms}`;
}

function matches(pane, row) {
  if (pane.port !== "all" && row.port !== pane.port) return false;
  if (!pane.channels.has(row.chan)) return false;
  if (pane.regex && !pane.regex.test(row.raw)) return false;
  return true;
}

function buildLine(pane, row) {
  const chan = row.chan || "debug";
  const d = document.createElement("div");
  const ts = document.createElement("span");
  ts.className = "ts";
  ts.textContent = fmtTs(pane, row);

  if (chan === "marker") {
    d.className = "ln marker";
    const div = document.createElement("span");
    div.className = "divider";
    div.textContent = "marker: " + row.raw;
    d.append(ts, div);
    return d;
  }

  const isErr = chan === "resp" && /\bERR\b/.test(row.raw);
  d.className = "ln " + chan + (isErr ? " err" : "");
  d.appendChild(ts);
  if (pane.port === "all") {
    const pt = document.createElement("span");
    pt.className = "port-tag";
    pt.textContent = row.port || "-";
    pt.style.color = portColor(row.port);
    d.appendChild(pt);
  }
  const tag = document.createElement("span");
  tag.className = "tag";
  tag.textContent = TAG[chan] || chan;
  d.appendChild(tag);
  const msg = document.createElement("span");
  msg.className = "msg";
  msg.textContent = row.raw;
  d.appendChild(msg);
  return d;
}

function updateShown(pane) {
  pane.shownEl.textContent = pane.rows.length + " lines";
}

function updateJump(pane) {
  pane.jumpBtn.textContent = pane.pending > 0 ? `↓ ${pane.pending} new` : "↓ latest";
}

// Virtualized render: only the ~visible screenful of rows ever exists in the DOM.
// paddingTop / paddingBottom stand in for the off-screen rows so the scrollbar is exact
// and scrolling is smooth. Render cost is bounded (one screenful) no matter how many
// thousands of lines are buffered - this is what keeps CPU low.
function render(pane) {
  const sc = pane.scrollEl;
  const total = pane.rows.length;
  const viewH = sc.clientHeight || 300;
  const visCount = Math.ceil(viewH / LINE_H) + OVERSCAN * 2;
  let first;
  if (pane.autoscroll) {
    first = Math.max(0, total - visCount);
  } else {
    const maxFirst = Math.max(0, total - visCount);
    first = Math.min(Math.max(0, Math.floor(sc.scrollTop / LINE_H) - OVERSCAN), maxFirst);
  }
  const last = Math.min(total, first + visCount);
  pane.winFirst = first;
  pane.winLast = last;

  const frag = document.createDocumentFragment();
  for (let i = first; i < last; i++) frag.appendChild(buildLine(pane, pane.rows[i]));
  pane.vlist.style.paddingTop = (first * LINE_H) + "px";
  pane.vlist.style.paddingBottom = ((total - last) * LINE_H) + "px";
  pane.vlist.replaceChildren(frag);
  updateShown(pane);
  if (pane.autoscroll) { pane.selfScroll = true; sc.scrollTop = 1e9; }
}

// Coalesce scroll-driven re-virtualization into one render per frame per pane.
let renderScheduled = false;
const renderQueue = new Set();
function scheduleRender(pane) {
  renderQueue.add(pane);
  if (renderScheduled) return;
  renderScheduled = true;
  requestAnimationFrame(() => {
    renderScheduled = false;
    const q = [...renderQueue];
    renderQueue.clear();
    for (const p of q) render(p);
  });
}

function setAutoscroll(pane, on) {
  pane.autoscroll = on;
  pane.pending = 0;
  pane.pill.textContent = on ? "live" : "paused";
  pane.pill.className = "pill " + (on ? "live" : "paused");
  if (on) {
    pane.jumpBtn.classList.remove("show");
    render(pane);              // snap to the latest and resume
  } else {
    updateJump(pane);
    pane.jumpBtn.classList.add("show");
  }
  updateShared();
}

function updateShared() {
  const anyLive = panes.some((p) => p.autoscroll);
  $("pauseAllBtn").textContent = anyLive ? "pause all" : "resume all";
}

// Recompute a pane's line set from the shared buffer (its filter changed). Preserves the
// pane's live/paused state - re-filtering never resumes a paused pane.
function rebuild(pane) {
  pane.rows = buffer.filter((row) => row.id > pane.clearId && matches(pane, row));
  if (pane.rows.length > VIEW_MAX) pane.rows.splice(0, pane.rows.length - VIEW_MAX);
  render(pane);
}

// A throttled flush appends queued lines to each pane's row array. Live panes re-render
// their (bounded) visible window; paused panes only grow the bottom spacer and a counter,
// so a paused pane does almost no work even while data keeps pouring in.
let flushTimer = null;
function scheduleFlush() {
  if (flushTimer) return;
  flushTimer = setTimeout(flush, FLUSH_MS);
}
function flush() {
  flushTimer = null;
  for (const pane of panes) {
    if (!pane.queue.length) continue;
    const added = pane.queue.length;
    for (const r of pane.queue) pane.rows.push(r);
    pane.queue.length = 0;

    let dropped = 0;
    if (pane.rows.length > VIEW_MAX) {
      dropped = pane.rows.length - VIEW_MAX;
      pane.rows.splice(0, dropped);
    }

    if (pane.autoscroll) {
      render(pane);
    } else {
      pane.pending += added;
      updateJump(pane);
      // Keep the frozen viewport perfectly still: if lines dropped off the top, shift the
      // window indices and scroll to compensate; then grow the bottom spacer for the new
      // lines. No visible rows are rebuilt.
      if (dropped) {
        pane.winFirst = Math.max(0, pane.winFirst - dropped);
        pane.winLast = Math.max(0, pane.winLast - dropped);
        pane.selfScroll = true;
        pane.scrollEl.scrollTop = Math.max(0, pane.scrollEl.scrollTop - dropped * LINE_H);
        pane.vlist.style.paddingTop = (pane.winFirst * LINE_H) + "px";
      }
      pane.vlist.style.paddingBottom = ((pane.rows.length - pane.winLast) * LINE_H) + "px";
    }
  }
}

function applyRegex(pane, src) {
  pane.regexSrc = src;
  if (!src) { pane.regex = null; pane.matchInput.classList.remove("invalid"); return; }
  try { pane.regex = new RegExp(src); pane.matchInput.classList.remove("invalid"); }
  catch { pane.regex = null; pane.matchInput.classList.add("invalid"); }
}

function populatePortSelect(pane) {
  const sel = pane.portSel;
  const cur = pane.port;
  const opts = ["all", ...knownAliases];
  if (cur !== "all" && !opts.includes(cur)) opts.push(cur);
  sel.textContent = "";
  for (const a of opts) {
    const o = document.createElement("option");
    o.value = a;
    o.textContent = a === "all" ? "all ports" : a;
    sel.appendChild(o);
  }
  sel.value = cur;
}

function setKnownPorts(aliases) {
  knownAliases = aliases;
  panes.forEach(populatePortSelect);
}

function updatePaneButtons() {
  const only = panes.length <= 1;
  panes.forEach((p) => { p.el.querySelector(".closepane").disabled = only; });
  $("addPaneBtn").disabled = panes.length >= MAX_PANES;
  $("paneCount").textContent = `${panes.length} / ${MAX_PANES}`;
}

function createPane(cfg) {
  const el = $("paneTpl").content.firstElementChild.cloneNode(true);
  const scrollEl = el.querySelector(".scrollback");
  const vlist = document.createElement("div");
  vlist.className = "vlist";
  scrollEl.appendChild(vlist);
  const pane = {
    el,
    scrollEl,
    vlist,
    portSel: el.querySelector(".port-sel"),
    matchInput: el.querySelector(".match"),
    pill: el.querySelector(".pill"),
    jumpBtn: el.querySelector(".jump"),
    shownEl: el.querySelector(".shown"),
    port: cfg.port || "all",
    channels: new Set(cfg.channels && cfg.channels.length ? cfg.channels : ALL_CHANS),
    regex: null,
    regexSrc: "",
    autoscroll: true,
    baseTs: null,
    regexTimer: null,
    rows: [],         // this pane's filtered lines (data, not DOM); virtualized on render
    queue: [],        // rows waiting for the next flush
    pending: 0,       // matching rows seen while paused (shown on the jump button)
    winFirst: 0,      // index range currently rendered into the DOM
    winLast: 0,
    clearId: 0,       // "cleared" boundary: rebuild ignores buffered lines up to this id
    selfScroll: false,
  };

  el.querySelectorAll(".chk").forEach((chk) => {
    const ch = chk.dataset.ch;
    if (!pane.channels.has(ch)) chk.classList.add("off");
    chk.addEventListener("click", () => {
      if (pane.channels.has(ch)) { pane.channels.delete(ch); chk.classList.add("off"); }
      else { pane.channels.add(ch); chk.classList.remove("off"); }
      rebuild(pane); persistState();
    });
  });

  pane.matchInput.value = cfg.regex || "";
  applyRegex(pane, cfg.regex || "");
  pane.matchInput.addEventListener("input", () => {
    applyRegex(pane, pane.matchInput.value);   // validity feedback is immediate
    clearTimeout(pane.regexTimer);             // re-render is debounced
    pane.regexTimer = setTimeout(() => { rebuild(pane); persistState(); }, REGEX_DEBOUNCE_MS);
  });

  el.querySelector(".clear").addEventListener("click", () => {
    pane.clearId = maxId; pane.rows = []; render(pane);
  });
  el.querySelector(".closepane").addEventListener("click", () => closePane(pane));

  pane.portSel.addEventListener("change", () => {
    pane.port = pane.portSel.value; rebuild(pane); persistState();
  });

  pane.scrollEl.addEventListener("scroll", () => {
    if (pane.selfScroll) { pane.selfScroll = false; return; }
    const sc = pane.scrollEl;
    const atBottom = sc.scrollHeight - sc.scrollTop - sc.clientHeight < LINE_H;
    if (atBottom && !pane.autoscroll) setAutoscroll(pane, true);
    else if (!atBottom && pane.autoscroll) setAutoscroll(pane, false);
    else if (!pane.autoscroll) scheduleRender(pane);   // re-virtualize the visible window
  });

  pane.pill.addEventListener("click", () => setAutoscroll(pane, !pane.autoscroll));
  pane.jumpBtn.addEventListener("click", () => setAutoscroll(pane, true));

  return pane;
}

function addPane(cfg) {
  if (panes.length >= MAX_PANES) return;
  const pane = createPane(cfg || {});
  panes.push(pane);
  $("terminalArea").appendChild(pane.el);
  populatePortSelect(pane);
  rebuild(pane);
  updatePaneButtons();
  persistState();
}

function closePane(pane) {
  if (panes.length <= 1) return;
  const i = panes.indexOf(pane);
  if (i < 0) return;
  panes.splice(i, 1);
  pane.el.remove();
  updatePaneButtons();
  persistState();
}

function persistState() {
  const st = {
    rel: relTime,
    panes: panes.map((p) => ({ port: p.port, channels: [...p.channels], regex: p.regexSrc })),
  };
  try { localStorage.setItem("termState", JSON.stringify(st)); } catch { /* private mode */ }
}

function updateRelBtn() {
  $("relBtn").classList.toggle("on", relTime);
}

function loadState() {
  let st = null;
  try { st = JSON.parse(localStorage.getItem("termState")); } catch { /* ignore */ }
  if (st && typeof st.rel === "boolean") relTime = st.rel;
  let cfgs = st && Array.isArray(st.panes) ? st.panes : null;
  if (!cfgs || !cfgs.length) cfgs = [{ port: "all", channels: ALL_CHANS, regex: "" }];
  for (const c of cfgs) addPane(c);
  updateRelBtn();
}

async function backfill() {
  try {
    const body = await api("GET", "/lines?order=asc&limit=200");
    for (const row of body.lines || []) pushBuffer(row);
  } catch { /* daemon may be down; ws will catch up */ }
  panes.forEach(rebuild);
}

let wsReconnect = null;
function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let sock;
  try { sock = new WebSocket(`${proto}://${location.host}/ws`); }
  catch { scheduleWsReconnect(); return; }
  sock.onmessage = (ev) => {
    let row;
    try { row = JSON.parse(ev.data); } catch { return; }
    if (!row || typeof row.id !== "number" || row.id <= maxId) return;
    pushBuffer(row);
    let need = false;
    for (const p of panes) {
      if (!matches(p, row)) continue;
      p.queue.push(row);   // flush() appends to rows; live panes render, paused panes count
      need = true;
    }
    if (need) scheduleFlush();
  };
  sock.onclose = scheduleWsReconnect;
  sock.onerror = () => { try { sock.close(); } catch { /* already closing */ } };
}
function scheduleWsReconnect() {
  if (wsReconnect) return;
  wsReconnect = setTimeout(() => { wsReconnect = null; connectWs(); }, 1000);
}

function initTerminal() {
  $("addPaneBtn").addEventListener("click", () => {
    const last = panes[panes.length - 1];
    addPane(last ? { port: last.port, channels: [...last.channels], regex: last.regexSrc } : {});
  });
  $("relBtn").addEventListener("click", () => {
    relTime = !relTime; updateRelBtn(); panes.forEach(rebuild); persistState();
  });
  $("pauseAllBtn").addEventListener("click", () => {
    const target = !panes.some((p) => p.autoscroll);   // any live -> pause all; else resume all
    panes.forEach((p) => setAutoscroll(p, target));
  });
  $("clearAllBtn").addEventListener("click", () => {
    anchorTs = null;   // re-zero relative time from here; next line becomes +0.000s
    panes.forEach((p) => { p.clearId = maxId; p.rows = []; render(p); });
  });
  window.addEventListener("resize", () => panes.forEach(scheduleRender));   // visible count changes
  loadState();
  updateShared();
  backfill().then(connectWs);
}

// ---- boot --------------------------------------------------------------------------

initTerminal();
refreshStatus();
setInterval(refreshStatus, 5000);   // port/version state changes rarely
setInterval(tickUptime, 1000);      // smooth local clock between polls
