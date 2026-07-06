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
  populateCmdPort();
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
    for (const row of body.lines || []) { pushBuffer(row); canIngest(row); }
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
    canIngest(row);
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

// ---- CAN table (sidebar): latest-per-id view built from !can events -----------------
//
// Classic CAN-tool view: one row per (port, id), showing the latest payload plus a
// running message count, an EWMA of the inter-arrival period, and the age since the
// frame was last seen. Fed from the same rows as the terminal (backfill + one /ws), so
// it costs nothing extra on the wire; a small timer re-renders to keep ages ticking.

const CAN_ALPHA = 0.3;         // EWMA weight on the newest inter-arrival sample
const CAN_STALE_S = 3;         // age past which a row is dimmed as "stale"
const canRows = new Map();     // key -> {port, id, ext, rtr, dlc, hex, count, period, lastTs}
let canDirty = false;

// Mirror of protocol.parse_can_event: decode an `!can <tick> <flags> <id> <data|->`
// body, returning null on anything malformed (matching the daemon's tolerant handling).
function parseCanEvent(raw) {
  const p = raw.split(" ");
  if (p.length !== 5 || p[0] !== "!can" || !/^\d+$/.test(p[1])) return null;
  const flags = p[2];
  let ext = false, rtr = false;
  if (flags !== "-") {
    if (!/^[xr]+$/.test(flags)) return null;
    ext = flags.includes("x"); rtr = flags.includes("r");
  }
  const id = parseInt(p[3], 16);
  if (!Number.isFinite(id)) return null;
  const payload = p[4];
  let dlc, hex = "";
  if (rtr) {
    if (!/^\d$/.test(payload) || +payload > 8) return null;
    dlc = +payload;
  } else if (payload === "-") {
    dlc = 0;
  } else {
    if (!/^([0-9a-fA-F]{2})+$/.test(payload) || payload.length > 16) return null;
    dlc = payload.length / 2;
    hex = payload.toUpperCase();
  }
  return { id, ext, rtr, dlc, hex };
}

function canIngest(row) {
  if (row.chan !== "event" || !row.raw.startsWith("!can ")) return;
  const f = parseCanEvent(row.raw);
  if (!f) return;
  const port = row.port || "-";
  const key = port + "|" + (f.ext ? "x" : "s") + f.id;
  let e = canRows.get(key);
  if (!e) {
    e = { port, id: f.id, count: 0, period: null, lastTs: null };
    canRows.set(key, e);
  }
  if (e.lastTs !== null) {
    const dt = (row.ts - e.lastTs) * 1000;   // inter-arrival in ms
    if (dt >= 0) e.period = e.period === null ? dt : CAN_ALPHA * dt + (1 - CAN_ALPHA) * e.period;
  }
  e.ext = f.ext; e.rtr = f.rtr; e.dlc = f.dlc; e.hex = f.hex;
  e.lastTs = row.ts;
  e.count += 1;
  canDirty = true;
}

function fmtCanId(e) {
  return (e.ext ? e.id.toString(16).toUpperCase().padStart(8, "0")
                : e.id.toString(16).toUpperCase().padStart(3, "0"));
}

function fmtCanData(e) {
  if (e.rtr) return "remote";
  if (!e.hex) return "-";
  return e.hex.replace(/(..)(?=.)/g, "$1 ");   // "DEAD" -> "DE AD"
}

function fmtCanPeriod(ms) {
  if (ms == null) return "-";
  if (ms < 10) return ms.toFixed(1);
  if (ms < 10000) return String(Math.round(ms));
  return (ms / 1000).toFixed(1) + "s";
}

function fmtCanAge(sec) {
  if (sec < 1) return Math.round(sec * 1000) + "ms";
  if (sec < 60) return sec.toFixed(1) + "s";
  const m = Math.floor(sec / 60);
  return m + "m" + String(Math.floor(sec % 60)).padStart(2, "0") + "s";
}

function cell(cls, text) {
  const td = document.createElement("td");
  if (cls) td.className = cls;
  if (text !== undefined) td.textContent = text;
  return td;
}

function renderCan() {
  canDirty = false;
  const wrap = $("canWrap");
  const rows = [...canRows.values()];
  $("canCount").textContent = rows.length ? `${rows.length} id${rows.length === 1 ? "" : "s"}` : "";
  if (!rows.length) {
    const e = document.createElement("div");
    e.className = "empty-state";
    e.textContent = "No CAN frames seen yet. !can events populate this live.";
    wrap.replaceChildren(e);
    return;
  }
  const multi = new Set(rows.map((r) => r.port)).size > 1;
  rows.sort((a, b) => (a.port < b.port ? -1 : a.port > b.port ? 1 : a.id - b.id));
  const now = Date.now() / 1000;

  const table = document.createElement("table");
  table.className = "can";
  const thead = document.createElement("thead");
  const htr = document.createElement("tr");
  const cols = multi ? ["port", "id", "dlc", "data", "count", "ms", "age"]
                     : ["id", "dlc", "data", "count", "ms", "age"];
  for (const c of cols) {
    const th = document.createElement("th");
    if (c === "port" || c === "id" || c === "data") th.className = "l";
    th.textContent = c;
    htr.appendChild(th);
  }
  thead.appendChild(htr);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const e of rows) {
    const tr = document.createElement("tr");
    if (multi) {
      const pt = cell("l dim");
      pt.textContent = e.port;
      pt.style.color = portColor(e.port);
      tr.appendChild(pt);
    }
    const idc = cell("l");
    const idspan = document.createElement("span");
    idspan.className = "id";
    idspan.textContent = fmtCanId(e);
    idc.appendChild(idspan);
    if (e.ext) { const f = document.createElement("span"); f.className = "flag"; f.textContent = "ext"; idc.appendChild(f); }
    if (e.rtr) { const f = document.createElement("span"); f.className = "flag"; f.textContent = "rtr"; idc.appendChild(f); }
    tr.appendChild(idc);

    tr.appendChild(cell("", String(e.dlc)));
    tr.appendChild(cell("l data", fmtCanData(e)));
    tr.appendChild(cell("dim", String(e.count)));
    tr.appendChild(cell("dim", fmtCanPeriod(e.period)));
    const age = e.lastTs == null ? 0 : now - e.lastTs;
    tr.appendChild(cell(age < CAN_STALE_S ? "age-fresh" : "age-stale", fmtCanAge(age)));
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.replaceChildren(table);
}

function initCan() {
  $("canReset").addEventListener("click", () => { canRows.clear(); renderCan(); });
  // Re-render on a timer so ages tick even when no new frames arrive; skip the work
  // entirely when the table is empty and nothing changed.
  setInterval(() => { if (canRows.size || canDirty) renderCan(); }, 500);
}

// ---- command bar: cmd/raw send + inline result + marker (SPEC 9.1) ------------------
//
// cmd mode routes through POST /cmd (seq + wait, timeout field) and renders the response
// inline with a distinct ok/err/timeout style; raw mode posts to POST /send. Up/down walk
// a localStorage-persisted history. The command and its response also stream back into the
// terminal panes over /ws, so this strip is just the immediate, focused acknowledgement.

let cmdMode = "cmd";        // "cmd" | "raw"
const cmdHistory = [];      // oldest-first; persisted in localStorage
let histIdx = -1;           // -1 = editing a fresh line, else index into cmdHistory
let histDraft = "";         // in-progress line stashed while browsing history

function loadCmdHistory() {
  try {
    const h = JSON.parse(localStorage.getItem("cmdHistory"));
    if (Array.isArray(h)) cmdHistory.push(...h.filter((x) => typeof x === "string"));
  } catch { /* ignore */ }
}
function saveCmdHistory() {
  try { localStorage.setItem("cmdHistory", JSON.stringify(cmdHistory.slice(-100))); }
  catch { /* private mode */ }
}

// null lets the daemon resolve the sole attached port (SPEC 4); an explicit alias targets it.
function cmdPortValue() {
  const v = $("cmdPort").value;
  return v && v !== "auto" ? v : null;
}

function populateCmdPort() {
  const sel = $("cmdPort");
  if (!sel) return;
  const cur = sel.value || "auto";
  const opts = ["auto", ...knownAliases];
  if (cur !== "auto" && !opts.includes(cur)) opts.push(cur);
  sel.textContent = "";
  for (const a of opts) {
    const o = document.createElement("option");
    o.value = a;
    o.textContent = a;
    sel.appendChild(o);
  }
  sel.value = opts.includes(cur) ? cur : "auto";
}

function setCmdMode(mode) {
  cmdMode = mode;
  document.querySelectorAll("#modeToggle button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  $("timeoutBox").classList.toggle("off", mode === "raw");   // timeout only applies to cmd
  $("prompt").textContent = mode === "raw" ? "$" : ">";
  $("cmdInput").focus();
}

// The result strip only occupies space while a result is showing: it auto-dismisses a
// few seconds after the final outcome (longer for errors), and a click hides it early.
// The response also lives in the terminal, so nothing is lost when it collapses.
let resultHideTimer = null;
function hideResult() {
  clearTimeout(resultHideTimer);
  resultHideTimer = null;
  $("cmdResult").hidden = true;
}

function showResult(cls, code, query, detail, latency) {
  const box = $("cmdResult");
  box.className = "cmd-result " + cls;
  box.hidden = false;
  box.textContent = "";
  if (query) { const q = document.createElement("span"); q.className = "rq"; q.textContent = query; box.appendChild(q); }
  const c = document.createElement("span"); c.className = "rc"; c.textContent = code; box.appendChild(c);
  if (detail) { const d = document.createElement("span"); d.textContent = detail; box.appendChild(d); }
  if (latency != null) { const l = document.createElement("span"); l.className = "rlat"; l.textContent = "  " + latency.toFixed(1) + " ms"; box.appendChild(l); }

  clearTimeout(resultHideTimer);
  resultHideTimer = null;
  if (cls !== "pending") {   // pending is replaced by the final outcome; don't time it out
    resultHideTimer = setTimeout(hideResult, cls === "err" ? 9000 : 5000);
  }
}

async function submitCmd() {
  const input = $("cmdInput");
  const text = input.value.trim();
  if (!text) return;
  if (cmdHistory[cmdHistory.length - 1] !== text) { cmdHistory.push(text); saveCmdHistory(); }
  histIdx = -1; histDraft = "";
  input.value = "";
  const port = cmdPortValue();
  const prompt = cmdMode === "raw" ? "$ " : "> ";

  if (cmdMode === "raw") {
    try {
      await api("POST", "/send", { port, line: text });
      showResult("ok", "sent", prompt + text, "", null);
    } catch (e) {
      showResult("err", "error", prompt + text, e.message, null);
    }
    return;
  }

  let timeout = parseInt($("cmdTimeout").value, 10);
  if (!Number.isFinite(timeout) || timeout <= 0) timeout = 1000;
  showResult("pending", "...", prompt + text, "", null);
  try {
    const r = await api("POST", "/cmd", { port, cmd: text, timeout_ms: timeout });
    if (r.status === "ok") {
      showResult("ok", "ok", prompt + text, r.data || "", r.latency_ms);
    } else if (r.status === "err") {
      const nm = r.err_name ? `${r.err_name} (${r.err_code})` : `err ${r.err_code}`;
      showResult("err", "err", prompt + text, r.err_detail ? `${nm}: ${r.err_detail}` : nm, r.latency_ms);
    } else {
      showResult("wait", "timeout", prompt + text, `no response in ${timeout} ms`, null);
    }
  } catch (e) {
    showResult("err", "error", prompt + text, e.message, null);
  }
}

function historyPrev() {
  if (!cmdHistory.length) return;
  const input = $("cmdInput");
  if (histIdx === -1) { histDraft = input.value; histIdx = cmdHistory.length; }
  if (histIdx > 0) histIdx -= 1;
  input.value = cmdHistory[histIdx] || "";
  input.setSelectionRange(input.value.length, input.value.length);
}
function historyNext() {
  const input = $("cmdInput");
  if (histIdx === -1) return;
  histIdx += 1;
  if (histIdx >= cmdHistory.length) { histIdx = -1; input.value = histDraft; }
  else input.value = cmdHistory[histIdx];
  input.setSelectionRange(input.value.length, input.value.length);
}

async function submitMarker() {
  const input = $("markerInput");
  const text = input.value.trim();
  if (!text) return;
  try {
    await api("POST", "/marker", { port: cmdPortValue(), text });
    input.value = "";   // it lands as a divider line in the terminal via /ws
  } catch (e) {
    showResult("err", "error", "marker: " + text, e.message, null);
  }
}

function initCmdBar() {
  loadCmdHistory();
  populateCmdPort();
  document.querySelectorAll("#modeToggle button").forEach((b) =>
    b.addEventListener("click", () => setCmdMode(b.dataset.mode)));
  const input = $("cmdInput");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); submitCmd(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); historyPrev(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); historyNext(); }
  });
  $("cmdResult").addEventListener("click", hideResult);
  $("markerBtn").addEventListener("click", submitMarker);
  $("markerInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); submitMarker(); }
  });
}

// ---- boot --------------------------------------------------------------------------

initCmdBar();
initCan();
initTerminal();
refreshStatus();
setInterval(refreshStatus, 5000);   // port/version state changes rarely
setInterval(tickUptime, 1000);      // smooth local clock between polls
