// exportdlg.js: the one export dialog, driven from the panels that open it.
//
// The stub has no layout, so what is asserted here is the wiring: which range choices a panel
// offers, the URL Export builds from the chosen range plus the panel's own options, and that
// the remembered range moves on Export and not on Cancel.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const SESSIONS = { sessions: [
  { id: 4, name: "run-a", lines: 120, started_ts: 1000, ended_ts: 2000, auto: false },
  { id: 7, name: "run-b", lines: 5, started_ts: 3000, ended_ts: null, auto: false },
] };

let lastUrl = null;
globalThis.fetch = async (url) => {
  lastUrl = url;
  return {
    ok: true, status: 200,
    headers: { get: () => null },
    blob: async () => new Blob(["body"]),
    json: async () => (String(url).startsWith("/sessions") ? SESSIONS : {}),
  };
};

const { state, PLOT_CAP, PLOT_SLACK } = await import(webuiUrl("state.js"));
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
  if (!charts.get("s0")) {
    ingest("!pd 0 a:u2 b:u2");
    for (let i = 0; i < 5; i++) ingest(`!ps 0 ${(0x100 + i).toString(16)} 000${i},00A${i}`);
  }
  return charts.get("s0");
}

async function open(fn) {
  lastUrl = null;
  fn();
  await tick();          // the sessions fetch the dialog fires on open
}

function pressExport() { lastUrl = null; env.byId("expGo").emit("click"); }
function query() {
  assert.ok(lastUrl, "no export request was issued");
  return new URLSearchParams(lastUrl.split("?")[1]);
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

  pressExport();
  const q = query();
  assert.ok(lastUrl.startsWith("/plot/export?"), `built ${lastUrl}`);
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
  pressExport();
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
  pressExport();
  assert.equal(query().has("decode"), false);
});

test("inverted clock bounds are refused inline and send nothing", async () => {
  const chart = aChart();
  await open(() => exportChart(chart));
  env.byId("expModeClock").emit("change");
  env.byId("expFrom").value = "2026-09-08T12:00:00";
  env.byId("expTo").value = "2026-09-08T11:00:00";

  lastUrl = null;
  env.byId("expGo").emit("click");
  assert.equal(env.byId("expErr").textContent, "the end of the range is before its start");
  assert.equal(lastUrl, null, "a refused range must not reach the daemon");
  assert.equal(env.byId("exportDlg").getAttribute("open"), "",
    "the dialog stays open so the bounds can be fixed");

  // Fixing the order sends it, as local time converted to epoch seconds.
  env.byId("expTo").value = "2026-09-08T13:00:00";
  pressExport();
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
  pressExport();
  const saved = JSON.parse(env.localStorage.getItem(KEY));
  assert.equal(saved.mode, "clock");
  assert.equal(saved.fromTs, Date.parse("2026-09-08T12:00:00") / 1000);

  // And the next panel that opens the dialog starts from it.
  await open(() => exportChart(chart));
  assert.equal(env.byId("expModeClock").checked, true, "the range must be remembered across panels");
  env.byId("expWhole").emit("click");
  assert.equal(env.byId("expModeSession").checked, true, "whole session must reset the choice");
  env.byId("expCancel").emit("click");
  env.localStorage.removeItem(KEY);
});

test("the session list labels the open run and preselects it", async () => {
  const chart = aChart();
  await open(() => exportChart(chart));
  const labels = env.byId("expSession").children.map((o) => o.textContent);
  assert.deepEqual(labels, ["run-a (120 lines)", "run-b (5 lines) (open)"]);
  pressExport();
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
  pressExport();
  const q = query();
  assert.ok(lastUrl.startsWith("/can/frames?"), `built ${lastUrl}`);
  assert.equal(q.get("format"), "csv");
  assert.equal(q.get("id"), "7DF", "an edited id list must replace the prefill, not add to it");

  // The snapshot is the client-side table, so it downloads a blob and hits no endpoint.
  await open(() => env.byId("canExport").emit("click"));
  opt("format").value = "snapshot";
  opt("format").emit("change");
  const blobs = env.blobs.length;
  pressExport();
  assert.equal(lastUrl, null, "the table snapshot must not be fetched from the daemon");
  assert.equal(env.blobs.length, blobs + 1, "the snapshot must still download");
  env.localStorage.removeItem(KEY);
});

test("an empty id list exports every id in the range", async () => {
  await open(() => env.byId("canExport").emit("click"));
  opt("ids").value = "   ";
  opt("ids").emit("change");
  pressExport();
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
  pressExport();
  assert.equal(query().get("last_ms"), String(chart.window * 1000));
  assert.equal(query().get("id_to"), String(frozenAt));

  await open(() => exportChart(chart));
  env.byId("expModeSession").emit("change");
  pressExport();
  assert.equal(query().has("last_ms"), false);
  assert.equal(query().get("id_to"), String(frozenAt),
    "a whole-session export from a paused chart must still stop where the chart does");
  setChartPaused(chart, false);
  env.localStorage.removeItem(KEY);
});
