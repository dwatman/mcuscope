// Settings page (SPEC 9.1 / 3.3.1): edits the saved config file via GET/PUT /config/*.
// A dialog, matching the attach dialog's idiom exactly (same markup pattern, same CSS
// classes). Also owns the persistent "restart daemon to apply" badge in the status bar,
// since restart_required is carried on every /config response.

import { $, api, hooks, getToken, setToken, resetTokenPrompt, downloadPath } from "./state.js";
import { reconnectStream } from "./api.js";
import { fmtBytes } from "./statusbar.js";

let cfg = null;              // last config seen (GET or a save's own refresh)
let devicesCache = [];       // GET /devices, refreshed each time the dialog opens

// ---- restart badge ------------------------------------------------------------------

function setBadge(restart) {
  const b = $("restartBadge");
  if (b) b.hidden = !restart;
}

// Re-fetch the saved config (path/exists/sections/token_set/restart_required) and update
// the badge. Callers that also want the fresh fields re-rendered call the render* helpers
// themselves; this just keeps `cfg` and the badge current.
async function refreshConfig() {
  try {
    cfg = await api("GET", "/config");
    setBadge(cfg.restart_required);
  } catch {
    /* daemon unreachable: keep the last known cfg/badge state rather than clearing it */
  }
  return cfg;
}

async function loadDevices() {
  try {
    const body = await api("GET", "/devices");
    devicesCache = body.devices || [];
  } catch {
    devicesCache = [];
  }
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
  $("cfgTokenErr").textContent = "";
}

// Store (or clear) the token this browser sends, re-arm the 401/1008 prompt budget, and
// reconnect the stream so a previously failed page recovers without a reload.
function applyToken(value) {
  setToken(value);
  resetTokenPrompt();
  reconnectStream();
  renderToken();
  const err = $("cfgTokenErr");
  err.textContent = value ? "saved; reconnecting stream" : "cleared; reconnecting stream";
  setTimeout(() => { if (err.textContent.endsWith("reconnecting stream")) err.textContent = ""; }, 2500);
}

function renderServer() {
  $("cfgHost").value = cfg.server.host;
  $("cfgPort").value = cfg.server.port;
  $("cfgServerErr").textContent = "";
}

// The cap is stored in bytes but edited in MB: nobody wants to type 536870912, and a
// mistyped byte figure is exactly the way to set a cap far lower than intended.
const MB = 1024 * 1024;

function renderStorage() {
  $("cfgDbPath").value = cfg.storage.db_path || "";
  $("cfgRetention").value = cfg.storage.retention_days;
  $("cfgMaxDb").value = cfg.storage.max_db_bytes
    ? Math.max(1, Math.round(cfg.storage.max_db_bytes / MB)) : 0;
  $("cfgMinSessions").value = cfg.storage.min_sessions;
  $("cfgAutoSession").checked = cfg.storage.auto_session !== false;
  $("cfgStorageErr").textContent = "";
  renderDbNow();
}

// Show what the capture currently occupies next to the cap field, so a cap is set against
// a real number instead of a guess.
async function renderDbNow() {
  const el = $("cfgDbNow");
  if (!el) return;
  try {
    const s = await api("GET", "/status");
    const trimmed = s.lines_trimmed
      ? `; ${s.lines_trimmed} oldest lines already trimmed by the cap` : "";
    el.textContent = `capture is currently ${fmtBytes(s.db_size_bytes)}${trimmed}`;
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
  exportBtn.addEventListener("click", () =>
    downloadPath(`/sessions/${sess.id}/export`, `${sess.name}.db`, "session export"));

  const delBtn = document.createElement("button");
  delBtn.type = "button"; delBtn.className = "iconbtn"; delBtn.textContent = "delete";
  delBtn.title = "delete this run's captured lines (not recoverable)";
  delBtn.addEventListener("click", () => deleteSession(sess));

  actTd.append(exportBtn, delBtn);
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

async function renderSessions() {
  const tbody = $("cfgSessionsBody");
  if (!tbody) return;
  tbody.textContent = "";
  $("cfgSessionsErr").textContent = "";
  let sessions = [];
  try {
    sessions = (await api("GET", "/sessions?limit=50")).sessions || [];
  } catch (e) {
    $("cfgSessionsErr").textContent = e.message;
    return;
  }
  if (!sessions.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4; td.className = "dim";
    td.textContent = "no sessions recorded yet (use the record button in the status bar)";
    tr.appendChild(td); tbody.appendChild(tr);
    return;
  }
  for (const sess of sessions) tbody.appendChild(sessionRow(sess));
}

function deviceOptionValue(d) { return d.by_id || d.device; }

// One <select> per ports-table row, same idiom as the attach dialog's device dropdown:
// discovered devices plus a "custom..." free-text fallback so an unplugged/remote device
// can still be entered by path.
function buildDeviceSelect(current) {
  const sel = document.createElement("select");
  sel.className = "mini";
  let matched = false;
  for (const d of devicesCache) {
    const opt = document.createElement("option");
    opt.value = deviceOptionValue(d);
    const desc = d.description || d.vid_pid || "";
    opt.textContent = desc ? `${d.device}  -  ${desc}` : d.device;
    if (opt.value === current) matched = true;
    sel.appendChild(opt);
  }
  const custom = document.createElement("option");
  custom.value = "custom";
  custom.textContent = "custom...";
  sel.appendChild(custom);
  sel.value = matched ? current : "custom";
  return sel;
}

function addPortRow(pc) {
  pc = pc || { alias: "", device: "", serial_number: "", baud: 115200, autoconnect: false };
  const tr = document.createElement("tr");

  const aliasTd = document.createElement("td");
  const aliasInput = document.createElement("input");
  aliasInput.className = "mini"; aliasInput.value = pc.alias || ""; aliasInput.placeholder = "board";
  aliasTd.appendChild(aliasInput);

  const devTd = document.createElement("td");
  const devSel = buildDeviceSelect(pc.device || "");
  const devCustom = document.createElement("input");
  devCustom.className = "mini";
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
  snTd.appendChild(snInput);

  const baudTd = document.createElement("td");
  const baudInput = document.createElement("input");
  baudInput.className = "mini"; baudInput.type = "number"; baudInput.min = "1";
  baudInput.value = pc.baud || 115200;
  baudTd.appendChild(baudInput);

  const autoTd = document.createElement("td");
  const autoInput = document.createElement("input");
  autoInput.type = "checkbox"; autoInput.checked = !!pc.autoconnect;
  autoInput.setAttribute("aria-label", "autoconnect");
  autoTd.appendChild(autoInput);

  const rmTd = document.createElement("td");
  const rmBtn = document.createElement("button");
  rmBtn.type = "button"; rmBtn.className = "iconbtn"; rmBtn.textContent = "remove";
  rmBtn.addEventListener("click", () => tr.remove());
  rmTd.appendChild(rmBtn);

  tr.append(aliasTd, devTd, snTd, baudTd, autoTd, rmTd);
  tr._fields = { aliasInput, devSel, devCustom, snInput, baudInput, autoInput };
  $("cfgPortsBody").appendChild(tr);
  return tr;
}

function renderPortsTable() {
  const tbody = $("cfgPortsBody");
  tbody.textContent = "";
  for (const pc of cfg.ports || []) addPortRow(pc);
  $("cfgPortsErr").textContent = "";
}

function rowDeviceValue(tr) {
  const f = tr._fields;
  return f.devSel.value === "custom" ? f.devCustom.value.trim() : f.devSel.value;
}

// Rows with no alias are dropped silently (an empty "+ port" row left untouched); everything
// else is sent as typed and the daemon applies the same validation the config loader does.
function collectPorts() {
  const rows = Array.from($("cfgPortsBody").querySelectorAll("tr"));
  const ports = [];
  for (const tr of rows) {
    const f = tr._fields;
    const alias = f.aliasInput.value.trim();
    if (!alias) continue;
    const entry = { alias, autoconnect: f.autoInput.checked };
    const device = rowDeviceValue(tr);
    if (device) entry.device = device;
    const serial_number = f.snInput.value.trim();
    if (serial_number) entry.serial_number = serial_number;
    const baud = parseInt(f.baudInput.value, 10);
    if (Number.isFinite(baud) && baud > 0) entry.baud = baud;
    ports.push(entry);
  }
  return ports;
}

// ---- save handlers ---------------------------------------------------------------------

async function saveServer() {
  const btn = $("cfgServerSave"); const err = $("cfgServerErr");
  err.textContent = "";
  const host = $("cfgHost").value.trim();
  const port = parseInt($("cfgPort").value, 10);
  if (!host) { err.textContent = "host is required"; return; }
  if (!Number.isFinite(port) || port < 1 || port > 65535) { err.textContent = "port must be 1-65535"; return; }
  btn.disabled = true;
  try {
    await api("PUT", "/config/server", { host, port });
    await refreshConfig();
    renderServer();
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
  const retention_days = parseInt($("cfgRetention").value, 10);
  if (!Number.isFinite(retention_days) || retention_days < 1 || retention_days > 3650) {
    err.textContent = "retention must be 1-3650 days"; return;
  }
  const capMb = parseInt($("cfgMaxDb").value, 10);
  if (!Number.isFinite(capMb) || capMb < 0) { err.textContent = "size cap must be 0 or more MB"; return; }
  const max_db_bytes = capMb * MB;
  const min_sessions = parseInt($("cfgMinSessions").value, 10);
  if (!Number.isFinite(min_sessions) || min_sessions < 0 || min_sessions > 1000) {
    err.textContent = "sessions to keep must be 0-1000"; return;
  }
  btn.disabled = true;
  try {
    await api("PUT", "/config/storage", {
      db_path, retention_days, max_db_bytes, min_sessions,
      auto_session: $("cfgAutoSession").checked,
    });
    await refreshConfig();
    renderStorage();
    renderSessions();
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

async function savePorts() {
  const btn = $("cfgPortsSave"); const err = $("cfgPortsErr");
  err.textContent = "";
  const ports = collectPorts();
  btn.disabled = true;
  try {
    await api("PUT", "/config/ports", { ports });
    await refreshConfig();
    renderPortsTable();
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

// ---- dialog open/close (mirrors the attach dialog in statusbar.js) -------------------

const dlg = $("settingsDlg");

async function openSettings() {
  await Promise.all([refreshConfig(), loadDevices()]);
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
  if (!cfg) {
    $("cfgPath").textContent = "could not load config (daemon unreachable)";
    $("cfgAuth").textContent = "";
    renderToken();   // entering a token is most useful exactly when requests are failing
    return;
  }
  renderMeta(); renderToken(); renderServer(); renderStorage(); renderPortsTable();
  renderSessions();
}

function closeSettings() {
  if (typeof dlg.close === "function") dlg.close();
  else dlg.removeAttribute("open");
}

export function initSettings() {
  $("settingsBtn").addEventListener("click", openSettings);
  $("setClose").addEventListener("click", closeSettings);
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); closeSettings(); });
  $("cfgTokenSave").addEventListener("click", () => applyToken($("cfgToken").value.trim() || null));
  $("cfgTokenClear").addEventListener("click", () => applyToken(null));
  $("cfgServerSave").addEventListener("click", saveServer);
  $("cfgStorageSave").addEventListener("click", saveStorage);
  $("cfgPortsSave").addEventListener("click", savePorts);
  $("cfgPortAdd").addEventListener("click", () => addPortRow());
  refreshConfig();   // prime the restart badge before the dialog is ever opened
}

// Called by the attach dialog's "save to config" checkbox after a successful runtime
// attach: merge (replace-by-alias) the newly attached port into the saved ports list and
// write it back. Best-effort: a failure here does not undo the runtime attach, it just
// means the config file was not updated, surfaced via the existing daemon-chip flash
// rather than a dedicated UI (this is a side effect of attach, not the primary action).
export async function saveAttachedPortToConfig(alias, device, baud) {
  try {
    const current = await api("GET", "/config");
    setBadge(current.restart_required);
    const ports = (current.ports || []).filter((p) => p.alias !== alias);
    ports.push({ alias, device, baud, autoconnect: true });
    await api("PUT", "/config/ports", { ports });
    cfg = await api("GET", "/config");
    setBadge(cfg.restart_required);
  } catch (e) {
    hooks.reportError("save to config failed: " + e.message);
  }
}
