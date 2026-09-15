// A paused chart or digital panel must export the window it SHOWS, not the last N seconds
// measured from now (REVIEW registry class 23, finding M5).
//
// The export sends a range the daemon resolves against now, so a chart paused on a transient
// downloaded a window that no longer contains it. The fix is the id watermark taken at pause
// (the same shape as terminal.js's pane.frozenId), sent as id_to in EVERY range mode
// (exportrange.params), so no range the shared dialog offers can reach past what the frozen
// surface shows.
//
// Per class 23's sweep the assertion is made after driving the OTHER writer: enough samples to
// take the ring past PLOT_CAP, which slides the freeze index and would move any watermark
// derived from the sample arrays.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick, makePane, makeRow } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();

// The double applies the endpoints' own parameter guards rather than answering 200 to
// everything (W6), and records the URL by whichever road it left on: a fetch, or the
// `<a download>` navigation state.js uses when no token is set.
const seen = installExportDaemon(env);

const { state, PLOT_CAP, PLOT_SLACK } = await import(webuiUrl("state.js"));
const { charts, plotIngest, setChartPaused, exportChart } = await import(webuiUrl("plots.js"));
const { setDigitalPaused, exportDigital, digitalLanes } = await import(webuiUrl("digital.js"));
const { ALL_CHANS } = await import(webuiUrl("pane.js"));
const { exportPane } = await import(webuiUrl("terminal.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();

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

// Open the panel's export dialog, optionally pick a range mode, and press Export.
async function pressExport(open, mode) {
  seen.lastUrl = null;
  open();
  if (mode) env.byId("expMode" + mode).emit("change");
  env.byId("expGo").emit("click");
  await tick();
}

function params() {
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

// Ticks only climb across the file, so no test's samples read as a board reset.
let nextTick = 0x100;

// Chart s0 paused after 10 samples, then driven past the ring cap so the freeze index slides.
function pausedPastRing() {
  ingest("!pd 0 a:u2");
  samples(0, 10, nextTick);
  const chart = charts.get("p1|s0");
  assert.ok(chart, "the stream must have built a chart");

  setChartPaused(chart, false);
  setChartPaused(chart, true);
  const frozenAt = state.maxId;
  assert.equal(chart.frozenMaxId, frozenAt, "the pause must record a line-id watermark");

  const extra = PLOT_CAP + PLOT_SLACK + 100;
  samples(0, extra, nextTick + 10);
  nextTick += 10 + extra;
  assert.ok(chart.xsHost.length < extra, "the ring must have trimmed");
  assert.ok(state.maxId > frozenAt + 1000, "the id watermark must now be well behind live");
  return { chart, frozenAt };
}

test("a paused chart exports the window it froze on, not the one ending now", async () => {
  const { chart, frozenAt } = pausedPastRing();

  await pressExport(() => exportChart(chart), "Shown");
  const p = params();
  assert.equal(p.get("id_to"), String(frozenAt),
    "the export must be bounded at the pause watermark, or a paused chart exports a window " +
    "measured from now and the frozen transient is not in it");
  // The window ends at the chart's own frozen sample, not measured back from the id_to row.
  const edge = chart.frozen.xsHost.at(-1);
  assert.equal(p.get("until_ts"), String(edge));
  assert.equal(p.get("since_ts"), String(edge - chart.window - 1e-6));
  assert.equal(p.has("last_ms"), false, "a duration is anchored on the id_to row, not on the chart");
});

test("the watermark still bounds a range that is not the shown window", async () => {
  const { chart, frozenAt } = pausedPastRing();

  // Session range: no last_ms at all, but the frozen surface must not export past its edge.
  await pressExport(() => exportChart(chart), "Session");
  const p = params();
  assert.equal(p.has("last_ms"), false, "a session range is not a last-N-seconds window");
  assert.equal(p.get("id_to"), String(frozenAt),
    "a whole-session export from a PAUSED chart must still stop at what the chart shows");
});

test("a live chart sends no id_to at all", async () => {
  const { chart } = pausedPastRing();
  setChartPaused(chart, false);
  assert.equal(chart.frozenMaxId, null, "resuming must clear the watermark");

  await pressExport(() => exportChart(chart), "Session");
  const p = params();
  assert.equal(p.has("id_to"), false, "a live export must keep the daemon anchored at now");
});

test("a paused digital panel exports the window it froze on", async () => {
  ingest("!pd 1 f:u1:/b0,b1");
  samples(1, 10, 0x100, 2);
  assert.ok(digitalLanes.size > 0, "the bits stream must have built lanes");

  setDigitalPaused(true);
  const frozenAt = state.maxId;

  samples(1, 500, 0x2000, 2);
  assert.ok(state.maxId > frozenAt + 400, "the id watermark must now be behind live");

  await pressExport(exportDigital, "Shown");
  assert.equal(params().get("id_to"), String(frozenAt),
    "the digital export must be bounded at the pause watermark");

  setDigitalPaused(false);
  await pressExport(exportDigital, "Session");
  assert.equal(params().has("id_to"), false, "a live digital export must send no bound");
});

// ---- the terminal pane, the third freeze surface --------------------------------------
//
// exportPane was module-private and driven by a button that only exists inside index.html's
// <template>, which the DOM stub cannot clone, so nothing here reached it: dropping the
// channel filter (M19) and dropping the freeze watermark (M20) both left the suite green,
// while the chart and lane exports beside it were pinned.

// A pane holding `n` rows, filtered the way a user filters one.
function pane(over = {}) {
  const p = makePane({ port: "p1", regexSrc: "^!can ", regex: /^!can /, ...over });
  p.channels = new Set(["debug", "event"]);
  p.rows = [makeRow(10, { ts: 1000 }), makeRow(11, { ts: 1002.5 })];
  return p;
}

test("a paused pane exports up to its freeze, not to now", async () => {
  const p = pane({ autoscroll: false, frozenId: 77 });
  await pressExport(() => exportPane(p), "Shown");
  const q = params();
  assert.equal(q.get("id_to"), "77",
    "a paused pane must stop at the row it froze on, like the chart and the lanes do");
  assert.equal(q.get("since_ts"), String(1000 - 1e-6), "the shown window starts at the first row it holds");
  assert.equal(q.get("until_ts"), "1002.5", "and ends at the last");
});

test("a live pane sends no bound at all", async () => {
  await pressExport(() => exportPane(pane({ autoscroll: true, frozenId: 77 })), "Session");
  assert.equal(params().has("id_to"), false,
    "a live pane's frozenId is stale: sending it would export a window the pane is past");
});

test("the pane's own three filters are what the download is filtered by", async () => {
  const p = pane({ autoscroll: false, frozenId: 5 });
  await pressExport(() => exportPane(p), "Session");
  const q = params();
  assert.equal(q.get("port"), "p1");
  assert.equal(q.get("match"), "^!can ");
  assert.deepEqual(q.getAll("chan").sort(), ["debug", "event"],
    "a comma-joined chan is 422 at the daemon: /lines takes it as a repeated parameter");
  // The 422 is invisible to URLSearchParams.get(), which happily returns "debug,event", so
  // assert on the query text too.
  const query = seen.lastUrl.split("?")[1];
  assert.ok(/(^|&)chan=debug(&|$)/.test(query) && /(^|&)chan=event(&|$)/.test(query), query);
  assert.equal(query.includes("%2C"), false, "no comma-joined list anywhere in the URL");
});

test("a pane with every channel ticked sends no chan at all", async () => {
  const p = pane({ autoscroll: false, frozenId: 5 });
  p.channels = new Set(ALL_CHANS);
  p.regexSrc = ""; p.regex = null;
  await pressExport(() => exportPane(p), "Session");
  const q = params();
  assert.deepEqual(q.getAll("chan"), [], "an unfiltered pane must not narrow the export");
  assert.equal(q.has("match"), false);
});

test("a pattern the pane dropped is not sent: the export filters what the pane shows", async () => {
  for (const src of ["[", "x".repeat(201)]) {
    const p = pane({ autoscroll: false, frozenId: 5, regexSrc: src, regex: null });
    await pressExport(() => exportPane(p), "Session");
    assert.equal(params().has("match"), false,
      `a pane showing unfiltered rows under ${JSON.stringify(src.slice(0, 8))} exported a filtered set`);
  }
});

// The positive control for the empty list below: a double that had stopped applying the guards
// would leave it empty too. The pane is put in the state the drop above exists to prevent - an
// over-long pattern still compiled - which is the one URL this file can build that /lines/export
// answers 4xx to.
test("a pattern past the daemon's cap, if it were sent, is refused and recorded", async () => {
  const src = "x".repeat(201);
  const p = pane({ autoscroll: false, frozenId: 5, regexSrc: src, regex: /x/ });
  await pressExport(() => exportPane(p), "Session");
  assert.equal(params().get("match"), src, "the control did not send the over-long pattern");
  assert.deepEqual(seen.refusals.map(([, why]) => why), ["match regex too long (max 200 chars)"],
    "the guard the assertion below rests on never fired");
  seen.refusals.length = 0;   // the assertion below is about what the panels build
});

test("nothing these panels exported would be refused by the daemon", async () => {
  // W6: the double these tests run against applies the endpoints' own guards, so a URL the
  // daemon answers 4xx to (a comma-joined `chan`, an `id_to` below the floor, `changes`
  // without `decode`) fails here rather than being certified by a blanket 200.
  // Every road the file drives, so this holds run alone as well as after the tests above.
  const { chart } = pausedPastRing();
  for (const mode of ["Shown", "Session"]) await pressExport(() => exportChart(chart), mode);
  setChartPaused(chart, false);
  await pressExport(() => exportChart(chart), "Session");
  ingest("!pd 1 f:u1:/b0,b1");
  samples(1, 10, nextTick, 2);
  nextTick += 10;
  setDigitalPaused(true);
  await pressExport(exportDigital, "Shown");
  setDigitalPaused(false);
  await pressExport(exportDigital, "Session");
  await pressExport(() => exportPane(pane({ autoscroll: false, frozenId: 77 })), "Shown");
  await pressExport(() => exportPane(pane({ autoscroll: false, frozenId: 5 })), "Session");
  assert.ok(seen.lastUrl, "the suite must have built at least one export URL");
  assert.deepEqual(seen.refusals, [],
    "an export the panel builds must be one the daemon will answer");
});
