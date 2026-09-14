// exportdlg.js: the one export dialog, driven from the panels that open it.
//
// The stub has no layout, so what is asserted here is the wiring: which range choices a panel
// offers, the URL Export builds from the chosen range plus the panel's own options, and that
// the remembered range moves on Export and not on Cancel.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();

const SESSIONS = [
  { id: 4, name: "run-a", lines: 120, started_ts: 1000, ended_ts: 2000, auto: false },
  { id: 7, name: "run-b", lines: 5, started_ts: 3000, ended_ts: null, auto: false },
];

// The double applies the endpoints' own guards, so a URL the daemon would refuse is caught here.
const seen = installExportDaemon(env, SESSIONS);

const { state, PLOT_CAP, PLOT_SLACK, setToken } = await import(webuiUrl("state.js"));
const { charts, plotIngest, setChartPaused, exportChart } = await import(webuiUrl("plots.js"));
const { canIngest, initCan } = await import(webuiUrl("can.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();
initCan();

const KEY = "mcuscope.exportRange";
const opt = (name) => {
  const host = env.byId("expOptions");
  return [...host.querySelectorAll("input"), ...host.querySelectorAll("select")]
    .find((el) => el.id === "expOpt_" + name);
};

let nextId = 0, nextTs = 1000;
function ingest(raw) {
  const row = { id: ++nextId, ts: (nextTs += 0.01), port: "p1", chan: "event", raw };
  state.maxId = row.id;
  plotIngest(row);
  canIngest(row);
}

// A one-stream chart with two visible channels, live.
function aChart() {
  if (!charts.get("p1|s0")) {
    ingest("!pd 0 a:u2 b:u2");
    for (let i = 0; i < 5; i++) ingest(`!ps 0 ${(0x100 + i).toString(16)} 000${i},00A${i}`);
  }
  return charts.get("p1|s0");
}

async function open(fn) {
  seen.lastUrl = null;
  fn();
  await tick();          // the sessions fetch the dialog fires on open
}

async function pressExport() {
  seen.lastUrl = null;
  env.byId("expGo").emit("click");
  await tick();          // doExport awaits the session fill and the download
}
function query() {
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

test("shown mode is offered only by a paused panel", async () => {
  const chart = aChart();
  setChartPaused(chart, false);
  await open(() => exportChart(chart));
  assert.equal(env.byId("expModeShown").disabled, true,
    "a live panel has no frozen window to export");
  assert.equal(env.byId("expModeShown").title, "pause the panel to export exactly what it shows");

  setChartPaused(chart, true);
  await open(() => exportChart(chart));
  assert.equal(env.byId("expModeShown").disabled, false, "a paused panel can export what it shows");
  assert.equal(env.byId("expModeShown").title, "");
  setChartPaused(chart, false);
});

test("a plot export carries decode, changes and deadband", async () => {
  const chart = aChart();
  await open(() => exportChart(chart));

  assert.equal(opt("format").value, "wide", "a single-stream chart defaults to wide");
  assert.equal(opt("decode").checked, true, "decode is on by default");
  assert.equal(opt("deadband").disabled, true, "deadband is meaningless without changes");

  opt("changes").checked = true;
  opt("changes").emit("change");
  assert.equal(opt("deadband").disabled, false, "changes must enable the deadband field");
  opt("deadband").value = " a=0.5 ";
  opt("deadband").emit("change");

  await pressExport();
  const q = query();
  assert.ok(seen.lastUrl.startsWith("/plot/export?"), `built ${seen.lastUrl}`);
  assert.equal(q.get("names"), "a,b");
  assert.equal(q.get("format"), "wide");
  assert.equal(q.get("decode"), "1");
  assert.equal(q.get("changes"), "1");
  assert.equal(q.get("deadband"), "a=0.5", "the deadband must go out trimmed");
});

test("changes off sends no deadband, even with one typed", async () => {
  const chart = aChart();
  await open(() => exportChart(chart));
  opt("deadband").value = "a=9";
  opt("deadband").emit("change");
  await pressExport();
  const q = query();
  assert.equal(q.has("changes"), false);
  assert.equal(q.has("deadband"), false,
    "deadband without changes is a 400 at the daemon; the dialog must not send it");
});

test("decode off drops the flag rather than sending decode=0", async () => {
  const chart = aChart();
  await open(() => exportChart(chart));
  opt("decode").checked = false;
  opt("decode").emit("change");
  await pressExport();
  assert.equal(query().has("decode"), false);
});

test("inverted clock bounds are refused inline and send nothing", async () => {
  const chart = aChart();
  await open(() => exportChart(chart));
  env.byId("expModeClock").emit("change");
  env.byId("expFrom").value = "2026-09-08T12:00:00";
  env.byId("expTo").value = "2026-09-08T11:00:00";

  seen.lastUrl = null;
  env.byId("expGo").emit("click");
  await tick();
  assert.equal(env.byId("expErr").textContent, "the end of the range is before its start");
  assert.equal(seen.lastUrl, null, "a refused range must not reach the daemon");
  assert.equal(env.byId("exportDlg").getAttribute("open"), "",
    "the dialog stays open so the bounds can be fixed");

  // Fixing the order sends it, as local time converted to epoch seconds.
  env.byId("expTo").value = "2026-09-08T13:00:00";
  await pressExport();
  const q = query();
  const from = Date.parse("2026-09-08T12:00:00") / 1000;
  assert.equal(q.get("since_ts"), String(from));
  assert.equal(q.get("until_ts"), String(from + 3600));
});

test("Cancel does not persist the range, Export does", async () => {
  env.localStorage.removeItem(KEY);
  const chart = aChart();

  await open(() => exportChart(chart));
  env.byId("expModeClock").emit("change");
  env.byId("expFrom").value = "2026-09-08T12:00:00";
  env.byId("expTo").value = "2026-09-08T13:00:00";
  env.byId("expCancel").emit("click");
  assert.equal(env.localStorage.getItem(KEY), null, "Cancel must leave the last range alone");

  await open(() => exportChart(chart));
  env.byId("expModeClock").emit("change");
  env.byId("expFrom").value = "2026-09-08T12:00:00";
  env.byId("expTo").value = "2026-09-08T13:00:00";
  await pressExport();
  const saved = JSON.parse(env.localStorage.getItem(KEY));
  assert.equal(saved.mode, "clock");
  assert.equal(saved.fromTs, Date.parse("2026-09-08T12:00:00") / 1000);

  // And the next panel that opens the dialog starts from it.
  await open(() => exportChart(chart));
  assert.equal(env.byId("expModeClock").checked, true, "the range must be remembered across panels");
  env.byId("expReset").emit("click");
  assert.equal(env.byId("expModeSession").checked, true, "reset range must reset the choice");
  env.byId("expCancel").emit("click");
  env.localStorage.removeItem(KEY);
});

test("the session list labels the open run and preselects it", async () => {
  const chart = aChart();
  await open(() => exportChart(chart));
  const labels = env.byId("expSession").children.map((o) => o.textContent);
  assert.deepEqual(labels, ["run-a (120 lines)", "run-b (5 lines) (open)"]);
  await pressExport();
  assert.equal(query().get("session"), "7", "the open session is the one preselected");
  env.localStorage.removeItem(KEY);
});

test("the CAN section prefills the ids on screen and can export the table snapshot", async () => {
  ingest("!can 100 - 100 DE");
  ingest("!can 100 - 7DF DEAD");
  await open(() => env.byId("canExport").emit("click"));

  assert.equal(opt("ids").value, "100,7DF", "the ids in the table must be prefilled, in table order");
  assert.equal(env.byId("expModeShown").disabled, true, "the CAN table has no frozen window");

  opt("ids").value = "7DF";
  opt("ids").emit("change");
  await pressExport();
  const q = query();
  assert.ok(seen.lastUrl.startsWith("/can/frames?"), `built ${seen.lastUrl}`);
  assert.equal(q.get("format"), "csv");
  assert.equal(q.get("id"), "7DF", "an edited id list must replace the prefill, not add to it");

  // The snapshot is the client-side table, so it downloads a blob and hits no endpoint.
  await open(() => env.byId("canExport").emit("click"));
  opt("format").value = "snapshot";
  opt("format").emit("change");
  const blobs = env.blobs.length;
  await pressExport();
  assert.equal(seen.lastUrl, null, "the table snapshot must not be fetched from the daemon");
  assert.equal(env.blobs.length, blobs + 1, "the snapshot must still download");
  env.localStorage.removeItem(KEY);
});

test("an empty id list exports every id in the range", async () => {
  await open(() => env.byId("canExport").emit("click"));
  opt("ids").value = "   ";
  opt("ids").emit("change");
  await pressExport();
  assert.equal(query().has("id"), false, "blanking the field must widen the export, not send an empty id");
  env.localStorage.removeItem(KEY);
});

test("a chart with everything hidden opens no dialog at all", async () => {
  const chart = aChart();
  const shown = new Map(chart.show);
  for (const n of chart.names) chart.show.set(n, false);
  env.byId("exportDlg").close();
  exportChart(chart);
  assert.equal(env.byId("exportDlg").getAttribute("open"), null,
    "with no channels there is nothing to export and nothing to ask about");
  chart.show = shown;
});

test("the shown window survives a ring trim, and a session range still stops at the freeze", async () => {
  const chart = aChart();
  setChartPaused(chart, true);
  const frozenAt = state.maxId;
  for (let i = 0; i < PLOT_CAP + PLOT_SLACK + 100; i++) {
    ingest(`!ps 0 ${(0x9000 + i).toString(16)} 0001,0002`);
  }

  await open(() => exportChart(chart));
  env.byId("expModeShown").emit("change");
  await pressExport();
  // The chart's own frozen window, as host-time edges (a duration is measured from the id_to row).
  const edge = chart.frozen.xsHost.at(-1);
  assert.equal(query().get("until_ts"), String(edge));
  assert.equal(query().get("since_ts"), String(edge - chart.window - 1e-6));
  assert.equal(query().has("last_ms"), false);
  assert.equal(query().get("id_to"), String(frozenAt));

  await open(() => exportChart(chart));
  env.byId("expModeSession").emit("change");
  await pressExport();
  assert.equal(query().has("last_ms") || query().has("since_ts"), false, "a session range is not a window");
  assert.equal(query().get("id_to"), String(frozenAt),
    "a whole-session export from a paused chart must still stop where the chart does");
  setChartPaused(chart, false);
  env.localStorage.removeItem(KEY);
});

test("a remembered shown range survives a panel that cannot offer it", async () => {
  // A panel that cannot offer `shown` (a live CAN table) falls back for its own export only;
  // the remembered choice stays `shown`.
  const chart = aChart();
  setChartPaused(chart, true);
  await open(() => exportChart(chart));
  env.byId("expModeShown").emit("change");
  await pressExport();
  assert.equal(JSON.parse(env.localStorage.getItem(KEY)).mode, "shown");

  await open(() => env.byId("canExport").emit("click"));
  assert.equal(env.byId("expModeShown").disabled, true, "a live CAN table has no frozen window");
  assert.equal(env.byId("expModeSession").checked, true,
    "the rendered selection falls back to a mode this panel can export");
  await pressExport();
  assert.equal(JSON.parse(env.localStorage.getItem(KEY)).mode, "shown",
    "the remembered mode belongs to the user, not to the panel that could not offer it");
  assert.equal(query().has("last_ms") || query().has("since_ts"), false,
    "and the export itself used the fallback");

  await open(() => exportChart(chart));
  assert.equal(env.byId("expModeShown").checked, true,
    "back on the paused chart, the remembered choice is still selected");
  env.byId("expCancel").emit("click");
  setChartPaused(chart, false);
  env.localStorage.removeItem(KEY);
});

test("a remembered session that is gone says so before falling back", async () => {
  // W11: substituting the open run silently made the next Export cover a different capture.
  env.localStorage.setItem(KEY, JSON.stringify(
    { mode: "session", session: "999", fromTs: null, toTs: null }));
  const chart = aChart();
  await open(() => exportChart(chart));
  assert.match(env.byId("expErr").textContent, /session 999 is no longer in the list/);
  assert.equal(env.byId("expSession").value, "7", "and it falls back to the open run");

  // A session that IS in the list is selected in silence.
  env.localStorage.setItem(KEY, JSON.stringify(
    { mode: "session", session: "4", fromTs: null, toTs: null }));
  await open(() => exportChart(chart));
  assert.equal(env.byId("expErr").textContent, "");
  assert.equal(env.byId("expSession").value, "4");
  env.byId("expCancel").emit("click");
  env.localStorage.removeItem(KEY);
});

test("the sessions list reaches past the newest 50", async () => {
  const chart = aChart();
  let asked = null;
  const real = globalThis.fetch;
  globalThis.fetch = async (url, opt) => {
    if (String(url).startsWith("/sessions")) asked = String(url);
    return real(url, opt);
  };
  await open(() => exportChart(chart));
  globalThis.fetch = real;
  assert.equal(new URLSearchParams(asked.split("?")[1]).get("limit"), "200",
    "a run older than the list's end cannot be picked from this dialog at all");
  env.byId("expCancel").emit("click");
});

test("Export pressed before the session list lands still carries the remembered session", async () => {
  env.localStorage.setItem(KEY, JSON.stringify(
    { mode: "session", session: "4", fromTs: null, toTs: null }));
  const chart = aChart();
  seen.lastUrl = null;
  exportChart(chart);                      // no await: /sessions is still in flight
  env.byId("expGo").emit("click");
  await tick();
  assert.equal(query().get("session"), "4",
    "an early Export must not export the open run instead of the remembered one");
  env.localStorage.removeItem(KEY);
});

test("a daemon refusal stays in the dialog, with the range that produced it", async () => {
  // A refusal is shown in the dialog, which stays open. A token forces the fetch path, the only
  // one that can read a refusal.
  setToken("t");
  const chart = aChart();
  await open(() => exportChart(chart));
  opt("changes").checked = true;
  opt("changes").emit("change");
  opt("deadband").value = "nosuch=0.5";    // not among the exported names: a 400 at the daemon
  opt("deadband").emit("change");
  await pressExport();
  assert.match(env.byId("expErr").textContent, /deadband names no exported channel: nosuch=0\.5/);
  assert.equal(env.byId("exportDlg").getAttribute("open"), "",
    "the dialog stays open so the options that were refused can be corrected");

  // Correcting it exports and closes.
  opt("deadband").value = "a=0.5";
  opt("deadband").emit("change");
  await pressExport();
  assert.equal(env.byId("exportDlg").getAttribute("open"), null);
  setToken(null);
  env.localStorage.removeItem(KEY);
});

test("the heading names what the panel exports", async () => {
  await open(() => exportChart(aChart()));
  assert.equal(env.byId("expTitle").textContent, "Export plot data");
  await open(() => env.byId("canExport").emit("click"));
  assert.equal(env.byId("expTitle").textContent, "Export CAN frames");
  env.byId("expCancel").emit("click");
});

test("an option's label points at its field; a checkbox sits inside its own", async () => {
  await open(() => exportChart(aChart()));
  const rows = env.byId("expOptions").children;
  const format = rows.find((r) => r.children[1] && r.children[1].id === "expOpt_format");
  assert.equal(format.children[0].htmlFor, "expOpt_format");
  const decode = rows.find((r) => r.className === "field checkbox-field");
  assert.equal(decode.children[0].htmlFor, undefined);
  env.byId("expCancel").emit("click");
});

test("focus lands on the range choice in force, not the close x", async () => {
  const focused = [];
  for (const id of ["expModeSession", "expModeClock", "expModeShown"]) env.byId(id).focus = () => focused.push(id);
  env.localStorage.setItem(KEY, JSON.stringify({ mode: "clock", fromTs: 1000, toTs: 2000, session: null }));
  await open(() => exportChart(aChart()));
  assert.deepEqual(focused, ["expModeClock"]);
  env.byId("expCancel").emit("click");
  env.localStorage.removeItem(KEY);
});

test("Enter in an option exports once, and a second press while it runs does not", async () => {
  await open(() => exportChart(aChart()));
  const before = seen.fetched + seen.navigated;
  const dlg = env.byId("exportDlg");
  dlg.emit("keydown", { key: "Enter", target: { tagName: "INPUT" }, preventDefault() {} });
  dlg.emit("keydown", { key: "Enter", target: { tagName: "INPUT" }, preventDefault() {} });
  env.byId("expGo").emit("click");
  await tick();
  await tick();
  assert.equal(seen.fetched + seen.navigated - before, 1);
  assert.equal(env.byId("expGo").disabled, false, "Export is usable again once the download is away");
  env.localStorage.removeItem(KEY);
});

test("no URL this dialog built would be refused by the daemon", async () => {
  // Every URL the tests above built went through the double; only the deliberate `nosuch`
  // refusal may appear.
  assert.ok(seen.lastUrl, "the suite must have built at least one export URL");
  assert.deepEqual(seen.refusals.filter(([url]) => !url.includes("nosuch")), [],
    "the dialog must not be able to build a URL the daemon answers 4xx to");
  assert.ok(seen.navigated > 0,
    "with no token the download is a navigation, so the guards must cover that road too");
});

test("switching the range choice away and back keeps clock bounds typed but not exported", async () => {
  const chart = aChart();
  await open(() => exportChart(chart));
  env.byId("expModeClock").emit("change");
  env.byId("expFrom").value = "2026-09-08T12:00:00";
  env.byId("expTo").value = "2026-09-08T13:00:00";
  env.byId("expModeSession").emit("change");
  assert.equal(env.byId("expFrom").value, "2026-09-08T12:00:00", "a mode change re-rendered the range");
  env.byId("expModeClock").emit("change");
  assert.equal(env.byId("expFrom").disabled, false);
  assert.equal(env.byId("expTo").value, "2026-09-08T13:00:00", "the typed end was lost on the way back");
  env.byId("expCancel").emit("click");
});
