// plots.js / digital.js: what the history seed must leave alone - a paused surface, and
// history it has already applied (REVIEW registry classes 23 and 25).
//
// The seed lands on a "pause all" that is still latched whenever a capture reset re-runs the
// backfill: resetForDbReset destroys the charts and clears the lanes, keeps the latch, and
// re-seeds. Two ways that goes wrong - a chart born live under a button reading "resume all"
// (class 25), and a frozen view whose contents move because the freeze only ever covered the
// live arrival path (class 23). So: pause, drive the seed, and assert on the contents.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const CHANNELS = [
  { name: "tri", port: "p1", sid: "0", type: "s2", scale: 0.01, unit: "V", kind: "analog",
    last_ts: Date.now() / 1000, count: 3 },
  { name: "state", port: "p1", sid: "0", type: "u1", kind: "enum",
    labels: [[0, "idle"], [1, "run"]], last_ts: Date.now() / 1000, count: 3 },
];
const POINTS = {
  tri: [{ line_id: 1, ts: 1000.0, tick_ms: 100, value: 1.5 },
        { line_id: 2, ts: 1000.1, tick_ms: 110, value: 1.6 },
        { line_id: 3, ts: 1000.2, tick_ms: 120, value: 1.7 }],
  state: [{ line_id: 1, ts: 1000.0, tick_ms: 100, value: 0 },
          { line_id: 2, ts: 1000.1, tick_ms: 110, value: 1 },
          { line_id: 3, ts: 1000.2, tick_ms: 120, value: 0 }],
};

// One non-plot row, because the seed anchors its window to the newest row the backfill
// fetched (see seedPlotHistory) and a capture with no lines has no plot points either.
const BACKFILL = [{ id: 9, ts: 1000.5, port: "p1", chan: "debug", raw: "boot" }];

globalThis.fetch = async (url) => {
  const u = String(url);
  let body = { lines: u.includes("match=") ? [] : BACKFILL };
  if (u.startsWith("/plot/channels")) body = { channels: CHANNELS };
  else if (u.startsWith("/plot/series")) {
    const name = new URLSearchParams(u.slice(u.indexOf("?") + 1)).get("name");
    body = { name, points: POINTS[name] || [] };
  }
  return { ok: true, status: 200, json: async () => body };
};

const { charts, plotSeed } = await import(webuiUrl("plots.js"));
const { digitalLanes, isDigitalPaused } = await import(webuiUrl("digital.js"));
const { anyLive, pauseAll, pauseAllLabel } = await import(webuiUrl("freeze.js"));
const { connectWs } = await import(webuiUrl("api.js"));

test("a seed arriving under 'pause all' does not un-freeze anything", async () => {
  pauseAll(true);
  assert.equal(pauseAllLabel(), "resume all", "the fixture did not actually pause");

  connectWs();
  env.sockets.at(-1).onopen();
  await tick(0);
  await tick(0);

  // The seed really did write, or everything below passes on a seed that did nothing.
  const chart = charts.get("s0");
  assert.ok(chart, "the seed built no chart at all; the assertions below prove nothing");
  assert.equal(chart.xsHost.length, 3, "the seed did not reach the chart's data arrays");
  const lane = digitalLanes.get("state");
  assert.ok(lane && lane.vs.length, "the seed did not reach the digital lanes");

  // Class 25: a chart created after the group action is born into it.
  assert.equal(chart.paused, true, "a chart the seed created came up live under 'resume all'");
  assert.equal(anyLive(), false, "the seed put a surface back on the live edge");
  assert.equal(pauseAllLabel(), "resume all");

  // Class 23: the flag is not the freeze. chart.frozen is the snapshot currentData() draws
  // from, so a frozen chart that has buffered three samples still draws none of them.
  assert.equal(chart.frozen.xsHost.length, 0,
    "the frozen chart advanced to the seeded samples: the freeze only covered live arrival");
  assert.equal(isDigitalPaused(), true, "the seed resumed the digital panel");
  assert.equal(lane.valEl.textContent, "",
    "the frozen lane's readout was written by the seed; it must hold the frozen value");
});

test("a seed never fills a surface that already holds samples", async () => {
  // The seed's samples are the older ones, and addSample nudges anything out of order up
  // past the newest x - so a seed landing on a chart that has already been fed (a reconnect,
  // or a capture reset whose backfill is still in flight) would stack the history at the
  // live edge, drawn as a burst of history in the last instant of the window.
  const chart = charts.get("s0");
  const before = chart.xsHost.slice();
  plotSeed([{ channel: CHANNELS[0],
              points: [{ line_id: 40, ts: 1000.9, tick_ms: 200, value: 9.9 },
                       { line_id: 41, ts: 1001.0, tick_ms: 210, value: 9.8 }] }]);
  assert.deepEqual(chart.xsHost, before, "the seed appended to a chart that already had data");
  assert.deepEqual(chart.ys.get("tri"), [1.5, 1.6, 1.7]);
});
