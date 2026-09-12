import { $, api, downloadPath } from "./state.js";
import { loadRange, saveRange, reset, inverted, params } from "./exportrange.js";

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
let ctx = null;          // the call in progress: {kind, watermark, shownLastMs, options, build}
let values = {};         // current option values, by field name
let fields = new Map();  // field name -> input element
let sessionsReady = Promise.resolve();   // the open dialog's /sessions fill, awaited by Export

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

function setMode(mode) { range.mode = renderMode = mode; render(); }

// Options are declared by the caller: {name, type: select|check|text, label, choices, value,
// placeholder, enabledBy}. `enabledBy` names a checkbox field this one follows.
function buildOptions() {
  const host = $("expOptions");
  host.textContent = "";
  fields = new Map();
  values = {};
  for (const f of ctx.options || []) {
    values[f.name] = f.value;
    const row = document.createElement("div");
    row.className = "field";
    let input;
    if (f.type === "check") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!f.value;
      const label = document.createElement("label");
      label.append(input, document.createTextNode(" " + f.label));
      row.appendChild(label);
      row.className = "field checkbox-field";
      input.addEventListener("change", () => { values[f.name] = !!input.checked; render(); });
    } else {
      const label = document.createElement("label");
      label.textContent = f.label;
      if (f.type === "select") {
        input = document.createElement("select");
        for (const c of f.choices) {
          const o = document.createElement("option");
          o.value = c; o.textContent = c;
          if (c === f.value) o.selected = true;
          input.appendChild(o);
        }
      } else {
        input = document.createElement("input");
        input.className = "mini";
        if (f.placeholder) input.placeholder = f.placeholder;
      }
      input.value = f.value == null ? "" : String(f.value);
      input.addEventListener("change", () => { values[f.name] = input.value; });
      input.addEventListener("input", () => { values[f.name] = input.value; });
      row.append(label, input);
    }
    input.id = "expOpt_" + f.name;
    fields.set(f.name, input);
    host.appendChild(row);
  }
}

function render() {
  $("expModeSession").checked = renderMode === "session";
  $("expModeClock").checked = renderMode === "clock";
  $("expModeShown").checked = renderMode === "shown";
  $("expSession").disabled = renderMode !== "session";
  $("expFrom").disabled = $("expTo").disabled = renderMode !== "clock";
  $("expFrom").value = toLocalInput(range.fromTs);
  $("expTo").value = toLocalInput(range.toTs);
  for (const f of ctx.options || []) {
    if (!f.enabledBy) continue;
    fields.get(f.name).disabled = !values[f.enabledBy];
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
  const ok = ctx.watermark != null && ctx.shownLastMs != null;
  shown.disabled = !ok;
  shown.title = ok ? "" : "pause the panel to export exactly what it shows";
  renderMode = !ok && range.mode === "shown" ? "session" : range.mode;
}

// 200, not 50: a session older than the list's end cannot be reached from this dialog at all,
// and the daemon opens one automatically per run, so 50 is a few days of restarts.
const SESSION_LIMIT = 200;

async function fillSessions() {
  const sel = $("expSession");
  sel.textContent = "";
  let sessions = [];
  try {
    sessions = (await api("GET", `/sessions?limit=${SESSION_LIMIT}`)).sessions || [];
  } catch { /* offline */ }
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
    o.textContent = `${s.name} (${s.lines} lines)` + (s.ended_ts === null ? " (open)" : "");
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

// `build(rangeParams, optionValues)` returns the path to download, or null when the caller
// did the download itself (the CAN table snapshot is built client-side, not by the daemon).
export function openExportDialog(opts) {
  ctx = opts;
  range = loadRange();
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
}

function closeExport() {
  if (typeof dlg.close === "function") dlg.close();
  else dlg.removeAttribute("open");
}

async function doExport() {
  await sessionsReady;
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
  const p = params(effective, { watermark: ctx.watermark, shownLastMs: ctx.shownLastMs });
  const path = ctx.build(p, values);
  saveRange(range);           // remembered on Export only, so Cancel leaves the last one alone
  if (!path) { closeExport(); return; }   // the caller downloaded it itself (CAN snapshot)
  const fmt = values.format || "csv";
  const ext = { text: "txt", jsonl: "jsonl" }[fmt] || "csv";
  // Closed only once the download is away. A refusal belongs beside the range and options
  // that produced it, not in a toast over a dialog that has already gone.
  const err = await downloadPath(path, `${ctx.kind}.${ext}`, `${ctx.kind} export`);
  if (err) { $("expErr").textContent = err; return; }
  closeExport();
}

export function initExportDialog() {
  $("expClose").addEventListener("click", closeExport);
  $("expCancel").addEventListener("click", closeExport);
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); closeExport(); });
  $("expGo").addEventListener("click", doExport);
  $("expModeSession").addEventListener("change", () => setMode("session"));
  $("expModeClock").addEventListener("change", () => setMode("clock"));
  $("expModeShown").addEventListener("change", () => setMode("shown"));
  $("expWhole").addEventListener("click", () => {
    range = reset();
    renderMode = range.mode;
    render();
    sessionsReady = fillSessions();
  });
}
