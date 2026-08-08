// api.js + plots.js: a fresh page must seed the charts from stored history (SPEC 9.2).
//
// The web UI discovered channels from live WebSocket traffic alone and never called
// /plot/channels or /plot/series at all, so after a reload the charts were empty until new
// samples arrived, and a channel that had stopped emitting never appeared - with the daemon
// holding its whole history the entire time. The owner confirmed it in a browser against
// `mcuscoped --sim` with ~24000 stored plot points on 2026-08-08.
//
// Driven through connectWs() and the exported chart/lane models, so the seed is exercised
// exactly where the live stream exercises the same code: the request shape and its bounds,
// the merge back into one x array per chart, the digital lanes, and the de-duplication
// against the rows the /lines backfill replays.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

// The whole fixture runs on the daemon's timeline, because the seed does: the newest row
// the backfill fetched (line 15, below) is the anchor every window is measured back from.
const ANCHOR_TS = 1000.5;

// One typed stream (analog + enum + packed bit), one ad-hoc channel, one channel that
// stopped emitting ten minutes ago, and enough filler to overrun the channel cap.
const CHANNELS = [
  { name: "tri", port: "p1", sid: "0", type: "s2", scale: 0.01, unit: "V", kind: "analog",
    last_ts: 1000.2, count: 3 },
  { name: "state", port: "p1", sid: "0", type: "u1", kind: "enum",
    labels: [[0, "idle"], [1, "run"]], last_ts: 1000.2, count: 3 },
  { name: "led", port: "p1", sid: "0", type: "u1", kind: "bit", group: "flags", bit: 0,
    last_ts: 1000.2, count: 3 },
  { name: "sine", port: "p1", sid: null, kind: "analog", last_ts: 1000.2, count: 2 },
  { name: "old_temp", port: "p1", sid: "1", type: "u2", unit: "C", kind: "analog",
    last_ts: 400.2, count: 2 },
];
for (let i = 0; i < 35; i++) {
  CHANNELS.push({ name: "filler" + i, port: "p1", sid: "2", type: "u2", kind: "analog",
                  last_ts: 100, count: 1 });
}

// Stream 0's three samples, as /plot/series answers them: one call per channel, and the
// three channels share the line ids they were decoded from.
const SERIES = {
  tri: [{ line_id: 10, ts: 1000.0, tick_ms: 100, value: 1.5 },
        { line_id: 11, ts: 1000.1, tick_ms: 110, value: 1.6 },
        { line_id: 12, ts: 1000.2, tick_ms: 120, value: 1.7 }],
  state: [{ line_id: 10, ts: 1000.0, tick_ms: 100, value: 0 },
          { line_id: 11, ts: 1000.1, tick_ms: 110, value: 1 },
          { line_id: 12, ts: 1000.2, tick_ms: 120, value: 1 }],
  led: [{ line_id: 10, ts: 1000.0, tick_ms: 100, value: 0 },
        { line_id: 11, ts: 1000.1, tick_ms: 110, value: 0 },
        { line_id: 12, ts: 1000.2, tick_ms: 120, value: 1 }],
  sine: [{ line_id: 13, ts: 1000.3, tick_ms: 130, value: 0.5 },
         { line_id: 14, ts: 1000.4, tick_ms: 140, value: 0.6 }],
  old_temp: [{ line_id: 4, ts: 900.0, tick_ms: 40, value: 21 },
             { line_id: 5, ts: 900.1, tick_ms: 50, value: 22 }],
};

// The /lines backfill replays line 12 (already seeded) and carries line 15 (not seeded).
const SEED_ROWS = [
  { id: 15, ts: 1000.5, port: "p1", chan: "event", raw: "!ps 0 96 00B4,01,03" },
  { id: 12, ts: 1000.2, port: "p1", chan: "event", raw: "!ps 0 78 00AA,01,01" },
];
const DEF_ROWS = [
  { id: 1, ts: 999, port: "p1", chan: "event",
    raw: "!pd 0 tri:s2*0.01:V state:u1:=0=idle,1=run flags:u1:/led,motor" },
];

const seen = [];
globalThis.fetch = async (url) => {
  const u = String(url);
  seen.push(u);
  let body;
  if (u.startsWith("/plot/channels")) {
    body = { channels: CHANNELS };
  } else if (u.startsWith("/plot/series")) {
    const name = new URLSearchParams(u.slice(u.indexOf("?") + 1)).get("name");
    body = { name, port: "p1", points: SERIES[name] || [] };
  } else {
    body = { lines: u.includes("match=") ? DEF_ROWS : SEED_ROWS };
  }
  return { ok: true, status: 200, json: async () => body };
};

const { charts } = await import(webuiUrl("plots.js"));
const { digitalLanes } = await import(webuiUrl("digital.js"));
const { connectWs } = await import(webuiUrl("api.js"));

const seriesQueries = () => seen.filter((u) => u.startsWith("/plot/series"));
const queryOf = (url) => new URLSearchParams(url.slice(url.indexOf("?") + 1));

test("a fresh page seeds the charts and lanes from stored plot history", async () => {
  connectWs();
  const sock = env.sockets.at(-1);
  assert.ok(sock, "connectWs did not open a socket");
  sock.onopen();
  await tick(0);
  await tick(0);

  // Discovery: without this call a channel that has stopped emitting is never even known.
  assert.equal(seen.filter((u) => u.startsWith("/plot/channels")).length, 1,
    `expected exactly one /plot/channels discovery call, got ${seen.join(" | ")}`);

  // The payoff. Three samples of one typed stream, merged back onto ONE x array: a chart
  // holding two x values per sample (one per channel) is the merge failing.
  const s0 = charts.get("s0");
  assert.ok(s0, "no chart for stream 0: the seeded history never reached the model");
  assert.deepEqual(s0.ys.get("tri"), [1.5, 1.6, 1.7, 1.8],
    "the analog channel did not seed its stored samples (last one is live, from line 15)");
  assert.equal(s0.unit.get("tri"), "V", "the unit from /plot/channels was dropped");

  // The ad-hoc chart is a separate chart, keyed by a NULL sid in the store.
  const adhoc = charts.get("adhoc");
  assert.ok(adhoc, "no ad-hoc chart: a sid of null must land on the shared ad-hoc chart");
  assert.deepEqual(adhoc.ys.get("sine"), [0.5, 0.6]);

  // The channel that stopped ten minutes ago: present, with its own history.
  const s1 = charts.get("s1");
  assert.ok(s1, "the stopped channel never appeared, which is half the defect");
  assert.deepEqual(s1.ys.get("old_temp"), [21, 22]);

  // The digital lanes come from the same seed, routed by the kind /plot/channels reports.
  const enumLane = digitalLanes.get("state");
  assert.ok(enumLane, "the enum lane was not seeded");
  assert.equal(enumLane.kind, "enum");
  assert.deepEqual(enumLane.labels, [[0, "idle"], [1, "run"]], "enum labels were dropped");
  assert.deepEqual(enumLane.vs, [0, 1], "transition-reduced history: 0 then 1");
  const bitLane = digitalLanes.get("led");
  assert.ok(bitLane, "the packed-bit lane was not seeded");
  assert.equal(bitLane.kind, "bits");
  assert.equal(bitLane.group, "flags", "a bit lane must sit under its parent group");
});

test("the seed is bounded: capped channels, capped points, no decimation", async () => {
  const queries = seriesQueries();
  // One request per channel, so the channel cap is also the request fan-out cap.
  assert.equal(queries.length, 32,
    `expected the 32-channel cap over ${CHANNELS.length} channels, got ${queries.length}`);
  // Most recently active first, so a device rotating channel names seeds what is watched.
  const names = queries.map((u) => queryOf(u).get("name"));
  for (const want of ["tri", "state", "led", "sine", "old_temp"]) {
    assert.ok(names.includes(want), `${want} was cut by the cap ahead of an idle filler`);
  }
  for (const u of queries) {
    const q = queryOf(u);
    assert.equal(q.get("limit"), "2000", `unbounded point count in ${u}`);
    assert.ok(Number(q.get("last_ms")) > 0, `unbounded window in ${u}`);
    assert.equal(q.get("port"), "p1",
      "without port= two boards declaring one name seed a single merged channel");
    // One anchor line for every channel, so the daemon measures each window back from the
    // same timestamp. Without it the two edges of the window differ per channel by a sample,
    // and a chart's shared x array turns each disagreement into a null gap in a trace.
    assert.equal(q.get("id_to"), "15", `window not anchored to the backfill's newest row: ${u}`);
    // min/max decimation answers each channel on a different set of lines, which a shared
    // x array turns into nulls and a stepped path draws as isolated dots.
    assert.equal(q.get("decimate"), null, `decimation would shatter the merge: ${u}`);
  }

  // The window is the one the UI comes up showing (30 s), plus the channel's own silence -
  // a channel that stopped ten minutes ago answers an empty window without it.
  const live = Number(queryOf(queries[names.indexOf("tri")]).get("last_ms"));
  assert.equal(live, 30300, "an active channel asks for the shown window plus its own 0.3 s idle");
  const stale = Number(queryOf(queries[names.indexOf("old_temp")]).get("last_ms"));
  assert.equal(stale, 630300,
    `a channel idle for 600 s asked for ${stale} ms; its own silence is not being added`);
});

test("the seed does not double-count what the backfill replays", async () => {
  const s0 = charts.get("s0");
  // Line 12 is in the seed AND in the /lines backfill; line 15 is only in the backfill.
  assert.deepEqual(s0.xsHost.map((v) => Math.round(v * 10) / 10), [1000, 1000.1, 1000.2, 1000.5],
    "a replayed line was ingested twice (or a newer live line was dropped by the watermark)");
  // The live line past the watermark still decodes into the lanes as well.
  assert.deepEqual(digitalLanes.get("motor").vs, [1],
    "a channel absent from the seed must still ingest live");
});

test("a reconnect does not seed again on top of the history it already holds", async () => {
  const before = seen.filter((u) => u.startsWith("/plot/channels")).length;
  const s0 = charts.get("s0");
  const samples = s0.xsHost.length;
  connectWs();
  env.sockets.at(-1).onopen();
  await tick(0);
  await tick(0);
  assert.equal(seen.filter((u) => u.startsWith("/plot/channels")).length, before,
    "a reconnect re-ran the history seed; the charts already hold that history");
  assert.equal(s0.xsHost.length, samples, "the chart grew on a reconnect that carried no new rows");
});
