// A paused chart or digital panel must export the window it SHOWS, not the last N seconds
// measured from now (REVIEW registry class 23, finding M5).
//
// The export sends last_ms only, which the daemon resolves against now, so a chart paused on
// a transient downloaded a window that no longer contains it - under a button whose own title
// says "the current window". The fix is the id watermark taken at pause (the same shape as
// terminal.js's pane.frozenId), sent as id_to; the daemon then measures last_ms back from
// that line.
//
// Per class 23's sweep the assertion is made after driving the OTHER writer: enough samples to
// take the ring past PLOT_CAP, which slides the freeze index and would move any watermark
// derived from the sample arrays.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();

let lastUrl = null;
globalThis.fetch = async (url) => {
  lastUrl = url;
  return {
    ok: true, status: 200,
    headers: { get: () => null },
    blob: async () => new Blob(["csv"]),
    json: async () => ({}),
  };
};

const { state, PLOT_CAP, PLOT_SLACK } = await import(webuiUrl("state.js"));
const { charts, plotIngest, setChartPaused, exportChart } = await import(webuiUrl("plots.js"));
const { setDigitalPaused, exportDigital, digitalLanes } = await import(webuiUrl("digital.js"));

let nextId = 0;
let nextTs = 1000;
function ingest(raw) {
  const row = { id: ++nextId, ts: (nextTs += 0.01), port: "p1", chan: "event", raw };
  state.maxId = row.id;               // pushBuffer's job in the live path
  plotIngest(row);
}

// !ps with a 32-bit tick and one field of `nibbles` hex digits, `n` samples, value changing.
function samples(sid, n, from, nibbles = 4) {
  const mask = (1 << (nibbles * 4)) - 1;
  for (let i = 0; i < n; i++) {
    const tick = (from + i).toString(16).toUpperCase();
    const val = ((from + i) & mask).toString(16).toUpperCase().padStart(nibbles, "0");
    ingest(`!ps ${sid} ${tick} ${val}`);
  }
}

function params() {
  assert.ok(lastUrl, "no export request was issued");
  return new URLSearchParams(lastUrl.split("?")[1]);
}

test("a paused chart exports the window it froze on, not the one ending now", async () => {
  ingest("!pd 0 a:u2");
  samples(0, 10, 0x100);
  const chart = charts.get("s0");
  assert.ok(chart, "the stream must have built a chart");

  setChartPaused(chart, true);
  const frozenAt = state.maxId;
  assert.equal(chart.frozenMaxId, frozenAt, "the pause must record a line-id watermark");

  // Drive the other writer past the ring cap, so the freeze index slides.
  const extra = PLOT_CAP + PLOT_SLACK + 100;
  samples(0, extra, 0x1000);
  assert.ok(chart.xsHost.length < extra, "the ring must have trimmed");
  assert.ok(state.maxId > frozenAt + 1000, "the id watermark must now be well behind live");

  await exportChart(chart);
  const p = params();
  assert.equal(p.get("id_to"), String(frozenAt),
    "the export must be bounded at the pause watermark, or a paused chart exports a window " +
    "measured from now and the frozen transient is not in it");
  assert.equal(p.get("last_ms"), String(chart.window * 1000));
});

test("a live chart sends no id_to at all", async () => {
  const chart = charts.get("s0");
  setChartPaused(chart, false);
  assert.equal(chart.frozenMaxId, null, "resuming must clear the watermark");

  lastUrl = null;
  await exportChart(chart);
  const p = params();
  assert.equal(p.has("id_to"), false, "a live export must keep the daemon anchored at now");
  assert.equal(p.get("last_ms"), String(chart.window * 1000));
});

test("a paused digital panel exports the window it froze on", async () => {
  ingest("!pd 1 f:u1:/b0,b1");
  samples(1, 10, 0x100, 2);
  assert.ok(digitalLanes.size > 0, "the bits stream must have built lanes");

  setDigitalPaused(true);
  const frozenAt = state.maxId;

  samples(1, 500, 0x2000, 2);
  assert.ok(state.maxId > frozenAt + 400, "the id watermark must now be behind live");

  lastUrl = null;
  await exportDigital();
  const p = params();
  assert.equal(p.get("id_to"), String(frozenAt),
    "the digital export must be bounded at the pause watermark");

  setDigitalPaused(false);
  lastUrl = null;
  await exportDigital();
  assert.equal(params().has("id_to"), false, "a live digital export must send no bound");
});
