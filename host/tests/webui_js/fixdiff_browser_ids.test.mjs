// Fix-diff review of the browser ids fix, 2026-09-16: a lane window past a trimmed id index goes
// by time on that side (H1), host-base lanes go by id (L2), a lane stream the cap refused adds no
// id (L4), a seed takes field order from the backfill's own `!pd` (M1), and the disabled shown
// window says why when the panel is already paused (L1).

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

const BIT = { name: "f", kind: "bits" };
const rows = [];   // {id, ts} of every lane sample, for the daemon model below
let nextId = 0;

// One lane sample on port p1, as plots.js routes it.
function lane(name, val, host, tick, stream = "p1|s1") {
  state.maxId = ++nextId;
  rows.push({ id: nextId, ts: host });
  D.digitalIngest("p1", [[name, val, BIT]], { host, tick, id: nextId }, stream);
  return nextId;
}

// The daemon's selection: ts > since_ts, ts <= until_ts, id > since_id, id <= id_to.
function selected(q) {
  const b = (k, d) => (q.has(k) ? Number(q.get(k)) : d);
  return rows.filter((r) => r.ts > b("since_ts", -Infinity) && r.ts <= b("until_ts", Infinity)
    && r.id > b("since_id", -Infinity) && r.id <= b("id_to", Infinity)).map((r) => r.id);
}

async function exportShown(open) {
  seen.lastUrl = null;
  open();
  assert.equal(env.byId("expModeShown").disabled, false, "the shown window was not offered");
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

const TOTAL = PLOT_CAP + PLOT_SLACK + 10;   // one trim: the index keeps samples TOTAL - PLOT_CAP - 9 on
const host = (i) => 10 + i * 0.001;         // one sample a millisecond on both clocks

// Sample i at tick i. `val(i)` sets the lane's level, so its vertices reach as far back as it holds.
function trimmed(val) {
  const ids = [];
  for (let i = 0; i < TOTAL; i++) ids.push(lane("b0", val(i), host(i), i));
  return ids;
}
// A selection as {n, first, last}: a failing deepEqual over 100k ids prints a diff that runs node
// out of memory. `selected` is ascending, so equal summaries of contiguous slices are equal.
const span = (ids) => ({ n: ids.length, first: ids[0], last: ids.at(-1) });
const ticksIn = (ids, a, b) => span(ids.slice(a, b + 1));

// ---- H1: an index trimmed past the window ----------------------------------------------------

test("tick base: a window starting before the trimmed index exports its whole span, by time there", async () => {
  resetAll();
  const ids = trimmed((i) => Math.floor(i / 100) % 2);   // a vertex every 100 samples, all kept
  zoom("tick", 50.5, TOTAL + 1);
  const q = await exportShown(D.exportDigital);
  assert.equal(q.has("since_id"), false);
  assert.equal(q.get("id_to"), String(ids.at(-1)), "the upper side is still an id");
  assert.deepEqual(span(selected(q)), ticksIn(ids, 51, TOTAL - 1), "ticks 51 on, not the index's newest 100k");
});

test("tick base: a window ending before the trimmed index goes by time on both sides", async () => {
  resetAll();
  const ids = trimmed((i) => Math.floor(i / 100) % 2);
  zoom("tick", 50.5, 150.5);
  const q = await exportShown(D.exportDigital);
  assert.equal(q.has("since_id"), false);
  assert.equal(q.get("id_to"), String(state.maxId), "only the freeze bounds the ids");
  assert.deepEqual(span(selected(q)), ticksIn(ids, 51, 150));
});

test("tick base: with no lane vertex after the edge, the index's oldest sample brackets it", async () => {
  resetAll();
  const ids = trimmed((i) => (i < 10 ? 0 : 1));   // vertices at ticks 0 and 10 only
  zoom("tick", 50.5, TOTAL + 1);
  const q = await exportShown(D.exportDigital);
  assert.deepEqual(span(selected(q)), ticksIn(ids, 51, TOTAL - 1));
});

test("host base: a window starting before the trimmed index exports by its host edge there", async () => {
  resetAll();
  const ids = trimmed((i) => Math.floor(i / 100) % 2);
  zoom("host", host(50) + 0.0005, host(TOTAL));
  const q = await exportShown(D.exportDigital);
  assert.equal(q.has("since_id"), false);
  assert.equal(q.get("id_to"), String(ids.at(-1)));
  assert.deepEqual(span(selected(q)), ticksIn(ids, 51, TOTAL - 1));
});

test("another port's lanes do not bracket the edge", async () => {
  resetAll();
  const ids = trimmed((i) => Math.floor(i / 100) % 2);
  D.digitalIngest("p2", [["b0", 1, BIT]], { host: 999, tick: 50, id: nextId + 1 }, "p2|s1");
  zoom("tick", 50.5, TOTAL + 1);
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("port"), "p1");
  assert.deepEqual(span(selected(q)), ticksIn(ids, 51, TOTAL - 1));
});

test("a trim while paused moves neither bracket: the lane vertices", async () => {
  resetAll();
  const ids = trimmed((i) => Math.floor(i / 100) % 2);
  zoom("tick", 50.5, TOTAL + 1);
  for (let i = TOTAL; i < 2 * TOTAL; i++) lane("b0", i % 2, host(i), i);   // rotates the live vertices
  const q = await exportShown(D.exportDigital);
  assert.deepEqual(span(selected(q)), ticksIn(ids, 51, TOTAL - 1));
});

test("a trim while paused moves neither bracket: the id index", async () => {
  resetAll();
  const ids = trimmed((i) => (i < 10 ? 0 : 1));
  zoom("tick", 50.5, TOTAL + 1);
  for (let i = 0; i < TOTAL; i++) lane("b0", 1, 2000 + i * 0.001, TOTAL + i);   // no vertex, a later clock
  const q = await exportShown(D.exportDigital);
  assert.deepEqual(span(selected(q)), ticksIn(ids, 51, TOTAL - 1));
});

test("host base tail: the newest burst is exported whole, though its nudge passes the edge", async () => {
  resetAll();
  const ids = [lane("b0", 0, 100.5, 0), ...[...Array(10).keys()].map((j) => lane("b0", j % 2, 130, 1 + j))];
  F.pauseAll(true);
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("since_id"), String(ids[0] - 1));
  assert.equal(q.get("id_to"), String(ids.at(-1)));
});

test("an index that has not trimmed keeps the id edge for a window reaching before it", async () => {
  resetAll();
  const ids = [...Array(50).keys()].map((i) => lane("b0", i % 2, host(1000 + i), 1000 + i));
  zoom("tick", 500, 2000);
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("since_id"), String(ids[0] - 1));
  assert.equal(q.has("since_ts"), false, "nothing before the first sample is missing");
});

test("a trim before the pause is in the snapshot; one after it is not", async () => {
  resetAll();
  trimmed((i) => Math.floor(i / 100) % 2);
  zoom("tick", 50.5, TOTAL + 1);
  let q = await exportShown(D.exportDigital);
  assert.equal(q.has("since_ts"), true, "positive control: trimmed before the pause");
  resetAll();
  const ids = [...Array(1000).keys()].map((i) => lane("b0", i % 2, host(i), i));
  zoom("tick", -1, 2000);
  for (let i = 1000; i < TOTAL; i++) lane("b0", i % 2, host(i), i);
  q = await exportShown(D.exportDigital);
  assert.equal(q.get("since_id"), String(ids[0] - 1));
  assert.equal(q.get("id_to"), String(ids.at(-1)));
});

// ---- L4: a stream the lane cap refused ---------------------------------------------------------

test("a lane stream the cap refused adds no id to the port's window", async () => {
  resetAll();
  for (let k = 0; k < 64; k++) lane("l" + k, 0, 1, 1);   // MAX_LANES, one stream
  const early = nextId;
  const inside = [...Array(10).keys()].map((i) => lane("l0", i % 2, 20 + i, 1000 + i));
  // Another stream on the port, refused by the cap, with lower ids inside the window's ticks.
  state.maxId = 10000;
  D.digitalIngest("p1", [["extra", 1, BIT]], { host: 25, tick: 1005, id: early - 30 }, "p1|s9");
  assert.equal(D.digitalLanes.has("p1|extra"), false, "the fixture must hit the cap");
  zoom("tick", 1000, 1009);
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("since_id"), String(inside[0] - 1));
});

// ---- L1: the disabled shown window's title ----------------------------------------------------

test("a paused panel whose window holds nothing says so, not to pause", () => {
  resetAll();
  lane("b0", 1, host(0), 0);
  lane("b0", 0, host(100), 100);
  D.exportDigital();
  assert.equal(env.byId("expModeShown").title, "pause the panel to export exactly what it shows", "live");
  env.byId("expCancel").emit("click");
  zoom("tick", 10, 90);
  D.exportDigital();
  assert.equal(env.byId("expModeShown").disabled, true);
  assert.equal(env.byId("expModeShown").title, "the shown window holds nothing to export");
  env.byId("expCancel").emit("click");
});

// ---- M1: seed order from the backfill's own `!pd` ---------------------------------------------

const seedPts = [{ line_id: 7001, ts: 700, tick_ms: 1, value: 1 }];
const meta = (name, sid) => ({ name, port: "p1", sid, type: "u2", kind: "analog" });
const pd = (id, raw, port = "p1", chan = "event") => ({ id, ts: 1, port, chan, raw });

test("the seed takes field order from the newest `!pd` among the backfill rows", () => {
  resetAll();
  P.plotIngest(pd(1, "!pd 6 wa:u2 wt:u2"));   // primed, and older than the backfill's
  P.plotSeed([{ channel: meta("wa", "6"), points: seedPts }, { channel: meta("wt", "6"), points: seedPts }],
    [pd(2, "!pd 6 wa:u2 wt:u2"), pd(3, "!pd 6 wt:u2 wa:u2"), pd(4, "!pd 6 bad"), pd(5, "!pd 6 wa:u2 wt:u2", "p2"),
     pd(6, "!pd 6 wa:u2 wt:u2", "p1", "cmd")]);
  assert.deepEqual(P.charts.get("p1|s6").names, ["wt", "wa"]);
});
