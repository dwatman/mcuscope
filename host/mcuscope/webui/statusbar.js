import { $, api, intField, MAX_BAUD } from "./state.js";
import { setKnownPorts } from "./terminal.js";
import { saveAttachedPortToConfig } from "./settings.js";

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

// Flash the daemon chip red briefly to surface a transient failure (e.g. a failed detach)
// that has no dedicated place in the UI. The message rides along as the chip's tooltip.
const DAEMON_TITLE =
  "daemon address (bind 0.0.0.0 to reach it across the LAN; set server.token in config.toml for that, and this page will ask for it)";
let daemonFlashTimer = null;
function flashDaemonError(msg) {
  const el = $("daemon");
  if (!el) return;
  el.classList.add("flash-err");
  el.title = msg;
  clearTimeout(daemonFlashTimer);
  daemonFlashTimer = setTimeout(() => {
    el.classList.remove("flash-err");
    el.title = DAEMON_TITLE;
  }, 2500);
}

// Human-readable byte size, exported so the settings dialog labels the cap in the same
// units the status bar shows.
export function fmtBytes(n) {
  if (!Number.isFinite(n) || n < 0) return "";
  if (n < 1024) return n + " B";
  const units = ["kB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (v < 10 ? v.toFixed(1) : Math.round(v)) + " " + units[i];
}

function renderDaemon(s) {
  $("daemonVer").textContent = "mcuscoped " + s.version;
  $("daemonHost").textContent = location.host;
  uptimeBase = s.uptime_s;
  uptimeAt = Date.now();
  tickUptime();
  renderDbSize(s);
  renderSession(s.session);
  renderUpdate(s);
}

// ---- "update available" badge (SPEC 3.6) ---------------------------------------------
//
// The daemon does the checking; this only shows what it found. Dismissing hides *this
// version* and nothing else: a newer release is different news and shows the badge again,
// so brushing one aside can never silence the next. The stored value is the dismissed
// version string, so there is no record shape to corrupt and no expiry to get wrong.

const DISMISS_KEY = "mcuscope.updateDismissed";

function dismissedVersion() {
  try {
    return localStorage.getItem(DISMISS_KEY);
  } catch {
    return null;   // unreadable/disabled storage: behave as if nothing was dismissed
  }
}

function dismissUpdate(version) {
  try {
    localStorage.setItem(DISMISS_KEY, version);
  } catch { /* storage disabled: the badge simply comes back on reload */ }
  renderUpdateBadge(version);
}

let updateInfo = null;   // the daemon's last /status update block

function renderUpdate(s) {
  updateInfo = s.update && s.update.available ? s.update : null;
  renderUpdateBadge(updateInfo ? updateInfo.latest : null);
}

function renderUpdateBadge(version) {
  const el = $("updateBadge");
  if (!el) return;
  if (!version || !updateInfo || updateInfo.latest !== version || dismissedVersion() === version) {
    el.hidden = true;
    return;
  }
  const link = $("updateLink");
  link.textContent = "update: " + version;
  // Validate at the sink, not just at the source. The daemon currently sends a hard-coded
  // constant here, but this is an href assignment fed by a field that names PyPI: a
  // "javascript:" value would execute on click, and the guarantee should not live 700
  // lines away in Python.
  const fallback = "https://pypi.org/project/mcuscope/";
  const href = updateInfo.url || fallback;
  link.href = /^https?:\/\//i.test(href) ? href : fallback;
  // The two installers README.md documents, matching what `mcu status` prints.
  link.title = `MCUscope ${version} has been released. Upgrade with:  `
    + `uv tool upgrade mcuscope   (or: pipx upgrade mcuscope)`;
  el.hidden = false;
}

// ---- session control ----------------------------------------------------------------
//
// One button, because a session is one piece of state: with none running it starts one,
// with one running it shows the name and stops it. The boundaries also land in the
// terminal as marker dividers, so the run is visible there without consulting this chip.
//
// The daemon's own automatic session does not count as "running" here. It is not something
// anyone started, it covers the whole daemon run, and treating it as running would leave
// the button permanently showing stop for a session nobody asked for - and no way to start
// a named one.

let activeSession = null;

function renderSession(session) {
  activeSession = session && !session.auto ? session : null;
  const btn = $("sessionBtn");
  if (!btn) return;
  if (activeSession) {
    btn.textContent = "■ " + activeSession.name;   // stop square
    btn.classList.add("primary");
    btn.title = `Recording session "${activeSession.name}" (id ${activeSession.id}). Click to end it.`;
  } else {
    btn.textContent = "● session";                 // record dot
    btn.classList.remove("primary");
    btn.title = session && session.auto
      ? `Capture is covered by the automatic run "${session.name}". Click to name a run of your own.`
      : "Name a span of the capture so this run can be queried and exported on its own";
  }
}

async function toggleSession() {
  try {
    if (activeSession) {
      await api("POST", "/sessions/stop", {});
    } else {
      const name = window.prompt("Session name", "run-" + new Date().toISOString().slice(0, 16));
      if (!name) return;
      await api("POST", "/sessions", { name, note: "" });
    }
  } catch (e) {
    flashDaemonError("session: " + e.message);
  }
  refreshStatus();
}

// Capture size in the status bar, so a size cap is chosen against a real number rather
// than guessed. With a cap set it reads "used / cap"; the element also carries a warning
// once the cap has actually trimmed anything.
function renderDbSize(s) {
  const el = $("daemonDb");
  if (!el) return;
  const size = fmtBytes(s.db_size_bytes);
  const cap = s.db_max_bytes ? " / " + fmtBytes(s.db_max_bytes) : "";
  el.textContent = size ? "db " + size + cap : "";
  el.classList.toggle("drop", !!s.lines_trimmed);
  el.title = s.lines_trimmed
    ? `Capture database size on disk. ${s.lines_trimmed} of the oldest lines have been trimmed to stay under the size cap.`
    : "Capture database size on disk";
}

// writeErrors is store-wide (/status.write_errors), not per port, but it belongs on every
// port chip: a connected port whose lines are not reaching the database is not healthy,
// and a green dot said it was. writerDead (/status.writer_alive === false) is the same
// shape one step worse: the writer task is gone, so nothing is being stored at all while
// the port stays "connected" and its rx count keeps climbing.
let portsSig = null;

function renderPorts(ports, writeErrors = 0, writerDead = false) {
  // Rebuilding the chips drops focus from the reconnect/detach buttons, and this runs on
  // every 5 s poll; compare what the chips actually display first (mirrors setKnownPorts).
  const sig = JSON.stringify([writeErrors, writerDead,
    ports.map((p) => [p.alias, p.device, p.resolved_device, p.description, p.baud,
                      !!p.connected, !!p.held, p.rx_dropped || 0])]);
  if (sig === portsSig) return;
  portsSig = sig;
  const host = $("ports");
  host.textContent = "";
  for (const pt of ports) {
    const chip = document.createElement("div");
    chip.className = "chip" + (pt.connected ? "" : " disc");
    // Alias and the short port name it landed on (/dev/ttyACM0, COM7): a by-id path is 70+
    // characters and pushed the header buttons onto a second line. The description, the
    // requested device string when it differs, and the baud ride along as the hover.
    const port = pt.resolved_device || "";
    chip.title = (port
      ? [pt.description, pt.device !== port ? pt.device : null]
      : [`waiting for ${pt.device}`]).concat(`@${pt.baud}`).filter(Boolean).join("\n");

    // The dot is the connect switch: green -> click to close the port and stop retrying
    // (held, red); red -> click to reconnect. Grey is the daemon's own loss of the device.
    const dot = document.createElement("button");
    dot.className = "dot" + (pt.held ? " crit"
      : pt.connected ? (writeErrors || writerDead ? " crit" : "") : " off");
    dot.title = pt.held
      ? `Reconnect ${pt.alias}`
      : `Disconnect ${pt.alias} (keeps the attachment; click again to reconnect)`;
    dot.setAttribute("aria-label", dot.title);
    dot.addEventListener("click", () => (pt.held ? reconnectPort(pt.alias) : holdPort(pt.alias)));
    chip.appendChild(dot);

    const alias = document.createElement("span");
    alias.className = "alias";
    alias.textContent = pt.alias;
    chip.appendChild(alias);

    if (port) {
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = port;
      chip.appendChild(meta);
    }

    // Lines shed because storage could not keep up: the capture has holes, so say so
    // rather than leaving the gap to be discovered by reading the log.
    if (pt.rx_dropped) {
      const drop = document.createElement("span");
      drop.className = "meta drop";
      drop.textContent = `${pt.rx_dropped} dropped`;
      drop.title = "Lines lost because capture could not keep up with the port";
      chip.appendChild(drop);
    }

    // Not a count but an end: with the store writer dead nothing more will be stored.
    if (writerDead) {
      const dead = document.createElement("span");
      dead.className = "meta drop";
      dead.textContent = "capture stopped";
      dead.title = "The daemon's store writer has died: received lines are no longer being saved. Restart mcuscoped.";
      chip.appendChild(dead);
    }

    // Lines the capture could not write to the database at all: received, counted, gone.
    if (writeErrors) {
      const werr = document.createElement("span");
      werr.className = "meta drop";
      werr.textContent = `${writeErrors} write error` + (writeErrors === 1 ? "" : "s");
      werr.title = "Lines that could not be stored (disk full or an I/O error); check the daemon log";
      chip.appendChild(werr);
    }

    if (!pt.connected && !pt.held) {   // held: the dot is the reconnect control
      // The daemon retries with backoff on its own; this skips the wait after e.g.
      // replugging the device, without having to detach and re-attach by hand.
      const rc = document.createElement("button");
      rc.className = "x reconnect";
      rc.title = `Reconnect ${pt.alias} now`;
      rc.setAttribute("aria-label", `Reconnect ${pt.alias} now`);
      rc.textContent = "↻";
      rc.addEventListener("click", () => reconnectPort(pt.alias));
      chip.appendChild(rc);
    }

    const x = document.createElement("button");
    x.className = "x";
    x.title = `Detach ${pt.alias}`;
    x.setAttribute("aria-label", `Detach ${pt.alias}`);
    x.textContent = "×";
    x.addEventListener("click", () => detachPort(pt.alias));
    chip.appendChild(x);

    host.appendChild(chip);
  }
}

// One poll at a time, with a client-side deadline. A daemon that accepts the connection and
// then stalls answers nothing and closes nothing, so the 5 s interval in app.js piled
// overlapping fetches up for as long as it lasted. Concurrent callers (the interval, a detach,
// the attach dialog) share the poll already in flight rather than starting another, so the
// guard costs an on-demand refresh nothing. The deadline is under the poll interval, so a
// stalled poll is always gone before its successor is due.
const STATUS_TIMEOUT_MS = 4000;
let statusInFlight = null;
let renderFaultLogged = false;

function refreshStatus() {
  if (statusInFlight) return statusInFlight;
  statusInFlight = pollStatus().finally(() => { statusInFlight = null; });
  return statusInFlight;
}

async function pollStatus() {
  let s;
  const ac = typeof AbortController === "function" ? new AbortController() : null;
  const timer = ac ? setTimeout(() => ac.abort(), STATUS_TIMEOUT_MS) : null;
  try {
    s = await api("GET", "/status", undefined, ac ? ac.signal : undefined);
  } catch {
    setDaemonOnline(false);
    renderSession(null);
    // The port chips and the db size are health surfaces too: with no answer from the daemon
    // there is no port health to report, and holding the last good reading left a green
    // "connected" chip and a stale size beside a "daemon unreachable" version string.
    renderPorts([]);
    renderDbSize({});
    return;
  } finally {
    clearTimeout(timer);
  }
  // Outside the fetch's catch. A throw from the rendering below is a fault in this file, not
  // an unreachable daemon, and reporting it as one painted a healthy daemon dead on every 5 s
  // poll for as long as the fault lasted. Keep the last good paint instead, and say so once:
  // this runs every 5 s, so a deterministic fault would otherwise fill the console.
  try {
    renderDaemon(s);
    renderPorts(s.ports || [], s.write_errors || 0, s.writer_alive === false);
    setKnownPorts((s.ports || []).map((p) => p.alias));
    setDaemonOnline(true);
  } catch (e) {
    if (!renderFaultLogged) { renderFaultLogged = true; console.error("status render failed:", e); }
  }
}

async function reconnectPort(alias) {
  try {
    await api("POST", "/ports/" + encodeURIComponent(alias) + "/reconnect");
  } catch (e) {
    flashDaemonError("reconnect " + alias + " failed: " + e.message);
  }
  refreshStatus();
}

async function holdPort(alias) {
  try {
    await api("POST", "/ports/" + encodeURIComponent(alias) + "/disconnect");
  } catch (e) {
    flashDaemonError("disconnect " + alias + " failed: " + e.message);
  }
  refreshStatus();
}

async function detachPort(alias) {
  try {
    await api("DELETE", "/ports/" + encodeURIComponent(alias));
  } catch (e) {
    // Surface the failure without a modal: flash the daemon chip red briefly with the reason.
    flashDaemonError("detach " + alias + " failed: " + e.message);
  }
  refreshStatus();
}

// ---- attach dialog -----------------------------------------------------------------

const dlg = $("attachDlg");

async function openAttach() {
  $("dlgErr").textContent = "";
  $("aliasInput").value = "";
  $("saveToConfig").checked = false;
  $("bindById").checked = false;
  await populateDevices();
  syncBaudCustom();
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
}

function closeAttach() {
  if (typeof dlg.close === "function") dlg.close();
  else dlg.removeAttribute("open");
}

let devices = [];   // GET /devices as of the last dialog open; the bind box reads by_id from it

async function populateDevices() {
  const sel = $("devSel");
  sel.textContent = "";
  devices = [];
  try {
    const body = await api("GET", "/devices");
    devices = body.devices || [];
  } catch (e) {
    $("dlgErr").textContent = "could not list devices: " + e.message;
  }
  for (const d of devices) {
    const opt = document.createElement("option");
    opt.value = d.device;   // what was picked; the bind box swaps in by_id at submit
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

function selectedDevice() {
  const v = $("devSel").value;
  return devices.find((d) => d.device === v) || null;
}

function syncDevCustom() {
  $("devCustom").style.display = $("devSel").value === "custom" ? "" : "none";
  // Binding needs a by-id path, which only Linux has and only for enumerated devices.
  const d = selectedDevice();
  $("bindRow").style.display = d && d.by_id ? "" : "none";
}
function syncBaudCustom() {
  $("baudCustom").style.display = $("baudSel").value === "custom" ? "" : "none";
}

function chosenDevice() {
  const v = $("devSel").value;
  if (v === "custom") return $("devCustom").value.trim();
  const d = selectedDevice();
  return $("bindById").checked && d && d.by_id ? d.by_id : v;
}
function chosenBaud() {
  const v = $("baudSel").value;
  return v === "custom" ? intField($("baudCustom").value) : intField(v);
}

async function submitAttach() {
  const device = chosenDevice();
  const baud = chosenBaud();
  const alias = $("aliasInput").value.trim();
  if (!alias) { $("dlgErr").textContent = "alias is required"; return; }
  if (!device) { $("dlgErr").textContent = "device is required"; return; }
  // Both bounds, mirroring PortAttach.baud (gt=0, le=MAX_BAUD).
  if (!Number.isFinite(baud) || baud <= 0 || baud > MAX_BAUD) {
    $("dlgErr").textContent = `baud must be 1-${MAX_BAUD}`; return;
  }
  try {
    await api("POST", "/ports", { alias, device, baud });
    if ($("saveToConfig").checked) saveAttachedPortToConfig(alias, device, baud);   // best-effort, see settings.js
    closeAttach();
    refreshStatus();
  } catch (e) {
    $("dlgErr").textContent = e.message;
  }
}

export function initStatusbar() {
$("updateDismiss").addEventListener("click", () => {
  if (updateInfo) dismissUpdate(updateInfo.latest);
});
$("sessionBtn").addEventListener("click", toggleSession);
$("attachBtn").addEventListener("click", openAttach);
$("dlgCancel").addEventListener("click", closeAttach);
$("dlgClose").addEventListener("click", closeAttach);
$("dlgAttach").addEventListener("click", submitAttach);
$("devSel").addEventListener("change", syncDevCustom);
$("baudSel").addEventListener("change", syncBaudCustom);
dlg.addEventListener("cancel", (e) => { e.preventDefault(); closeAttach(); });
}

export { refreshStatus, tickUptime, flashDaemonError };
