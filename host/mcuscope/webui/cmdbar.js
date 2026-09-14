import { $, api, intField, state, getEol, setEol, eolField, getCmdMode, setCmdModeFor,
         MAX_TIMEOUT_MS } from "./state.js";
import { scheduleResizeRedraw } from "./plots.js";
import { setRadios, rovingRadios } from "./chrome.js";

// ---- command bar: cmd/raw send + inline result + marker (SPEC 9.1) ------------------
//
// cmd mode routes through POST /cmd (seq + wait, timeout field) and renders the response
// inline with a distinct ok/err/timeout style; raw mode posts to POST /send. Up/down walk
// a localStorage-persisted history. The command and its response also stream back into the
// terminal panes over /ws, so this strip is just the immediate, focused acknowledgement.

const CMD_HISTORY_MAX = 100;   // cap the in-RAM history (and its localStorage mirror)
const CMD_PLACEHOLDER = "type a command, Enter to send, up/down for history";   // as index.html
let cmdMode = "cmd";        // "cmd" | "raw"; follows the targeted port (syncCmdMode)
let cmdGen = 0;             // bumped per submit/dismiss; only the newest may write the strip
const cmdHistory = [];      // oldest-first; persisted in localStorage
let histIdx = -1;           // -1 = editing a fresh line, else index into cmdHistory
let histDraft = "";         // in-progress line stashed while browsing history

function loadCmdHistory() {
  try {
    const h = JSON.parse(localStorage.getItem("cmdHistory"));
    // Capped on load as well as on save: a value written by an older build (or edited by
    // hand) is not bounded by what this build writes.
    if (Array.isArray(h)) {
      cmdHistory.push(...h.filter((x) => typeof x === "string").slice(-CMD_HISTORY_MAX));
    }
  } catch { /* ignore */ }
}
function saveCmdHistory() {
  try { localStorage.setItem("cmdHistory", JSON.stringify(cmdHistory.slice(-CMD_HISTORY_MAX))); }
  catch { /* private mode */ }
}

// null lets the daemon resolve the sole connected port (SPEC 4); an explicit alias targets it.
function cmdPortValue() {
  const v = $("cmdPort").value;
  return v && v !== "auto" ? v : null;
}

function populateCmdPort() {
  const sel = $("cmdPort");
  if (!sel) return;
  const cur = sel.value || "auto";
  const opts = ["auto", ...state.knownAliases];
  if (cur !== "auto" && !opts.includes(cur)) opts.push(cur);
  sel.textContent = "";
  for (const a of opts) {
    const o = document.createElement("option");
    o.value = a;
    o.textContent = a;
    sel.appendChild(o);
  }
  sel.value = opts.includes(cur) ? cur : "auto";
  // Nothing to send to: say so where the user types, rather than let Enter fail at the daemon.
  // The marker stays enabled, since a marker needs no port.
  const input = $("cmdInput");
  const none = !state.knownAliases.length;
  input.disabled = none;
  input.placeholder = none ? "attach a port to send commands" : CMD_PLACEHOLDER;
  syncCmdEol();
  syncCmdMode();
}

// The alias the bar is aimed at, resolved exactly as the daemon resolves a null port
// (PortManager.resolve, SPEC 4): the pick, else the sole managed port, else the sole
// CONNECTED one among several, else the "auto" pseudo-alias. The connected clause is not a
// nicety: without it the bar seeds its eol and its send mode from "auto" while the daemon
// sends the command to a real port with its own eol and its own OK monitor answer.
function targetAlias() {
  return cmdPortValue() || autoAlias();
}

function autoAlias() {
  if (state.knownAliases.length === 1) return state.knownAliases[0];
  const live = state.knownAliases.filter((a) => state.portConnected[a]);
  return live.length === 1 ? live[0] : "auto";
}

// Follow the targeted port's remembered or default mode (state.js getCmdMode). A status poll
// that first reports `OK monitor` for a never-picked port flips it to cmd here. Also labels the
// auto option with the port it resolves to in brackets, "(sim)", else "(auto)", since this runs
// on every poll; as with the eol select, the brackets mark the default and keep it as narrow as
// the bare aliases. The value stays "auto", so cmdPortValue() still sends no port.
function syncCmdMode() {
  const mode = getCmdMode(targetAlias());
  if (mode !== cmdMode) setCmdMode(mode);
  const auto = [...$("cmdPort").children].find((o) => o.value === "auto");
  const a = autoAlias();
  const label = `(${a})`;
  if (auto && auto.textContent !== label) auto.textContent = label;
}

// The select sits on "port default" until the user picks a line ending: the body then omits
// `eol` and the daemon appends the port's own. That option carries the value it will actually
// produce in brackets ("(CRLF)"), so the default is never a mystery, and picking it again is
// the way back out of an override - the only other escape was clearing localStorage. The
// label stays as short as the explicit options, since a select is as wide as its widest one.
// A pick is explicit from then on and persists (state.js).
export function eolDefaultLabel(portEol) {
  return `(${portEol === "none" ? "none" : portEol.toUpperCase()})`;
}

function syncCmdEol() {
  const sel = $("cmdEol");
  if (!sel) return;
  const portEol = state.portEol[targetAlias()] || "lf";
  const dflt = [...sel.children].find((o) => o.value === "");
  if (dflt) dflt.textContent = eolDefaultLabel(portEol);
  sel.value = getEol();
}

function setCmdMode(mode, remember = false) {
  cmdMode = mode;
  if (remember) setCmdModeFor(targetAlias(), mode);
  setRadios($("modeToggle"), (b) => b.dataset.mode === mode);
  $("timeoutBox").classList.toggle("off", mode === "raw");   // timeout only applies to cmd
  $("prompt").textContent = mode === "raw" ? "$" : ">";
  $("prompt").title = mode === "raw" ? "raw mode: the line is written as typed, no seq, no wait"
    : "cmd mode: sent with a seq, waits for the response";
  $("cmdInput").focus();
}

// The result strip only occupies space while a result is showing: it auto-dismisses a
// few seconds after the final outcome (longer for errors), and a click hides it early.
// The response also lives in the terminal, so nothing is lost when it collapses.
let resultHideTimer = null;
function hideResult() {
  cmdGen++;   // invalidate any in-flight command so a late response can't reopen the strip
  clearTimeout(resultHideTimer);
  resultHideTimer = null;
  setResultHidden(true);
}

// The strip takes height from the workspace row, so the panes' cached height is stale on
// every change; tell the virtualizer, or a blank band is left when the strip goes.
function setResultHidden(hidden) {
  const box = $("cmdResult");
  if (box.hidden === hidden) return;
  box.hidden = hidden;
  scheduleResizeRedraw();
}

function showResult(cls, code, query, detail, latency) {
  const box = $("cmdResult");
  box.className = "cmd-result " + cls;
  setResultHidden(false);
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
  if (cmdHistory[cmdHistory.length - 1] !== text) {
    cmdHistory.push(text);
    if (cmdHistory.length > CMD_HISTORY_MAX) cmdHistory.splice(0, cmdHistory.length - CMD_HISTORY_MAX);
    saveCmdHistory();
  }
  histIdx = -1; histDraft = "";
  input.value = "";
  const port = cmdPortValue();
  const prompt = cmdMode === "raw" ? "$ " : "> ";
  const gen = ++cmdGen;   // supersede any in-flight command; a stale response won't write below
  const report = (cls, code, detail, latency) => {
    if (gen === cmdGen) showResult(cls, code, prompt + text, detail, latency);
  };

  if (cmdMode === "raw") {
    try {
      await api("POST", "/send", { port, line: text, ...eolField() }, AbortSignal.timeout(5000));
      report("ok", "sent", "", null);
    } catch (e) {
      report("err", "error", httpErrText(e), null);
    }
    return;
  }

  let timeout = intField($("cmdTimeout").value);
  // Upper bound as well as lower: the daemon refuses timeout_ms above MAX_TIMEOUT_MS, and the
  // AbortSignal below is armed from this same value, so an over-large entry left the strip on
  // "..." for its whole window after the daemon had already refused.
  if (!Number.isFinite(timeout) || timeout <= 0 || timeout > MAX_TIMEOUT_MS) {
    timeout = 1000;
    $("cmdTimeout").value = "1000";   // make the fallback visible instead of silently ignoring the field
  }
  report("pending", "...", "", null);
  try {
    // The daemon bounds the command with timeout_ms; the AbortSignal bounds the HTTP request
    // itself (daemon dying mid-request), so the strip can never stay on "..." forever.
    const r = await api("POST", "/cmd", { port, cmd: text, timeout_ms: timeout, ...eolField() },
                        AbortSignal.timeout(timeout + 5000));
    if (r.status === "ok") {
      report("ok", "ok", r.data || "", r.latency_ms);
    } else if (r.status === "err") {
      const nm = r.err_name ? `${r.err_name} (${r.err_code})` : `err ${r.err_code}`;
      report("err", "err", r.err_detail ? `${nm}: ${r.err_detail}` : nm, r.latency_ms);
    } else {
      report("wait", "timeout", `no response in ${timeout} ms`, null);
    }
  } catch (e) {
    report("err", "error", httpErrText(e), null);
  }
}

// AbortSignal.timeout raises a DOMException whose .message is browser-speak; translate it.
function httpErrText(e) {
  return e && e.name === "TimeoutError" ? "no reply from daemon" : e.message;
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
    // ...but only in a pane showing mrk, so acknowledge it here as well.
    showResult("ok", "marker", text, null, null);
  } catch (e) {
    showResult("err", "error", "marker: " + text, e.message, null);
  }
}

function initCmdBar() {
  loadCmdHistory();
  populateCmdPort();
  document.querySelectorAll("#modeToggle button").forEach((b) =>
    b.addEventListener("click", () => setCmdMode(b.dataset.mode, true)));
  setRadios($("modeToggle"), (b) => b.dataset.mode === cmdMode);
  rovingRadios($("modeToggle"));
  const input = $("cmdInput");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); submitCmd(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); historyPrev(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); historyNext(); }
  });
  $("cmdEol").addEventListener("change", () => setEol($("cmdEol").value));
  $("cmdPort").addEventListener("change", () => { syncCmdEol(); syncCmdMode(); });
  $("cmdResult").addEventListener("click", hideResult);
  $("markerBtn").addEventListener("click", submitMarker);
  $("markerInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); submitMarker(); }
  });
}

export { populateCmdPort, syncCmdEol, syncCmdMode, setCmdMode, initCmdBar };
