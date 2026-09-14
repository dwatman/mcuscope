// Two boards declaring the same stream id and the same channel and lane names (SPEC 9.2:
// names are unique only within a port). Charts were keyed by sid and lanes by name, so the
// second board's samples interleaved into the first board's trace with no port named anywhere.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);

const { state } = await import(webuiUrl("state.js"));
const { charts, plotIngest, plotSeed, exportChart, clearAllCharts } = await import(webuiUrl("plots.js"));
const { digitalLanes, exportDigital, buildDigitalHead, clearAllDigital } = await import(webuiUrl("digital.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();
buildDigitalHead();

let nextId = 0;
function ingest(port, raw) {
  const row = { id: ++nextId, ts: 1000 + nextId * 0.01, port, chan: "event", raw };
  state.maxId = row.id;
  plotIngest(row);
}

const DEF = "!pd 0 temp:s2:C state:u1:=0=IDLE,1=RUN io:u1:/led";

function twoBoards() {
  clearAllCharts();
  clearAllDigital();
  ingest("a", DEF);
  ingest("b", DEF);
  ingest("a", "!ps 0 64 000A,00,01");
  ingest("b", "!ps 0 64 0014,01,00");
  ingest("a", "!ps 0 C8 000B,00,01");
}

async function pressExport(open, pick = {}) {
  seen.lastUrl = null;
  const dlg = env.byId("exportDlg");
  dlg.close();
  open();
  if (!dlg.hasAttribute("open")) return null;
  for (const [name, value] of Object.entries(pick)) {
    const el = env.byId("expOptions").querySelector("#expOpt_" + name);
    el.value = value;
    el.emit("change");
  }
  env.byId("expGo").emit("click");
  await tick();
  return seen.lastUrl ? new URLSearchParams(seen.lastUrl.split("?")[1]) : null;
}

test("the same stream id and channel name on two ports build two charts, neither absorbing the other", () => {
  twoBoards();
  const a = charts.get("a|s0"), b = charts.get("b|s0");
  assert.ok(a && b, `expected one chart per port, got ${[...charts.keys()]}`);
  assert.equal(charts.size, 2);
  assert.deepEqual(a.ys.get("temp"), [10, 11], "board a's trace holds only board a's samples");
  assert.deepEqual(b.ys.get("temp"), [20], "board b's trace holds only board b's samples");
});

test("the same enum and bit lane names on two ports are two lanes each", () => {
  twoBoards();
  assert.deepEqual([...digitalLanes.keys()].sort(), ["a|led", "a|state", "b|led", "b|state"]);
  assert.deepEqual(digitalLanes.get("a|state").vs, [0], "a transition-reduced lane: a held IDLE is one vertex");
  assert.deepEqual(digitalLanes.get("b|state").vs, [1]);
});

test("the port is named only once a second port has contributed", () => {
  clearAllCharts();
  clearAllDigital();
  ingest("a", DEF);
  ingest("a", "!ps 0 64 000A,00,01");
  const a = charts.get("a|s0");
  assert.equal(a.portEl.hidden, true, "one board: a port tag is noise");
  assert.equal(digitalLanes.get("a|state").portEl.hidden, true);
  ingest("b", DEF);
  ingest("b", "!ps 0 64 0014,01,00");
  assert.equal(a.portEl.hidden, false, "the first board's chart must name its port once b arrives");
  assert.equal(a.portEl.textContent, "a");
  assert.equal(charts.get("b|s0").portEl.textContent, "b");
  assert.equal(digitalLanes.get("a|state").portEl.hidden, false, "and so must its lanes");
  assert.equal(digitalLanes.get("b|led").portEl.hidden, false, "a lane born into two ports is tagged too");
  const groups = env.byId("digitalLanes").children.filter((c) => c.className === "dgroup").map((c) => c.textContent);
  assert.deepEqual(groups, ["io (packed) on a", "io (packed) on b"]);
  clearAllCharts();
  clearAllDigital();
  ingest("b", DEF);
  ingest("b", "!ps 0 64 0014,01,00");
  assert.equal(charts.get("b|s0").portEl.hidden, true, "after a clear, one board is untagged again");
});

test("a port that detaches and re-attaches rejoins its own chart and lanes", () => {
  twoBoards();
  const a = charts.get("a|s0");
  const lane = digitalLanes.get("a|state");
  // The board goes away (nothing client-side is dropped), board b keeps talking, then board a
  // is attached again under the same alias and rebroadcasts its definition.
  ingest("b", "!ps 0 12C 0015,01,00");
  ingest("a", DEF);
  ingest("a", "!ps 0 190 000C,01,01");
  assert.equal(charts.size, 2, "a re-attach must not build a third chart");
  assert.equal(charts.get("a|s0"), a, "the same chart object, not a rebuilt one");
  assert.deepEqual(a.ys.get("temp"), [10, 11, 12]);
  assert.deepEqual(charts.get("b|s0").ys.get("temp"), [20, 21]);
  assert.equal(digitalLanes.get("a|state"), lane);
  assert.deepEqual(lane.vs, [0, 1]);
});

test("a stream redeclared with different fields changes only that port's chart", () => {
  twoBoards();
  ingest("a", "!pd 0 temp:s2:K hum:u1:pct");
  ingest("a", "!ps 0 12C 0120,2A");
  const a = charts.get("a|s0"), b = charts.get("b|s0");
  assert.deepEqual(a.names, ["temp", "hum"], "the new field joins board a's chart");
  assert.deepEqual(a.ys.get("hum"), [null, null, 42], "backfilled with gaps, aligned with x");
  assert.equal(a.unit.get("temp"), "K", "the newest definition is in force for the unit");
  const chip = a.chansEl.children.find((c) => c.textContent.startsWith("temp"));
  assert.ok(chip.textContent.endsWith("K"), `the chip must show the new unit, got ${chip.textContent}`);
  assert.deepEqual(b.names, ["temp"], "board b never redeclared anything");
  assert.equal(b.unit.get("temp"), "C");
  assert.deepEqual(b.ys.get("temp"), [20]);
});

test("the history seed keeps two ports' same-named channels apart", () => {
  clearAllCharts();
  clearAllDigital();
  plotSeed([
    { channel: { name: "temp", port: "a", sid: "0", type: "s2", kind: "analog", unit: "C" },
      points: [{ line_id: 1, ts: 1, tick_ms: 1, value: 1 }] },
    { channel: { name: "temp", port: "b", sid: "0", type: "s2", kind: "analog", unit: "C" },
      points: [{ line_id: 2, ts: 2, tick_ms: 2, value: 2 }] },
  ]);
  assert.deepEqual(charts.get("a|s0").ys.get("temp"), [1]);
  assert.deepEqual(charts.get("b|s0").ys.get("temp"), [2]);
});

test("a chart's export is scoped to its own port", async () => {
  twoBoards();
  const q = await pressExport(() => exportChart(charts.get("b|s0")));
  assert.ok(q, "no export request was made");
  assert.equal(q.get("port"), "b", "without port= the daemon merges both boards' temp");
  assert.equal(q.get("names"), "temp");
});

test("lanes from two ports export one port at a time, named by the Port choice", async () => {
  twoBoards();
  const first = await pressExport(exportDigital);
  assert.equal(first.get("port"), "a", "the first port is the default");
  assert.deepEqual(first.get("names").split(",").sort(), ["led", "state"]);
  const second = await pressExport(exportDigital, { port: "b" });
  assert.equal(second.get("port"), "b");
  digitalLanes.get("b|led").show = false;
  const narrowed = await pressExport(exportDigital, { port: "b" });
  assert.equal(narrowed.get("names"), "state", "only board b's shown lanes");
});

test("lanes from one port export with that port and offer no Port choice", async () => {
  clearAllCharts();
  clearAllDigital();
  ingest("a", DEF);
  ingest("a", "!ps 0 64 000A,00,01");
  const q = await pressExport(exportDigital);
  assert.equal(q.get("port"), "a");
  assert.equal(env.byId("expOptions").querySelectorAll("select").some((s) => s.id === "expOpt_port"), false);
});
