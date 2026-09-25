import { $, api, state, downloadPath, STATUS_TIMEOUT_MS, userText } from "./state.js";
import { loadRange, saveRange, reset, inverted, params } from "./exportrange.js";
import { enterSubmits } from "./chrome.js";

// ---- the one export dialog, shared by every panel ------------------------------------
//
// The range half is identical everywhere and is remembered across panels (exportrange.js);
// the panel-specific half is a small field list the caller passes in, so a new panel adds an
// options array and a build() rather than another dialog. The pure parts live in
// exportrange.js, which is where the range rules are tested.

const dlg = $("exportDlg");

let range = loadRange();
// The mode this dialog is showing and will export with. It differs from range.mode only
// where the panel cannot offer the remembered one (applyShownAvailability), and the
// difference is not persisted: the remembered choice belongs to the user, not to the panel.
let renderMode = range.mode;
let ctx = null;          // the call in progress: {kind, watermark, shown, options, build}
let values = {};         // current option values, by field name
let fields = new Map();  // field name -> input element
let derived = new Set(); // fields still showing a default derived from the others (not edited)
let sessionsReady = Promise.resolve();   // the open dialog's /sessions fill, awaited by Export
// Bumped by every close: a pending Export acts only while its dialog is still open, so a Cancel
// ends it and it never runs against the next panel's dialog.
let dialogGen = 0;
// The capture the open dialog's bounds (the panel's watermark and window, the session list)
// were read from; a reset since leaves them naming lines and runs that no longer exist.
let openCapture = 0;

// The heading names what the panel exports, so a wrong `export` click shows before the download.
const TITLES = { lines: "Export terminal lines", plot: "Export plot data", can: "Export CAN frames" };

// A datetime-local value ("2026-09-08T14:03:00") is local time in both directions.
function toEpoch(v) {
  const ms = Date.parse(v);
  return Number.isNaN(ms) ? null : ms / 1000;
}

function toLocalInput(ts) {
  if (ts == null) return "";
  const d = new Date(ts * 1000), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T` +
         `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// A mode change repaints only: render() also rewrites the clock fields from the saved range.
function setMode(mode) { range.mode = renderMode = mode; paint(); }

// Options are declared by the caller: {name, type: select|check|text, label, choices, value,
// placeholder, enabledBy}. `enabledBy` names a checkbox field this one follows, or is
// {field, equals} to follow a select's value. A choice is a value or a [value, text] pair.
// A function `value(values)` is a default derived from the fields above it: it follows their
// changes until the user edits this field.
function buildOptions() {
  const host = $("expOptions");
  host.textContent = "";
  fields = new Map();
  values = {};
  derived = new Set();
  for (const f of ctx.options || []) {
    if (typeof f.value === "function") derived.add(f.name);
    values[f.name] = derived.has(f.name) ? f.value(values) : f.value;
    const row = document.createElement("div");
    row.className = "field";
    let input;
    if (f.type === "check") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!values[f.name];
      const label = document.createElement("label");
      label.append(input, document.createTextNode(" " + f.label));
      row.appendChild(label);
      row.className = "field checkbox-field";
      input.addEventListener("change", () => { values[f.name] = !!input.checked; optionChanged(f.name); });
    } else {
      const label = document.createElement("label");
      label.textContent = f.label;
      if (f.type === "select") {
        input = document.createElement("select");
        for (const c of f.choices) {
          const [v, text] = Array.isArray(c) ? c : [c, c];
          const o = document.createElement("option");
          o.value = v; o.textContent = text;
          if (v === values[f.name]) o.selected = true;
          input.appendChild(o);
        }
      } else {
        input = document.createElement("input");
        input.className = "mini";
        if (f.placeholder) input.placeholder = f.placeholder;
      }
      input.value = values[f.name] == null ? "" : String(values[f.name]);
      input.addEventListener("change", () => { values[f.name] = input.value; optionChanged(f.name); });
      input.addEventListener("input", () => { values[f.name] = input.value; derived.delete(f.name); });
      row.append(label, input);
    }
    input.id = "expOpt_" + f.name;
    if (f.type !== "check") row.children[0].htmlFor = input.id;   // a checkbox sits inside its label
    fields.set(f.name, input);
    host.appendChild(row);
  }
}

function render() {
  $("expFrom").value = toLocalInput(range.fromTs);
  $("expTo").value = toLocalInput(range.toTs);
  paint();
}

function paint() {
  $("expModeSession").checked = renderMode === "session";
  $("expModeClock").checked = renderMode === "clock";
  $("expModeShown").checked = renderMode === "shown";
  $("expSession").disabled = renderMode !== "session";
  $("expFrom").disabled = $("expTo").disabled = renderMode !== "clock";
  gateOptions();
}

// An edited field keeps its own value from then on; the derived ones follow the edit.
function optionChanged(name) {
  derived.delete(name);
  for (const f of ctx.options || []) {
    if (!derived.has(f.name)) continue;
    values[f.name] = f.value(values);
    fields.get(f.name).value = values[f.name] == null ? "" : String(values[f.name]);
  }
  gateOptions();
}

// Option changes re-gate only: render() rewrites the clock fields from the saved range, which
// would wipe bounds typed but not yet exported.
function gateOptions() {
  for (const f of ctx.options || []) {
    if (!f.enabledBy) continue;
    const by = f.enabledBy;
    fields.get(f.name).disabled = typeof by === "string" ? !values[by] : values[by.field] !== by.equals;
  }
}

// The shown-window choice needs both a freeze (so the surface has an export bound) and a
// span to export; a panel that shows neither cannot offer it.
//
// A panel that cannot offer it does NOT forget the choice: `renderMode` is what this dialog
// shows and what it exports, while `range.mode` stays the remembered one. Rewriting the
// remembered mode here meant one export from the CAN table, or from any live panel, lost a
// `shown` choice made on a paused one - and saveRange then persisted the loss.
function applyShownAvailability() {
  const shown = $("expModeShown");
  const ok = ctx.watermark != null && ctx.shown != null;
  shown.disabled = !ok;
  shown.title = ok ? "" : ctx.watermark == null ? "pause the panel to export exactly what it shows"
    : "the shown window holds nothing to export";
  renderMode = !ok && range.mode === "shown" ? "session" : range.mode;
}

// 200, not 50: a session older than the list's end cannot be reached from this dialog at all,
// and the daemon opens one automatically per run, so 50 is a few days of restarts.
const SESSION_LIMIT = 200;
let fillGen = 0;   // only the newest of overlapping fills (open, reset range) writes the select

async function fillSessions() {
  const sel = $("expSession");
  const gen = ++fillGen;
  sel.textContent = "";
  let sessions = [], err = "";
  try {
    // A deadline, so a stalled daemon leaves Export usable over the whole capture.
    sessions = (await api("GET", `/sessions?limit=${SESSION_LIMIT}`, undefined,
                          AbortSignal.timeout(STATUS_TIMEOUT_MS))).sessions || [];
  } catch (e) {
    err = "could not list sessions: " + (e.name === "TimeoutError" ? "no reply from daemon" : e.message);
  }
  if (gen !== fillGen) return;
  if (err) $("expErr").textContent = err;
  if (!sessions.length) {
    const o = document.createElement("option");
    o.value = ""; o.textContent = "whole capture";
    sel.appendChild(o);
    sel.value = "";
    return;
  }
  const open = sessions.find((s) => s.ended_ts === null);
  for (const s of sessions) {
    const o = document.createElement("option");
    o.value = String(s.id);
    o.textContent = `${userText(s.name)} (${s.lines} lines)` + (s.ended_ts === null ? " (open)" : "");
    sel.appendChild(o);
  }
  const have = sessions.some((s) => String(s.id) === String(range.session));
  // A remembered session that is gone (deleted, or older than the list) is a silent change of
  // what Export covers, so say it before falling back rather than exporting a different run.
  if (!have && range.session != null) {
    $("expErr").textContent =
      `session ${range.session} is no longer in the list; the range moved to the newest run`;
  }
  const want = have ? String(range.session) : String((open || sessions[0]).id);
  sel.value = want;
  range.session = want;
}

// /plot/export's decode options and the URL they build, shared by the charts and the lanes so
// the parameter grammar has one client-side builder (REVIEW.md class 54).
export function plotDecodeOptions() {
  return [
    { name: "decode", type: "check", label: "decode values (enum labels, bit lanes)", value: true },
    { name: "changes", type: "check", label: "changes only", value: false, enabledBy: "decode" },
    { name: "deadband", type: "text", label: "Deadband", value: "",
      placeholder: "channel=0.5,other=2", enabledBy: "changes" },
  ];
}

// `v` is the dialog's option values: format plus plotDecodeOptions.
export function plotExportPath(p, v, names, port) {
  p.set("names", names.join(","));
  // Names are unique only within a port (SPEC 9.2); "-" is a sample with no port.
  if (port !== "-") p.set("port", port);
  p.set("format", v.format);
  if (v.decode) p.set("decode", "1");
  if (v.changes) {
    p.set("changes", "1");
    p.set("decode", "1");   // changes=1 without decode=1 is a 400, not an export (SPEC 9.2)
    if (v.deadband.trim()) p.set("deadband", v.deadband.trim());
  }
  return "/plot/export?" + p.toString();
}

// `build(rangeParams, optionValues)` returns the path to download, or null when the caller
// did the download itself (the CAN table snapshot is built client-side, not by the daemon).
export function openExportDialog(opts) {
  ctx = opts;
  openCapture = state.captureGen;
  $("expGo").disabled = false;   // an Export still pending from a closed dialog holds nothing here
  range = loadRange();
  $("expTitle").textContent = TITLES[ctx.kind] || "Export";
  $("expErr").textContent = "";
  buildOptions();
  applyShownAvailability();
  render();
  // The dialog opens now and the session list fills when the daemon answers; doExport awaits
  // this, so an Export pressed in between still carries the remembered session rather than
  // silently exporting the open run. Awaiting it here instead would mean no dialog at all
  // while a daemon that accepted the connection is still thinking about it.
  sessionsReady = fillSessions();
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
  // The range choice in force, rather than the close x that showModal would pick.
  $({ session: "expModeSession", clock: "expModeClock", shown: "expModeShown" }[renderMode]).focus();
}

function closeExport() {
  dialogGen++;
  if (typeof dlg.close === "function") dlg.close();
  else dlg.removeAttribute("open");
}

// Held busy until the download is away, so a held Enter cannot start a second one.
async function doExport() {
  const btn = $("expGo");
  if (btn.disabled) return;
  btn.disabled = true;
  const gen = dialogGen, mine = ctx;
  // Not once another panel's dialog has opened: the button is that dialog's now.
  try { await exportNow(gen); } finally { if (ctx === mine) btn.disabled = false; }
}

async function exportNow(gen) {
  // The newest fill: one superseded by `reset range` while awaited leaves the select empty.
  for (let ready = null; ready !== sessionsReady;) { ready = sessionsReady; await ready; }
  if (gen !== dialogGen) return;
  if (openCapture !== state.captureGen) {
    $("expErr").textContent = "the capture was reset while this dialog was open; close it and export again";
    return;
  }
  if (renderMode === "session") range.session = $("expSession").value || null;
  if (renderMode === "clock") {
    range.fromTs = toEpoch($("expFrom").value);
    range.toTs = toEpoch($("expTo").value);
  }
  // What this panel can actually export (W10): the remembered mode where it is available,
  // the rendered fallback otherwise. range keeps the remembered one.
  const effective = { ...range, mode: renderMode };
  if (inverted(effective)) {
    $("expErr").textContent = "the end of the range is before its start";
    return;
  }
  // A function when the window depends on an option (the lanes' ids differ per Port choice).
  const shown = typeof ctx.shown === "function" ? ctx.shown(values) : ctx.shown;
  const p = params(effective, { watermark: ctx.watermark, shown });
  const path = ctx.build(p, values);
  saveRange(range);           // remembered on Export only, so Cancel leaves the last one alone
  if (!path) { closeExport(); return; }   // the caller downloaded it itself (CAN snapshot)
  const fmt = values.format || "csv";
  const ext = { text: "txt", jsonl: "jsonl" }[fmt] || "csv";
  // Closed only once the download is away. A refusal belongs beside the range and options
  // that produced it, not in a toast over a dialog that has already gone.
  const err = await downloadPath(path, `${ctx.kind}.${ext}`, `${ctx.kind} export`,
                                 () => gen === dialogGen);
  if (gen !== dialogGen) return;
  if (err) { $("expErr").textContent = err; return; }
  closeExport();
}

export function initExportDialog() {
  $("expClose").addEventListener("click", closeExport);
  $("expCancel").addEventListener("click", closeExport);
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); closeExport(); });
  $("expGo").addEventListener("click", doExport);
  enterSubmits(dlg, () => $("expGo"));
  $("expModeSession").addEventListener("change", () => setMode("session"));
  $("expModeClock").addEventListener("change", () => setMode("clock"));
  $("expModeShown").addEventListener("change", () => setMode("shown"));
  $("expReset").addEventListener("click", () => {
    range = reset();
    renderMode = range.mode;
    render();
    sessionsReady = fillSessions();
  });
}
