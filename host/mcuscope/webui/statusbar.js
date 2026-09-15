import { $, api, intField, state, MAX_BAUD, fillEolOptions, DEFAULT_EOL, STATUS_TIMEOUT_MS } from "./state.js";
import { setKnownPorts } from "./terminal.js";
import { syncCmdEol, syncCmdMode, setCmdOffline } from "./cmdbar.js";
import { saveAttachedPortToConfig } from "./settings.js";
import { scheduleResizeRedraw } from "./plots.js";
import { enterSubmits } from "./chrome.js";
import { noteDaemonNow } from "./can.js";

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

// Byte-identical to the title index.html starts with. The token is runtime-only
// (--token / MCUSCOPED_TOKEN): config.py ignores a server.token key.
const DAEMON_TITLE = "Daemon address. To reach this page across the LAN, start mcuscoped with "
  + "--host 0.0.0.0 and set MCUSCOPED_TOKEN; this page will then ask for the token.";

function setDaemonOnline(online) {
  $("daemonDot").className = "dot " + (online ? "" : "crit");
  if (!online) {
    $("daemonVer").textContent = "daemon unreachable";
    $("daemonHost").textContent = location.host;
    $("daemon").title = DAEMON_TITLE;
  }
}

// A failed action (detach, disconnect, reconnect, session, export) flashes the daemon chip as
// the cue and leaves its reason in the strip under the bar. The strip stays until dismissed,
// replaced by the next failure, or cleared by the next action that succeeds; not by a poll,
// since every action polls straight after and would erase its own failure.
let daemonFlashTimer = null;
let failGen = 0;   // bumped by every failure shown; see clearFailureSince
function flashDaemonError(msg) {
  failGen++;
  const el = $("daemon");
  if (el) {
    el.classList.add("flash-err");
    clearTimeout(daemonFlashTimer);
    daemonFlashTimer = setTimeout(() => el.classList.remove("flash-err"), 2500);
  }
  setActionError(msg);
}

function setActionError(msg) {
  const strip = $("actionErr");
  if (!strip) return;
  const was = strip.hidden;
  $("actionErrText").textContent = msg || "";
  strip.hidden = !msg;
  if (was !== strip.hidden) scheduleResizeRedraw();   // the workspace row changed height
}

// An action that succeeded clears the strip, unless a failure was shown after it started
// (`g` is failGen then): a slow detach must not erase the reconnect refusal that landed first.
function clearFailureSince(g) {
  if (g === failGen) setActionError("");
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
  $("brandVer").textContent = s.version;
  $("daemonVer").textContent = "";
  $("daemonHost").textContent = location.host;
  // Uptime and size are hover detail: the address is the only thing on the chip anyone copies.
  const { content, cap, disk } = dbFigures(s);
  $("daemon").title = [`mcuscoped ${s.version}, ${fmtUptime(s.uptime_s)}`
    + (content ? `, db ${content}${cap}` + (disk ? ` (${disk} on disk)` : "") : ""),
  DAEMON_TITLE].join("\n");
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

function showDlg(d) {
  if (typeof d.showModal === "function") d.showModal();
  else d.setAttribute("open", "");
}
function closeDlg(d) {
  if (typeof d.close === "function") d.close();
  else d.removeAttribute("open");
}

// Stopping needs nothing more; starting asks for a name and an optional note. The default
// name is local time with no characters that need quoting as `--session`, like auto-<stamp>.
async function toggleSession() {
  if (!activeSession) { openSessionDialog(); return; }
  const g = failGen;
  try {
    await api("POST", "/sessions/stop", {});
    clearFailureSince(g);
  } catch (e) {
    flashDaemonError("session: " + e.message);
  }
  refreshStatus(true);   // after an action: not a poll that predates it
}

const sesDlg = $("sessionDlg");
let sesGen = 0;   // per dialog open: a start's refusal is not written into a later opening

function openSessionDialog() {
  sesGen++;
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  $("sesName").value = `run-${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_${p(d.getHours())}-${p(d.getMinutes())}`;
  $("sesNote").value = "";
  $("sesErr").textContent = "";
  showDlg(sesDlg);
  $("sesName").select();   // autofocused; typing replaces the default
}

// A refusal stays in the dialog beside the name that caused it, as in the attach dialog.
async function startSession() {
  const btn = $("sesStart");
  if (btn.disabled) return;   // one start in flight: a held Enter must not start two sessions
  const name = $("sesName").value.trim();
  if (!name) { $("sesErr").textContent = "Name is required"; return; }
  btn.disabled = true;
  const g = failGen, gen = sesGen;
  try {
    await api("POST", "/sessions", { name, note: $("sesNote").value.trim() });
    clearFailureSince(g);
    closeDlg(sesDlg);   // a reopened dialog too: the session it would start is now running
    refreshStatus(true);   // after an action: not a poll that predates it
  } catch (e) {
    if (gen === sesGen) $("sesErr").textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

// The capture size lives in the daemon chip's hover (renderDaemon); it shows in the bar only
// once the size cap has trimmed lines, as a warning, since a capture with holes looks clean.
// The cap is enforced against db_content_bytes, which is what sits beside it; the file on disk
// keeps freed pages and reads larger after a trim, so it is only ever the "on disk" aside.
function dbFigures(s) {
  return { content: fmtBytes(s.db_content_bytes),
           cap: s.db_max_bytes ? " / " + fmtBytes(s.db_max_bytes) : "",
           disk: fmtBytes(s.db_size_bytes) };
}

function renderDbSize(s) {
  const el = $("daemonDb");
  if (!el) return;
  const { content, cap, disk } = dbFigures(s);
  el.textContent = content && s.lines_trimmed ? "db " + content + cap : "";
  el.classList.toggle("drop", !!s.lines_trimmed);
  const onDisk = disk ? ` ${disk} on disk.` : "";
  el.title = s.lines_trimmed
    ? `Capture database content.${onDisk} ${s.lines_trimmed} of the oldest lines have been trimmed to stay under the size cap.`
    : `Capture database content.${onDisk}`;
}

// writeErrors is store-wide (/status.write_errors), not per port, but it belongs on every
// port chip: a connected port whose lines are not reaching the database is not healthy,
// and a green dot said it was. writerDead (/status.writer_alive === false) is the same
// shape one step worse: the writer task is gone, so nothing is being stored at all while
// the port stays "connected" and its rx count keeps climbing.
let portsSig = null;
// alias -> the rate cell of the chip on screen, so the per-port rate can be written without
// rebuilding the chip (the same trick can.js uses for its age column). It is derived from a
// counter that moves on every poll, so it must stay OUT of portsSig or the chips would be
// rebuilt - and focus dropped - every 5 s.
let rateCells = new Map();
let prevRx = null;      // alias -> lines_rx at the previous poll
let prevRxAt = 0;       // Date.now() of that poll

// Lines per second for one port from two /status polls. Null wherever the figure would be a
// lie rather than a number: the first poll, a counter that went backwards (daemon restarted,
// the port re-attached), or two polls that arrived at the same instant.
export function portRate(prev, rx, dtSeconds) {
  if (prev == null || !Number.isFinite(rx) || !Number.isFinite(prev)) return null;
  if (rx < prev) return null;
  if (!Number.isFinite(dtSeconds) || dtSeconds <= 0) return null;
  return Math.round((rx - prev) / dtSeconds);
}

function renderPortRates(ports) {
  const now = Date.now();
  const dt = (now - prevRxAt) / 1000;
  const next = new Map();
  for (const pt of ports) {
    next.set(pt.alias, pt.lines_rx);
    const cell = rateCells.get(pt.alias);
    if (!cell) continue;
    const r = prevRx ? portRate(prevRx.get(pt.alias), pt.lines_rx, dt) : null;
    cell.textContent = r == null ? "" : `${r}/s`;
  }
  prevRx = next;
  prevRxAt = now;
}

// Plain English for /status.disconnect_reason, which is a wire token. Null-prototyped
// because the key comes off the wire; an unknown reason falls back to the token itself so a
// future value stays visible rather than blanking the line.
const DISCONNECT_WHY = Object.assign(Object.create(null), {
  connecting: "opening the port for the first time",
  manual: "disconnected on request",
  no_device: "device not present (power, cable, or still enumerating)",
  open_failed: "device present but the open failed (busy, or permissions)",
  read_error: "the link dropped mid-session",
});

// `known` is false while the daemon is unreachable: no chips, and no "no ports attached"
// either, since an unknown port list is not an empty one.
function renderPorts(ports, writeErrors = 0, writerDead = false, known = true) {
  // Rebuilding the chips drops focus from the reconnect/detach buttons, and this runs on
  // every 5 s poll; compare what the chips actually display first (mirrors setKnownPorts).
  const sig = JSON.stringify([known, writeErrors, writerDead,
    ports.map((p) => [p.alias, p.device, p.resolved_device, p.description, p.baud,
                      !!p.connected, !!p.held, p.disconnect_reason || "",
                      p.rx_dropped || 0, p.write_failures || 0,
                      p.last_write_error || "", p.target || ""])]);
  if (sig === portsSig) return;
  portsSig = sig;
  const host = $("ports");
  host.textContent = "";
  rateCells = new Map();
  if (known && !ports.length) {
    const none = document.createElement("span");
    none.className = "none";
    none.textContent = "no ports attached";
    host.appendChild(none);
  }
  for (const pt of ports) {
    const chip = document.createElement("div");
    chip.className = "chip" + (pt.connected ? "" : " disc");
    // Alias and the short port name it landed on (/dev/ttyACM0, COM7): a by-id path is 70+
    // characters and pushed the header buttons onto a second line. The description, the
    // requested device string when it differs, and the baud ride along as the hover: a CSS
    // tooltip (.chip[data-tip]::after), because the browser wraps a native title at its own
    // width and broke the by-id path mid-name.
    // A disconnected port's reason rides along too: the chip's grey dot says it is down,
    // not why (no_device = power or cable, open_failed = busy or permissions).
    const port = pt.resolved_device || "";
    chip.dataset.tip = (port
      ? [pt.description, pt.device !== port ? pt.device : null]
      : [`waiting for ${pt.device}`])
      .concat(`@${pt.baud}`, pt.connected ? null
        : (DISCONNECT_WHY[pt.disconnect_reason] || pt.disconnect_reason))
      // What the board itself says it is (OK monitor), which is the half of the identity the
      // alias cannot tell you: the alias stays put when the probe is moved to another board.
      // Silent when it has not answered - most ports never do, and the chip's missing target
      // span already says it.
      .concat(pt.target ? `monitor reports: ${pt.target}` : null)
      .filter(Boolean).join("\n");

    // The dot is the connect switch: green -> click to close the port and stop retrying
    // (held, red); red -> click to reconnect. Grey is the daemon's own loss of the device.
    const dot = document.createElement("button");
    // A port that receives but cannot send is critical too: the CLI calls this DEGRADED.
    dot.className = "dot" + (pt.held ? " crit"
      : pt.connected ? (writeErrors || writerDead || pt.write_failures ? " crit" : "") : " off");
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

    // The board behind the port, from `OK monitor`. The alias follows the cable, not the
    // board, so a probe moved to the other bench board otherwise keeps reading as the old one.
    // Not repeated when it is the alias itself (the demo's "sim"); the hover still says it.
    if (pt.target && pt.target !== pt.alias) {
      const tg = document.createElement("span");
      tg.className = "meta target";
      tg.textContent = pt.target;
      chip.appendChild(tg);
    }

    // Filled by renderPortRates on every poll, not here: the rate moves constantly and the
    // chips are only rebuilt when something in portsSig does.
    const rate = document.createElement("span");
    rate.className = "meta rate";
    rate.title = `Lines received on ${pt.alias} since the previous status poll`;
    rateCells.set(pt.alias, rate);
    chip.appendChild(rate);

    // Lines shed because storage could not keep up: the capture has holes, so say so
    // rather than leaving the gap to be discovered by reading the log.
    if (pt.rx_dropped) {
      const drop = document.createElement("span");
      drop.className = "meta drop";
      drop.textContent = `${pt.rx_dropped} dropped`;
      drop.title = "Lines lost because capture could not keep up with the port";
      chip.appendChild(drop);
    }

    // Writes the port refused: it receives but cannot send, which is per port and shows
    // nowhere else (mcu status calls it DEGRADED).
    if (pt.write_failures) {
      const wf = document.createElement("span");
      wf.className = "meta drop";
      wf.textContent = `${pt.write_failures} write fail` + (pt.write_failures === 1 ? "" : "s");
      wf.title = pt.last_write_error
        || "Consecutive failed writes to this port; it receives but cannot send";
      chip.appendChild(wf);
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
// stalled poll is always gone before its successor is due (STATUS_TIMEOUT_MS, state.js).
let statusInFlight = null;
let renderFaultLogged = false;

// `fresh`: the caller just changed daemon state (attach, detach, a session), and a poll
// already in flight may have read /status before that change, so it waits for one started
// after the in-flight poll ends; fresh callers meanwhile all share that one.
function refreshStatus(fresh = false) {
  if (statusInFlight && fresh) {
    const next = () => refreshStatus();
    return statusInFlight.then(next, next);
  }
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
    setCmdOffline(true);
    renderSession(null);
    // The port chips and the db size are health surfaces too: with no answer from the daemon
    // there is no port health to report, and holding the last good reading left a green
    // "connected" chip and a stale size beside a "daemon unreachable" version string.
    renderPorts([], 0, false, false);
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
    renderPortRates(s.ports || []);
    // Null-prototyped, not plain: an alias is wire data, and `constructor` / `toString` are
    // legal aliases (config.ALIAS_RE) that a plain object answers from Object.prototype.
    const aliasMap = (field) => Object.assign(Object.create(null),
      Object.fromEntries((s.ports || []).map((p) => [p.alias, p[field]])));
    state.portEol = aliasMap("eol");
    state.portTarget = aliasMap("target");
    // The command bar resolves "auto" the way PortManager.resolve() does, which needs to know
    // which of several attached ports is connected.
    state.portConnected = Object.assign(Object.create(null),
      Object.fromEntries((s.ports || []).map((p) => [p.alias, !!p.connected])));
    setKnownPorts((s.ports || []).map((p) => p.alias));
    noteDaemonNow(s.now);
    setCmdOffline(false);
    syncCmdEol();
    syncCmdMode();
    setDaemonOnline(true);
  } catch (e) {
    if (!renderFaultLogged) { renderFaultLogged = true; console.error("status render failed:", e); }
  }
}

async function reconnectPort(alias) {
  const g = failGen;
  try {
    await api("POST", "/ports/" + encodeURIComponent(alias) + "/reconnect");
    clearFailureSince(g);
  } catch (e) {
    flashDaemonError("reconnect " + alias + " failed: " + e.message);
  }
  refreshStatus(true);   // after an action: not a poll that predates it
}

async function holdPort(alias) {
  const g = failGen;
  try {
    await api("POST", "/ports/" + encodeURIComponent(alias) + "/disconnect");
    clearFailureSince(g);
  } catch (e) {
    flashDaemonError("disconnect " + alias + " failed: " + e.message);
  }
  refreshStatus(true);   // after an action: not a poll that predates it
}

async function detachPort(alias) {
  const g = failGen;
  try {
    await api("DELETE", "/ports/" + encodeURIComponent(alias));
    clearFailureSince(g);
  } catch (e) {
    flashDaemonError("detach " + alias + " failed: " + e.message);
  }
  refreshStatus(true);   // after an action: not a poll that predates it
}

// ---- attach dialog -----------------------------------------------------------------

const dlg = $("attachDlg");

let attachGen = 0;   // per dialog open: an attach sent from an earlier opening writes nothing here
async function openAttach() {
  attachGen++;
  $("dlgErr").textContent = "";
  $("aliasInput").value = "";
  $("attachSerial").value = "";
  $("attachEol").value = DEFAULT_EOL;
  $("saveToConfig").checked = false;
  $("bindById").checked = false;
  aliasTyped = false;
  syncBaudCustom();
  // Open from the click and fill when /devices answers: opened after the await (up to
  // STATUS_TIMEOUT_MS), the dialog took focus from wherever the user had gone meanwhile.
  if (!dlg.hasAttribute("open")) showDlg(dlg);
  devicesLoading = true;
  $("dlgAttach").disabled = true;
  if (!(await populateDevices())) return;   // a later click's fill owns the dialog
  devicesLoading = false;
  $("dlgAttach").disabled = false;
}

function closeAttach() {
  devicesGen++;   // a fill still loading must not write into a closed dialog
  devicesLoading = false;
  $("dlgAttach").disabled = false;
  closeDlg(dlg);
}

// The alias defaults as the CLI's does (cli.py _derive_alias): "board" for a URL, else the
// device path's last component with anything outside the alias grammar replaced by "-".
// Kept in step with that function clause by clause.
export function deriveAlias(device) {
  const dev = String(device || "");
  if (dev.includes("://")) return "board";
  const base = dev.replace(/\\/g, "/").replace(/\/+$/, "").split("/").pop() || "board";
  return base.replace(/[^A-Za-z0-9_.-]/gu, "-").slice(0, 32).replace(/^[_.-]+/, "") || "board";
}

// Prefilled until the user types an alias of their own; clearing it resumes the prefill.
let aliasTyped = false;
function syncAlias() {
  if (aliasTyped) return;
  const v = $("devSel").value;
  $("aliasInput").value = deriveAlias(v === "custom" ? $("devCustom").value.trim() : v);
}

let devices = [];   // GET /devices as of the last dialog open; the bind box reads by_id from it
let devicesGen = 0;
let devicesLoading = false;   // Attach is held until the device list has landed

// Returns false when a newer fill started while this one waited, and writes nothing then.
// The deadline lets a daemon that accepts and never answers still open the dialog.
async function populateDevices() {
  const sel = $("devSel");
  const gen = ++devicesGen;
  sel.textContent = "";
  const wait = document.createElement("option");
  wait.textContent = "loading devices...";
  sel.appendChild(wait);
  let found = [], err = "";
  try {
    const body = await api("GET", "/devices", undefined, AbortSignal.timeout(STATUS_TIMEOUT_MS));
    found = body.devices || [];
  } catch (e) {
    err = "could not list devices: " + (e.name === "TimeoutError" ? "no reply from daemon" : e.message);
  }
  if (gen !== devicesGen) return false;
  sel.textContent = "";
  devices = found;
  if (err) $("dlgErr").textContent = err;
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
  return true;
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
  syncAlias();
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
  if ($("dlgAttach").disabled || devicesLoading) return;   // one attach in flight, or no list yet
  const device = chosenDevice();
  const baud = chosenBaud();
  const alias = $("aliasInput").value.trim();
  if (!alias) { $("dlgErr").textContent = "Alias is required"; return; }
  if (!device) { $("dlgErr").textContent = "Device is required"; return; }
  // Both bounds, mirroring PortAttach.baud (gt=0, le=MAX_BAUD).
  if (!Number.isFinite(baud) || baud <= 0 || baud > MAX_BAUD) {
    $("dlgErr").textContent = `Baud must be 1-${MAX_BAUD}`; return;
  }
  // eol always (PortAttach defaults it to lf, so a CRLF board attached here otherwise lands
  // on lf with Settings the only way back); serial_number only when filled, since "" is not
  // "no serial number" to the daemon.
  const eol = $("attachEol").value;
  const serialNumber = $("attachSerial").value.trim();
  const body = { alias, device, baud, eol };
  if (serialNumber) body.serial_number = serialNumber;
  // Read now: a dialog reopened while the attach is out resets the box.
  const saveToConfig = $("saveToConfig").checked;
  const gen = attachGen;
  const btn = $("dlgAttach");
  btn.disabled = true;
  try {
    await api("POST", "/ports", body);
    // best-effort, see settings.js; the same values the attach used, or the saved port comes
    // back on the next daemon start with a different eol from the one just chosen.
    if (saveToConfig) saveAttachedPortToConfig(alias, device, baud, eol, serialNumber);
    if (gen === attachGen) closeAttach();
    refreshStatus(true);   // after an action: not a poll that predates it
  } catch (e) {
    if (gen === attachGen) $("dlgErr").textContent = e.message;
  } finally {
    // Only for the opening that sent it: a dialog reopened meanwhile is holding the button
    // until its own device list lands, and unholding it here would leave a live-looking
    // Attach whose click submitAttach refuses in silence.
    if (gen === attachGen) btn.disabled = devicesLoading;
  }
}

export function initStatusbar() {
$("updateDismiss").addEventListener("click", () => {
  if (updateInfo) dismissUpdate(updateInfo.latest);
});
$("sessionBtn").addEventListener("click", toggleSession);
$("actionErrDismiss").addEventListener("click", () => setActionError(""));
fillEolOptions($("attachEol"));
$("attachBtn").addEventListener("click", openAttach);
$("dlgCancel").addEventListener("click", closeAttach);
$("dlgClose").addEventListener("click", closeAttach);
$("dlgAttach").addEventListener("click", submitAttach);
$("devSel").addEventListener("change", syncDevCustom);
$("devCustom").addEventListener("input", syncAlias);
$("aliasInput").addEventListener("input", () => { aliasTyped = $("aliasInput").value.trim() !== ""; });
$("baudSel").addEventListener("change", syncBaudCustom);
dlg.addEventListener("cancel", (e) => { e.preventDefault(); closeAttach(); });
enterSubmits(dlg, () => $("dlgAttach"));
$("sesStart").addEventListener("click", startSession);
$("sesCancel").addEventListener("click", () => closeDlg(sesDlg));
$("sesClose").addEventListener("click", () => closeDlg(sesDlg));
sesDlg.addEventListener("cancel", (e) => { e.preventDefault(); closeDlg(sesDlg); });
enterSubmits(sesDlg, () => $("sesStart"));
}

export { refreshStatus, flashDaemonError, STATUS_TIMEOUT_MS };
