// Settings page (SPEC 9.1 / 3.3.1): edits the saved config file via GET/PUT /config/*.
// A dialog, matching the attach dialog's idiom exactly (same markup pattern, same CSS
// classes). Also owns the persistent "restart daemon to apply" badge in the status bar,
// since restart_required is carried on every /config response.

import { $, api, hooks, intField, getToken, setToken, resetTokenPrompt, downloadPath,
         MAX_BAUD, MAX_DB_BYTES, isEol, fillEolOptions, DEFAULT_EOL } from "./state.js";
import { reconnectStream } from "./api.js";
import { fmtBytes, STATUS_TIMEOUT_MS } from "./statusbar.js";
import { enterSubmits } from "./chrome.js";

let cfg = null;              // last config seen (GET or a save's own refresh)
let devicesCache = [];       // GET /devices, refreshed each time the dialog opens

function reportIfFailed(msg) { if (msg) hooks.reportError(msg); }

// ---- restart badge ------------------------------------------------------------------

function setBadge(restart) {
  const b = $("restartBadge");
  if (b) b.hidden = !restart;
}

// Re-fetch the saved config (path/exists/sections/token_set/restart_required) and update
// the badge. Callers that also want the fresh fields re-rendered call the render* helpers
// themselves; this just keeps `cfg` and the badge current. Returns null when this fetch
// failed, even though `cfg` keeps the last known state for the badge.
async function refreshConfig(signal) {
  try {
    cfg = await api("GET", "/config", undefined, signal);
    setBadge(cfg.restart_required);
    return cfg;
  } catch {
    return null;
  }
}

async function loadDevices(signal) {
  try {
    const body = await api("GET", "/devices", undefined, signal);
    devicesCache = body.devices || [];
  } catch {
    devicesCache = [];
  }
}

// ---- unsaved edits ---------------------------------------------------------------------
//
// A section is dirty while its fields differ from what was last rendered into them (a render
// follows every successful save), so an edit typed back to the saved value is clean again.
// PlotJuggler applies as it changes and Sessions has no fields, so neither can hold unsaved
// typing. Read-only (daemon unreachable), only the token can be saved, so only it counts.

const SECTIONS = [
  { sec: "cfgSecServer", save: "cfgServerSave", name: "Server",
    read: () => [$("cfgHost").value, $("cfgPort").value] },
  { sec: "cfgSecStorage", save: "cfgStorageSave", name: "Storage",
    read: () => [$("cfgDbPath").value, $("cfgRetention").value, $("cfgMaxDb").value,
                 $("cfgMinSessions").value, $("cfgAutoSession").checked] },
  { sec: "cfgSecUpdate", save: "cfgUpdateSave", name: "Updates",
    read: () => [$("cfgUpdateCheck").checked] },
  { sec: "cfgSecToken", save: "cfgTokenSave", name: "Access token",
    read: () => [$("cfgToken").value] },
  { sec: "cfgSecPorts", save: "cfgPortsSave", name: "Ports", read: () => portsSnapshot() },
];
const PORTS = SECTIONS[4];
const cleanAs = new Map();   // section id -> its fields as last rendered
let readOnly = false;

function isDirty(s) {
  if (readOnly && s.sec !== "cfgSecToken") return false;
  return cleanAs.has(s.sec) && JSON.stringify(s.read()) !== cleanAs.get(s.sec);
}

function paintDirty(s) {
  const dirty = isDirty(s);
  $(s.sec).classList.toggle("dirty", dirty);
  $(s.save).classList.toggle("primary", dirty);
  $(s.save).textContent = dirty ? "Save *" : "Save";
}

function markClean(s) {
  cleanAs.set(s.sec, JSON.stringify(s.read()));
  paintDirty(s);
}

// The names of the sections holding unsaved edits, in dialog order.
export function dirtySections() {
  return SECTIONS.filter(isDirty).map((s) => s.name);
}

// With the daemon unreachable nothing but the browser-side token can be saved.
const DAEMON_CONTROLS = ["cfgServerSave", "cfgStorageSave", "cfgUpdateSave", "cfgPjSave",
                         "cfgPortsSave", "cfgPortAdd"];
function setReadOnly(on) {
  readOnly = on;
  for (const id of DAEMON_CONTROLS) $(id).disabled = on;
  SECTIONS.forEach(paintDirty);
}

// ---- render --------------------------------------------------------------------------

function renderMeta() {
  $("cfgPath").textContent = cfg.path + (cfg.exists ? "" : "  (not created yet - saving will create it)");
  $("cfgAuth").textContent = cfg.token_set ? "auth: token set" : "auth: token not set";
}

// ---- client access token (browser-side; the daemon's token is set at start) ----------

function renderToken() {
  $("cfgToken").value = getToken() || "";
  $("cfgToken").placeholder = getToken() ? "" : "(none stored)";
  $("cfgTokenNote").textContent = "";
  markClean(SECTIONS[3]);
}

// Store (or clear) the token this browser sends, re-arm the 401/1008 prompt budget, and
// reconnect the stream so a previously failed page recovers without a reload.
function applyToken(value) {
  setToken(value);
  resetTokenPrompt();
  reconnectStream();
  renderToken();
  const note = $("cfgTokenNote");
  note.textContent = value ? "saved; reconnecting stream" : "cleared; reconnecting stream";
  setTimeout(() => { if (note.textContent.endsWith("reconnecting stream")) note.textContent = ""; }, 2500);
}

function renderServer() {
  $("cfgHost").value = cfg.server.host;
  $("cfgPort").value = cfg.server.port;
  $("cfgServerErr").textContent = "";
  markClean(SECTIONS[0]);
}

// The cap is stored in bytes but edited in MB: nobody wants to type 536870912, and a
// mistyped byte figure is exactly the way to set a cap far lower than intended.
const MB = 1024 * 1024;
// The saved cap and the whole MB it was rendered as. A cap that is not a whole MiB (a hand
// edit) is sent back as these bytes while the field still reads `mb`, not re-rounded.
let capShown = { mb: 0, bytes: 0 };

function renderStorage() {
  $("cfgDbPath").value = cfg.storage.db_path || "";
  $("cfgRetention").value = cfg.storage.retention_days;
  const bytes = cfg.storage.max_db_bytes || 0;
  capShown = { mb: bytes ? Math.max(1, Math.round(bytes / MB)) : 0, bytes };
  $("cfgMaxDb").value = capShown.mb;
  $("cfgMinSessions").value = cfg.storage.min_sessions;
  $("cfgAutoSession").checked = cfg.storage.auto_session !== false;
  $("cfgStorageErr").textContent = "";
  markClean(SECTIONS[1]);
  renderDbNow();
}

// The cap field's hint carries what the capture occupies now, so a cap is set against a real
// number instead of a guess; lines the cap has already trimmed are in its title.
const CAP_TITLE = "Past the cap the oldest lines are trimmed";
async function renderDbNow() {
  const el = $("cfgDbNow");
  if (!el) return;
  try {
    const s = await api("GET", "/status");
    renderWarnings(s.config_warnings);
    // Content, not file size: the cap is enforced against db_content_bytes (SPEC 3.4).
    el.textContent = `0 = no cap; now ${fmtBytes(s.db_content_bytes)}`;
    const disk = fmtBytes(s.db_size_bytes);
    el.title = CAP_TITLE + (disk ? `; ${disk} on disk` : "")
      + (s.lines_trimmed ? `; ${s.lines_trimmed} trimmed so far` : "");
  } catch {
    el.textContent = "0 = no cap";
    el.title = CAP_TITLE;
    renderWarnings(null);
  }
}

// What the daemon ignored when it loaded the config file (/status config_warnings, absent
// from an older daemon), one line each; hidden when there is none.
function renderWarnings(list) {
  const box = $("cfgWarnings");
  box.textContent = "";
  const warnings = Array.isArray(list) ? list : [];
  for (const w of warnings) {
    const li = document.createElement("li");
    li.textContent = String(w);
    box.appendChild(li);
  }
  box.hidden = !warnings.length;
}

// ---- update check (SPEC 3.6) ---------------------------------------------------------

// PlotJuggler stream (SPEC 3.7): the checkbox and destination drive the RUNTIME state
// immediately via PUT /plotjuggler; "Save as default" writes the config file too. The
// section renders from GET /plotjuggler, not the saved config, because the live state
// is what the controls change.
async function renderPj() {
  const err = $("cfgPjErr");
  err.textContent = "";
  // The GET lands after the dialog is open: each control takes the answer only while it still
  // reads what it did when the request left.
  const enabled = $("cfgPjEnabled").checked, dest = $("cfgPjDest").value;
  try {
    const st = await api("GET", "/plotjuggler");
    if ($("cfgPjEnabled").checked === enabled) $("cfgPjEnabled").checked = st.enabled;
    if ($("cfgPjDest").value === dest) $("cfgPjDest").value = st.dest;
  } catch (e) {
    err.textContent = e.message;
  }
}

async function applyPj() {
  const err = $("cfgPjErr");
  err.textContent = "";
  try {
    const dest = $("cfgPjDest").value.trim();
    const enabled = $("cfgPjEnabled").checked;
    const st = await api("PUT", "/plotjuggler", { enabled, dest: dest || null });
    // Echo the daemon's answer, so a kept-previous dest (blank field) becomes visible; not
    // over a control changed while the PUT was out (that change sent a PUT of its own).
    if ($("cfgPjEnabled").checked === enabled) $("cfgPjEnabled").checked = st.enabled;
    if ($("cfgPjDest").value.trim() === dest) $("cfgPjDest").value = st.dest;
    return true;
  } catch (e) {
    err.textContent = e.message;
    // A refused change left the daemon on its old state; re-sync the checkbox so it
    // does not show a stream the daemon refused. The dest field keeps the user's
    // typing: reverting it would eat the value they are mid-correcting.
    const shown = $("cfgPjEnabled").checked;
    try {
      const st = await api("GET", "/plotjuggler");
      if ($("cfgPjEnabled").checked === shown) $("cfgPjEnabled").checked = st.enabled;
    } catch { /* daemon unreachable: the inline error already says so */ }
    return false;
  }
}

async function savePjDefault() {
  const btn = $("cfgPjSave"); const err = $("cfgPjErr");
  btn.disabled = true;
  try {
    if (!(await applyPj())) return;   // never save a state the daemon refused to run
    await putConfig("plotjuggler", {
      enabled: $("cfgPjEnabled").checked, dest: $("cfgPjDest").value.trim(),
    });
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

function renderUpdateCheck() {
  const box = $("cfgUpdateCheck");
  if (!box) return;
  box.checked = !cfg.update || cfg.update.check !== false;
  $("cfgUpdateErr").textContent = "";
  markClean(SECTIONS[2]);
  renderUpdateNow();
}

// What the daemon last learned, so the setting is not a black box: a check that is on but
// has never produced a result (env veto, offline, or simply too soon) says so.
async function renderUpdateNow() {
  const el = $("cfgUpdateNow");
  if (!el) return;
  if (!$("cfgUpdateCheck").checked) {
    el.textContent = "checks are off; the daemon makes no outbound request";
    return;
  }
  try {
    const s = await api("GET", "/status");
    if (!s.update) {
      // Either nothing has run yet (the first check is seconds after startup) or the
      // environment veto is in force, which the daemon deliberately does not distinguish;
      // the checkbox hint's title names the veto.
      el.textContent = "not checked yet in this daemon run";
    } else if (s.update.available) {
      el.textContent = `${s.update.latest} is available (running ${s.version})`;
    } else {
      el.textContent = `${s.version} is the newest release (checked ${fmtWhen(s.update.checked_at)})`;
    }
  } catch {
    el.textContent = "";
  }
}

// ---- sessions (archive or delete a recorded run) -------------------------------------

function fmtWhen(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : "";
}

function sessionRow(sess) {
  const tr = document.createElement("tr");
  const running = sess.ended_ts === null;

  const nameTd = document.createElement("td");
  nameTd.textContent = sess.name;
  if (sess.note) nameTd.title = sess.note;
  const tags = [sess.auto ? "auto" : null, running ? "recording" : null].filter(Boolean);
  if (tags.length) {
    const tag = document.createElement("span");
    tag.className = "dim";
    tag.textContent = "  " + tags.join(", ");
    nameTd.appendChild(tag);
  }

  const whenTd = document.createElement("td");
  whenTd.textContent = fmtWhen(sess.started_ts);

  const linesTd = document.createElement("td");
  linesTd.textContent = sess.lines;

  const actTd = document.createElement("td");
  const exportBtn = document.createElement("button");
  exportBtn.type = "button"; exportBtn.className = "iconbtn"; exportBtn.textContent = "export";
  exportBtn.title = "download this run as a standalone capture database";
  // downloadPath returns the failure message rather than reporting it: from here there is no
  // dialog to put it in, so it becomes the same chip flash every other background failure does.
  // The button is held until the download is away, so a double click downloads once.
  const download = (btn, path, name, label) => btn.addEventListener("click", async () => {
    if (btn.disabled) return;
    btn.disabled = true;
    try { reportIfFailed(await downloadPath(path, name, label)); } finally { btn.disabled = false; }
  });
  download(exportBtn, `/sessions/${sess.id}/export`, `${sess.name}.db`, "session export");

  const bundleBtn = document.createElement("button");
  bundleBtn.type = "button"; bundleBtn.className = "iconbtn"; bundleBtn.textContent = "bundle";
  bundleBtn.title = "download this run as a zip: capture db, lines, plot and CAN CSVs";
  download(bundleBtn, `/sessions/${sess.id}/bundle`, "bundle.zip", "bundle export");

  const delBtn = document.createElement("button");
  delBtn.type = "button"; delBtn.className = "iconbtn"; delBtn.textContent = "delete";
  delBtn.title = "delete this run's captured lines (not recoverable)";
  delBtn.addEventListener("click", () => deleteSession(sess));

  actTd.append(exportBtn, bundleBtn, delBtn);
  tr.append(nameTd, whenTd, linesTd, actTd);
  return tr;
}

// Deleting the data is destructive and irreversible, so the confirm names the run and the
// number of lines rather than asking a generic "are you sure?".
async function deleteSession(sess) {
  const err = $("cfgSessionsErr");
  err.textContent = "";
  if (!window.confirm(`Delete "${sess.name}" and its ${sess.lines} captured lines?\n\nThis cannot be undone.`)) return;
  try {
    await api("DELETE", `/sessions/${sess.id}?data=true`);
    await renderSessions();
    renderDbNow();
  } catch (e) {
    err.textContent = e.message;
  }
}

let sessionsGen = 0;   // only the newest of overlapping fills writes the table
async function renderSessions() {
  const tbody = $("cfgSessionsBody");
  if (!tbody) return;
  const gen = ++sessionsGen;
  tbody.textContent = "";
  $("cfgSessionsErr").textContent = "";
  let sessions = [];
  try {
    sessions = (await api("GET", "/sessions?limit=50")).sessions || [];
  } catch (e) {
    if (gen === sessionsGen) $("cfgSessionsErr").textContent = e.message;
    return;
  }
  if (gen !== sessionsGen) return;
  if (!sessions.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4; td.className = "dim";
    td.textContent = "no sessions yet: the session button in the status bar starts one";
    tr.appendChild(td); tbody.appendChild(tr);
    return;
  }
  for (const sess of sessions) tbody.appendChild(sessionRow(sess));
}

// One <select> per ports-table row, same idiom as the attach dialog's device dropdown:
// discovered devices plus a "custom..." free-text fallback so an unplugged/remote device
// can still be entered by path. A device with a by-id path gets a second, "bound" entry
// (the dialog's bind box, as a row): the saved value is whichever the user picked, never
// silently swapped for the other.
function buildDeviceSelect(current) {
  const sel = document.createElement("select");
  sel.className = "mini";
  let matched = false;
  for (const d of devicesCache) {
    const desc = d.description || d.vid_pid || "";
    const label = desc ? `${d.device}  -  ${desc}` : d.device;
    const opt = document.createElement("option");
    opt.value = d.device;
    opt.textContent = label;
    if (opt.value === current) matched = true;
    sel.appendChild(opt);
    if (d.by_id) {
      const bound = document.createElement("option");
      bound.value = d.by_id;
      bound.textContent = label + "  (bound to this device)";
      if (bound.value === current) matched = true;
      sel.appendChild(bound);
    }
  }
  const custom = document.createElement("option");
  custom.value = "custom";
  custom.textContent = "custom...";
  sel.appendChild(custom);
  sel.value = matched ? current : "custom";
  return sel;
}

function addPortRow(pc) {
  pc = pc || { alias: "", device: "", serial_number: "", baud: 115200, autoconnect: false,
               identify: true };
  const tr = document.createElement("tr");

  const aliasTd = document.createElement("td");
  const aliasInput = document.createElement("input");
  aliasInput.className = "mini"; aliasInput.value = pc.alias || ""; aliasInput.placeholder = "board";
  aliasInput.setAttribute("aria-label", "alias");
  aliasTd.appendChild(aliasInput);

  const devTd = document.createElement("td");
  const devSel = buildDeviceSelect(pc.device || "");
  devSel.setAttribute("aria-label", "device");
  const devCustom = document.createElement("input");
  devCustom.className = "mini";
  devCustom.setAttribute("aria-label", "device path");
  devCustom.placeholder = "socket://host:port, /dev/ttyACM0, COM7";
  devCustom.value = devSel.value === "custom" ? (pc.device || "") : "";
  devCustom.style.display = devSel.value === "custom" ? "" : "none";
  devSel.addEventListener("change", () => {
    devCustom.style.display = devSel.value === "custom" ? "" : "none";
  });
  devTd.appendChild(devSel); devTd.appendChild(devCustom);

  const snTd = document.createElement("td");
  const snInput = document.createElement("input");
  snInput.className = "mini"; snInput.value = pc.serial_number || ""; snInput.placeholder = "(optional)";
  snInput.setAttribute("aria-label", "serial number");
  snTd.appendChild(snInput);

  const baudTd = document.createElement("td");
  const baudInput = document.createElement("input");
  baudInput.className = "mini"; baudInput.type = "number"; baudInput.min = "1";
  baudInput.value = pc.baud || 115200;
  baudInput.setAttribute("aria-label", "baud");
  baudTd.appendChild(baudInput);

  // GET /config reports every saved port's eol (a bad hand-written value already reads as the
  // loader's lf), so the row shows it and the save sends it back unchanged unless edited.
  const eolTd = document.createElement("td");
  const eolSel = document.createElement("select");
  eolSel.className = "mini";
  eolSel.setAttribute("aria-label", "line ending");
  fillEolOptions(eolSel);
  eolSel.value = isEol(pc.eol) ? pc.eol : DEFAULT_EOL;
  eolTd.appendChild(eolSel);

  const autoTd = document.createElement("td");
  const autoInput = document.createElement("input");
  autoInput.type = "checkbox"; autoInput.checked = !!pc.autoconnect;
  autoInput.setAttribute("aria-label", "autoconnect");
  autoTd.appendChild(autoInput);

  // Off, the daemon sends nothing on connect: a console that is not a monitor gets no ping.
  const idTd = document.createElement("td");
  const idInput = document.createElement("input");
  idInput.type = "checkbox"; idInput.checked = pc.identify !== false;
  idInput.setAttribute("aria-label", "identify");
  idTd.appendChild(idInput);

  const rmTd = document.createElement("td");
  const rmBtn = document.createElement("button");
  rmBtn.type = "button"; rmBtn.className = "iconbtn"; rmBtn.textContent = "remove";
  // Removing a row saves nothing until Save, which the section's dirty mark then says.
  rmBtn.addEventListener("click", () => { tr.remove(); paintDirty(PORTS); });
  rmTd.appendChild(rmBtn);

  tr.append(aliasTd, devTd, snTd, baudTd, eolTd, autoTd, idTd, rmTd);
  tr._fields = { aliasInput, devSel, devCustom, snInput, baudInput, eolSel, autoInput, idInput };
  $("cfgPortsBody").appendChild(tr);
  return tr;
}

function renderPortsTable() {
  const tbody = $("cfgPortsBody");
  tbody.textContent = "";
  for (const pc of cfg.ports || []) addPortRow(pc);
  $("cfgPortsErr").textContent = "";
  markClean(PORTS);
}

// The rows as typed, minus the blank-alias rows a save drops, so an untouched "+ port" row is
// not an unsaved edit.
function portsSnapshot() {
  return Array.from($("cfgPortsBody").querySelectorAll("tr"))
    .filter((tr) => tr._fields.aliasInput.value.trim())
    .map((tr) => {
      const f = tr._fields;
      return [f.aliasInput.value, f.devSel.value, f.devCustom.value, f.snInput.value,
              f.baudInput.value, f.eolSel.value, f.autoInput.checked, f.idInput.checked];
    });
}

function rowDeviceValue(tr) {
  const f = tr._fields;
  return f.devSel.value === "custom" ? f.devCustom.value.trim() : f.devSel.value;
}

// Rows with no alias are dropped silently (an empty "+ port" row left untouched); everything
// else is sent as typed and the daemon applies the same validation the config loader does.
// Returns null once it has named a refusal in `err`, so the caller saves nothing.
function collectPorts(err) {
  const rows = Array.from($("cfgPortsBody").querySelectorAll("tr"));
  const ports = [];
  for (const tr of rows) {
    const f = tr._fields;
    const alias = f.aliasInput.value.trim();
    if (!alias) continue;
    const entry = { alias, autoconnect: f.autoInput.checked, identify: f.idInput.checked,
                    eol: f.eolSel.value };
    const device = rowDeviceValue(tr);
    if (device) entry.device = device;
    const serial_number = f.snInput.value.trim();
    if (serial_number) entry.serial_number = serial_number;
    // A dropped baud is not "leave it alone": the field is omitted from the PUT and the
    // daemon applies its own default (115200), so clearing the box used to save a silent
    // baud change on a 921600 port. Named like every other numeric field in this dialog.
    // Both bounds, mirroring ConfigPortEntry.baud (gt=0, le=MAX_BAUD): the upper one was
    // omitted, so an over-large baud reached the daemon and came back as a raw 422.
    const baud = intField(f.baudInput.value);
    if (!Number.isFinite(baud) || baud < 1 || baud > MAX_BAUD) {
      err.textContent = `port "${alias}": Baud must be 1-${MAX_BAUD}`;
      return null;
    }
    entry.baud = baud;
    ports.push(entry);
  }
  return ports;
}

// ---- save handlers ---------------------------------------------------------------------

// The config revision the open dialog's fields were loaded at, sent with every save so a file
// changed since (another tab, a hand edit) is refused with 409 rather than overwritten (SPEC
// 3.3.1). Moved on by each save's own answer only: a re-read after a save must not adopt a
// newer file that the other sections were never rendered from. Undefined from an older daemon,
// which JSON.stringify then leaves out of the body.
let revision;

async function putConfig(section, body) {
  try {
    const answer = await api("PUT", `/config/${section}`, { ...body, revision });
    if (answer && answer.revision != null) revision = answer.revision;
    return answer;
  } catch (e) {
    // The fields are left as typed; closing the dialog asks before discarding them.
    if (e.status === 409) e.message += "; reopen Settings to load the current file";
    throw e;
  }
}

// After a PUT the daemon accepted: re-render from the re-read config, or, when the re-read
// fails, keep the fields as typed (they are what was saved) rather than render the stale copy.
// `answer` is the PUT's own body, whose restart_required still raises the badge then.
async function renderSaved(s, render, err, answer) {
  if (await refreshConfig()) { render(); return; }
  if (answer && answer.restart_required) setBadge(true);
  markClean(s);
  err.textContent = "saved; could not re-read the config";
}

async function saveServer() {
  const btn = $("cfgServerSave"); const err = $("cfgServerErr");
  err.textContent = "";
  const host = $("cfgHost").value.trim();
  const port = intField($("cfgPort").value);
  if (!host) { err.textContent = "Bind host is required"; return; }
  if (!Number.isFinite(port) || port < 1 || port > 65535) { err.textContent = "Port must be 1-65535"; return; }
  btn.disabled = true;
  try {
    const answer = await putConfig("server", { host, port });
    await renderSaved(SECTIONS[0], renderServer, err, answer);
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

async function saveStorage() {
  const btn = $("cfgStorageSave"); const err = $("cfgStorageErr");
  err.textContent = "";
  const db_path = $("cfgDbPath").value.trim();
  const retention_days = intField($("cfgRetention").value);
  if (!Number.isFinite(retention_days) || retention_days < 1 || retention_days > 3650) {
    err.textContent = "Retention must be 1-3650 days"; return;
  }
  // Both bounds, like the retention and sessions fields beside it (ConfigStorageBody
  // bounds max_db_bytes at 2**42).
  const capMb = intField($("cfgMaxDb").value);
  const maxCapMb = Math.floor(MAX_DB_BYTES / MB);
  if (!Number.isFinite(capMb) || capMb < 0 || capMb > maxCapMb) {
    err.textContent = `Size cap must be 0-${maxCapMb} MB`; return;
  }
  const max_db_bytes = capMb === capShown.mb ? capShown.bytes : capMb * MB;
  const min_sessions = intField($("cfgMinSessions").value);
  if (!Number.isFinite(min_sessions) || min_sessions < 0 || min_sessions > 1000) {
    err.textContent = "Keep newest sessions must be 0-1000"; return;
  }
  btn.disabled = true;
  try {
    const answer = await putConfig("storage", {
      db_path, retention_days, max_db_bytes, min_sessions,
      auto_session: $("cfgAutoSession").checked,
    });
    capShown = { mb: capMb, bytes: max_db_bytes };
    await renderSaved(SECTIONS[1], renderStorage, err, answer);
    renderSessions();
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

async function saveUpdateCheck() {
  const btn = $("cfgUpdateSave"); const err = $("cfgUpdateErr");
  err.textContent = "";
  btn.disabled = true;
  try {
    const answer = await putConfig("update", { check: $("cfgUpdateCheck").checked });
    await renderSaved(SECTIONS[2], renderUpdateCheck, err, answer);
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

async function savePorts() {
  const btn = $("cfgPortsSave"); const err = $("cfgPortsErr");
  err.textContent = "";
  const ports = collectPorts(err);
  if (ports === null) return;
  btn.disabled = true;
  try {
    const answer = await putConfig("ports", { ports });
    await renderSaved(PORTS, renderPortsTable, err, answer);
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

// ---- dialog open/close (mirrors the attach dialog in statusbar.js) -------------------

const dlg = $("settingsDlg");

let openGen = 0;   // a second click while the first open is loading supersedes it
async function openSettings() {
  const gen = ++openGen;
  // A deadline, so a daemon that accepts and never answers opens the read-only dialog
  // (SPEC 9.1) instead of nothing.
  const signal = AbortSignal.timeout(STATUS_TIMEOUT_MS);
  const [loaded] = await Promise.all([refreshConfig(signal), loadDevices(signal)]);
  if (gen !== openGen) return;
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
  setReadOnly(!loaded);
  revision = loaded ? loaded.revision : undefined;
  if (!loaded) {
    $("cfgPath").textContent = "daemon unreachable: settings are read-only; the access token still works";
    $("cfgAuth").textContent = "";
    renderWarnings(null);
    renderToken();   // entering a token is most useful exactly when requests are failing
    $("cfgToken").focus();
    return;
  }
  renderMeta(); renderToken(); renderServer(); renderStorage(); renderPortsTable();
  renderUpdateCheck(); renderSessions(); renderPj();
}

// Escape and the x both come here, so unsaved edits are never dropped without asking.
function closeSettings() {
  const dirty = dirtySections();
  if (dirty.length && !window.confirm(`Close Settings and discard unsaved changes to ${dirty.join(", ")}?`)) return;
  if (typeof dlg.close === "function") dlg.close();
  else dlg.removeAttribute("open");
}

// Enter saves the section the field is in (PlotJuggler applies on change and has none).
function sectionSave(target) {
  const sec = target.closest ? target.closest(".cfg-sec") : null;
  const s = sec && SECTIONS.find((x) => x.sec === sec.id);
  return s ? $(s.save) : null;
}

export function initSettings() {
  $("settingsBtn").addEventListener("click", openSettings);
  $("setClose").addEventListener("click", closeSettings);
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); closeSettings(); });
  $("cfgTokenSave").addEventListener("click", () => applyToken($("cfgToken").value.trim() || null));
  $("cfgTokenClear").addEventListener("click", () => applyToken(null));
  $("cfgServerSave").addEventListener("click", saveServer);
  $("cfgStorageSave").addEventListener("click", saveStorage);
  $("cfgUpdateSave").addEventListener("click", saveUpdateCheck);
  $("cfgPjEnabled").addEventListener("change", applyPj);
  $("cfgPjDest").addEventListener("change", applyPj);
  $("cfgPjSave").addEventListener("click", savePjDefault);
  $("cfgPortsSave").addEventListener("click", savePorts);
  $("cfgPortAdd").addEventListener("click", () => addPortRow());
  for (const s of SECTIONS) {
    $(s.sec).addEventListener("input", () => paintDirty(s));
    $(s.sec).addEventListener("change", () => paintDirty(s));
  }
  enterSubmits(dlg, sectionSave);
  refreshConfig();   // prime the restart badge before the dialog is ever opened
}

// Called by the attach dialog's "save to config" checkbox after a successful runtime
// attach: merge (replace-by-alias) the newly attached port into the saved ports list and
// write it back. Best-effort: a failure here does not undo the runtime attach, it just
// means the config file was not updated, surfaced via the existing daemon-chip flash
// rather than a dedicated UI (this is a side effect of attach, not the primary action).
export async function saveAttachedPortToConfig(alias, device, baud, eol, serialNumber) {
  try {
    const current = await api("GET", "/config");
    setBadge(current.restart_required);
    const ports = (current.ports || []).filter((p) => p.alias !== alias);
    // The same values the attach itself used: saving a port that was just attached as crlf
    // and having it come back as lf on the next daemon start is the 2026-09-04 defect.
    const entry = { alias, device, baud, autoconnect: true };
    if (eol) entry.eol = eol;
    if (serialNumber) entry.serial_number = serialNumber;
    ports.push(entry);
    await api("PUT", "/config/ports", { ports, revision: current.revision });
  } catch (e) {
    hooks.reportError("save to config failed: " + e.message);
    return;
  }
  // The badge only; a failed re-read is not a failed save.
  await refreshConfig();
}
