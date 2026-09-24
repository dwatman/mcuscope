// digital.js: the tick-base lanes' shown window exports by line id, since the samples of one
// serial burst share a timestamp; one id index per seeded stream, per port.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);

const { state, PLOT_CAP, PLOT_SLACK } = await import(webuiUrl("state.js"));
const F = await import(webuiUrl("freeze.js"));
const TW = await import(webuiUrl("timewindow.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();
D.initDigitalCursorSync();

let nextId = 0;
const rows = [];   // {id, ts, port} of every line, for the daemon model below
function row(raw, ts, port = "p1") {
  state.maxId = ++nextId;
  rows.push({ id: nextId, ts, port });
  P.plotIngest({ id: nextId, ts, port, chan: "event", raw });
  return nextId;
}
const hex = (n) => n.toString(16).padStart(8, "0");

// The daemon's selection: ts > since_ts, ts <= until_ts, id > since_id, id <= id_to, port.
function selected(q, port = "p1") {
  const b = (k, d) => (q.has(k) ? Number(q.get(k)) : d);
  return rows.filter((r) => r.port === port && r.ts > b("since_ts", -Infinity) && r.ts <= b("until_ts", Infinity)
    && r.id > b("since_id", -Infinity) && r.id <= b("id_to", Infinity)).map((r) => r.id);
}

const opt = (name) => [...env.byId("expOptions").querySelectorAll("select")].find((el) => el.id === "expOpt_" + name);

async function exportShown(open, port = null) {
  seen.lastUrl = null;
  open();
  assert.equal(env.byId("expModeShown").disabled, false, "the shown window was not offered");
  if (port) { opt("port").value = port; opt("port").emit("change"); }
  env.byId("expModeShown").emit("change");
  env.byId("expGo").emit("click");
  await tick();
  assert.ok(seen.lastUrl, "no export request was issued");
  assert.deepEqual(seen.refusals, []);
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

function resetAll() {
  F.pauseAll(false);
  P.clearAllCharts();
  D.clearAllDigital();
  TW.setZoom(null);
  state.timeMode = "host";
  rows.length = 0;
}

function zoom(mode, min, max) {
  state.timeMode = mode;
  F.pauseAll(true);
  TW.setZoom({ mode, min, max });
}

// ---- lead 1: the lanes' id index ----------------------------------------------------------

const BIT = { name: "f", kind: "bits" };

test("two seeded lane streams on one port keep their own id order (one index per stream)", async () => {
  resetAll();
  row("!pd 1 f:u1:/sb0", 1);
  row("!pd 2 sm:u1:=0=A,1=B", 1);
  const pts = (base) => [...Array(100).keys()].map((i) => ({ line_id: base + 2 * i, ts: 500 + i * 0.01, tick_ms: 10 * i, value: i % 2 }));
  P.plotSeed([
    { channel: { name: "sb0", port: "p1", sid: "1", type: "u1", kind: "bit", group: "f" }, points: pts(1001) },
    { channel: { name: "sm", port: "p1", sid: "2", type: "u1", kind: "enum", labels: [[0, "A"], [1, "B"]] }, points: pts(1002) },
  ]);
  assert.ok(D.digitalLanes.get("p1|sm").vs.length, "the second stream must have been seeded");
  state.maxId = 2000;
  zoom("tick", 205, 795);   // i = 21..79 on both streams
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("since_id"), String(1001 + 42 - 1));
  assert.equal(q.get("id_to"), String(1002 + 158), "stream 2's last sample inside is the later id");
});

test("across a port's lane streams the export takes the smallest first id and the largest last", async () => {
  resetAll();
  row("!pd 1 f:u1:/qb0", 1);
  row("!pd 2 qm:u1:=0=A,1=B", 1);
  const pts = (base, off) => [...Array(100).keys()].map((i) => ({ line_id: base + 2 * i, ts: 500 + i * 0.01, tick_ms: 10 * i + off, value: i % 2 }));
  P.plotSeed([
    { channel: { name: "qb0", port: "p1", sid: "1", type: "u1", kind: "bit", group: "f" }, points: pts(1001, 0) },
    { channel: { name: "qm", port: "p1", sid: "2", type: "u1", kind: "enum", labels: [[0, "A"], [1, "B"]] }, points: pts(1002, 7) },
  ]);
  state.maxId = 2000;
  // Stream 1: i = 21 (210) .. 78 (780), ids 1043 .. 1157. Stream 2: i = 21 (217) .. 77 (777), ids 1044 .. 1156.
  zoom("tick", 208, 785);
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("since_id"), "1042");
  assert.equal(q.get("id_to"), "1157");
});

test("lanes on two ports: each Port choice exports its own ids, and one with none exports nothing", async () => {
  resetAll();
  row("!pd 1 f:u1:/b0", 1, "p1");
  row("!pd 1 f:u1:/b0", 1, "p2");
  const p1 = [], early = [];   // p2's ids fall inside p1's, its ticks outside the window
  for (let i = 0; i < 100; i++) {
    p1.push(row(`!ps 1 ${hex(100000 + i * 10)} 0${i % 2}`, 20 + i, "p1"));
    if (i < 10) early.push(row(`!ps 1 ${hex(i * 10)} 0${i % 2}`, 20 + i, "p2"));
  }
  F.pauseAll(true);
  state.timeMode = "tick";   // window 30 s ending at tick 100990: p2's ticks 0..90 are outside
  let q = await exportShown(D.exportDigital, "p1");
  assert.deepEqual(selected(q, "p1"), p1);
  q = await exportShown(D.exportDigital, "p2");
  assert.equal(q.get("port"), "p2");
  assert.deepEqual(selected(q, "p2"), [], "a port with nothing inside must not export its whole history");
  assert.equal(early.length, 10);
});

test("the lanes' id index is snapshotted at pause: a trim while paused leaves the export alone", async () => {
  resetAll();
  row("!pd 1 f:u1:/b0", 1);
  let id = 100;
  const pre = [];
  for (let i = 0; i < 1000; i++) { pre.push(++id); D.digitalIngest("p1", [["b0", i % 2, BIT]], { host: 10 + i * 0.001, tick: i, id }, "p1|s1"); }
  state.maxId = id;
  F.pauseAll(true);
  for (let i = 1000; i < 1000 + PLOT_CAP + PLOT_SLACK + 10; i++) {
    D.digitalIngest("p1", [["b0", i % 2, BIT]], { host: 10 + i * 0.001, tick: i, id: ++id }, "p1|s1");
  }
  state.timeMode = "tick";
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("since_id"), String(pre[0] - 1));
  assert.equal(q.get("id_to"), String(pre.at(-1)));
});

test("a lane stream born while paused holds nothing the export covers", async () => {
  resetAll();
  row("!pd 1 f:u1:/b0", 1, "p1");
  row("!pd 1 f:u1:/b0", 1, "p2");
  for (let i = 0; i < 100; i++) row(`!ps 1 ${hex(100000 + i * 10)} 0${i % 2}`, 20 + i, "p1");
  F.pauseAll(true);
  const late = [...Array(10).keys()].map((i) => row(`!ps 1 ${hex(80000 + i * 10)} 0${i % 2}`, 200 + i, "p2"));
  state.timeMode = "tick";
  let q = await exportShown(D.exportDigital, "p2");
  assert.deepEqual(selected(q, "p2"), []);
  TW.setZoom({ mode: "tick", min: 79990, max: 80100 });   // only p2's post-pause ticks inside
  D.exportDigital();
  assert.equal(env.byId("expModeShown").disabled, true, "a window only post-pause samples fill is not shown");
  env.byId("expCancel").emit("click");
  TW.setZoom(null);
  F.pauseAll(false);
  F.pauseAll(true);   // positive control: the same samples once a pause covers them
  q = await exportShown(D.exportDigital, "p2");
  assert.deepEqual(selected(q, "p2"), late);
});

test("a lane window reaching past the trimmed index and every vertex exports from the oldest sample drawn", async () => {
  resetAll();
  row("!pd 1 f:u1:/b0", 1);
  const total = PLOT_CAP + PLOT_SLACK + 10;
  for (let i = 0; i < total; i++) D.digitalIngest("p1", [["b0", i % 2, BIT]], { host: 10 + i * 0.001, tick: i, id: 101 + i }, "p1|s1");
  state.maxId = 100 + total;
  zoom("tick", -1, total + 1);
  const q = await exportShown(D.exportDigital);
  const oldest = total - (PLOT_CAP + 9);   // the index and the lane's vertices both start here
  assert.equal(q.has("since_id"), false, "a trimmed index names no id before its oldest sample");
  assert.equal(q.get("since_ts"), String(10 + oldest * 0.001 - 1e-6));
  assert.equal(q.get("id_to"), String(100 + total));
});

test("tick base: a lanes zoom holding no sample offers no shown window", () => {
  resetAll();
  row("!pd 1 f:u1:/b0", 1);
  for (const t of [0, 100]) row(`!ps 1 ${hex(1000 + t)} 01`, 2 + t);
  zoom("tick", 1010, 1090);
  D.exportDigital();
  assert.equal(env.byId("expModeShown").disabled, true, "an empty id range would export nothing, silently");
  env.byId("expCancel").emit("click");
  zoom("tick", 1010, 1100);   // positive control: one sample inside
  D.exportDigital();
  assert.equal(env.byId("expModeShown").disabled, false);
  env.byId("expCancel").emit("click");
});

test("a lane sample late on the MCU clock does not throw the index's search off", async () => {
  resetAll();
  row("!pd 1 f:u1:/b0", 1);
  const ids = [0, 10, 20, 5, 40, 50, 60].map((t, i) => row(`!ps 1 ${hex(1000 + t)} 0${i % 2}`, 2 + i));
  zoom("tick", 1015, 1100);
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("since_id"), String(ids[2] - 1), "the first sample at or after 1015 in arrival order");
});

test("two lane samples on the zoom's right-edge tick are both exported", async () => {
  resetAll();
  row("!pd 1 f:u1:/b0", 1);
  const ids = [0, 10, 20, 20, 30].map((t, i) => row(`!ps 1 ${hex(1000 + t)} 0${i % 2}`, 2 + i));
  zoom("tick", 1005, 1020);
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("id_to"), String(ids[3]));
  assert.equal(q.get("since_id"), String(ids[1] - 1));
});
