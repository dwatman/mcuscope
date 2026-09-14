// timewindow.js estimateTick: under the tick time base a line with no tick of its own (debug,
// cmd, resp, sys, a marker without @tick) read "-". It now reads "~" plus an estimate from its
// port's nearest earlier tick line; these try to make that estimate lie.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";
import { newTickAnchors, noteTickAnchor, estimateTick } from "../../mcuscope/webui/timewindow.js";

installDom();

const dbg = (id, ts, port = "p1") => ({ id, ts, port, chan: "debug", raw: "boot" });

test("no anchor yet: no estimate", () => {
  assert.equal(estimateTick(newTickAnchors(), dbg(5, 100)), null);
});

test("an anchor from another port is never used", () => {
  const a = newTickAnchors();
  noteTickAnchor(a, "p2", 1, 100, 5000);
  assert.equal(estimateTick(a, dbg(5, 101, "p1")), null, "two boards run two clocks");
  assert.equal(estimateTick(a, dbg(5, 101, "p2")), 6000);
});

test("an anchor after the line is not used either", () => {
  const a = newTickAnchors();
  noteTickAnchor(a, "p1", 10, 100, 5000);
  assert.equal(estimateTick(a, dbg(9, 99.5)), null, "only an earlier tick line anchors a line");
  assert.equal(estimateTick(a, dbg(11, 101.25)), 6250, "its tick plus the host gap in ms");
});

test("a reset (a later anchor with a smaller tick) re-times only the lines after it", () => {
  const a = newTickAnchors();
  noteTickAnchor(a, "p1", 10, 100, 50000);
  noteTickAnchor(a, "p1", 20, 110, 20);          // the MCU rebooted
  assert.equal(estimateTick(a, dbg(15, 105)), 55000, "before the reboot: the old clock");
  assert.equal(estimateTick(a, dbg(25, 110.5)), 520, "after it: the new clock");
});

test("a reset inside the thinning gap is still kept", () => {
  const a = newTickAnchors();
  noteTickAnchor(a, "p1", 1, 100, 1000);
  noteTickAnchor(a, "p1", 2, 100.5, 1500);       // continues the clock: may be skipped
  noteTickAnchor(a, "p1", 3, 100.7, 10);         // reset 0.2 s later: must not be
  assert.equal(estimateTick(a, dbg(4, 100.8)), 110);
  noteTickAnchor(a, "p1", 5, 100.9, 2000);       // jumps forward (a second board swapped in)
  assert.equal(estimateTick(a, dbg(6, 101)), 2100);
});

test("anchors arriving out of order (a history page below the live rows) slot in by id", () => {
  const a = newTickAnchors();
  noteTickAnchor(a, "p1", 500, 200, 90000);      // live
  noteTickAnchor(a, "p1", 100, 150, 40000);      // paged in later, older
  noteTickAnchor(a, "p1", 100, 150, 40000);      // the same page again
  assert.equal(estimateTick(a, dbg(300, 160)), 50000, "the paged anchor, not the newer live one");
  assert.equal(estimateTick(a, dbg(50, 140)), null, "still nothing before the oldest anchor");
  assert.equal(estimateTick(a, dbg(600, 201)), 91000);
});

test("the estimate wraps at 2^32 as the tick does", () => {
  const a = newTickAnchors();
  noteTickAnchor(a, "p1", 1, 100, 2 ** 32 - 100);
  assert.equal(estimateTick(a, dbg(2, 100.3)), 200);
});

test("a row that is not a captured line gets no estimate", () => {
  const a = newTickAnchors();
  noteTickAnchor(a, "p1", 1, 100, 1000);
  assert.equal(estimateTick(a, { ts: 101, port: "p1", chan: "debug", raw: "x" }), null, "no id");
  assert.equal(estimateTick(a, { id: 2, ts: NaN, port: "p1", chan: "debug", raw: "x" }), null);
});

// ---- wiring: ingest, history pages and the column (the capture reset: api_db_reset_misfire) --

let served = [];
globalThis.fetch = async () => ({
  ok: true, status: 200, headers: { get: () => null },
  json: async () => ({ lines: served, truncated: false }),
});

const { state, buffer, pushBuffer, tickAnchors } = await import(webuiUrl("state.js"));
const { loadHistory, rebuild } = await import(webuiUrl("terminal.js"));

function column(pane) {
  return pane.vlist.children.map((ln) => ln.children[0].textContent);
}

test("ingested tick lines anchor the column; the estimate is marked and zeroed like a tick", () => {
  buffer.length = 0; tickAnchors.clear();
  state.maxId = 0; state.anchorTs = null; state.anchorTick = null; state.timeMode = "tick";
  pushBuffer(makeRow(1, { ts: 100, raw: "sim boot" }));
  pushBuffer(makeRow(2, { ts: 100.5, chan: "event", raw: "!can 7000 - 100 -" }));
  pushBuffer(makeRow(3, { ts: 101.25, raw: "temp=25" }));
  pushBuffer(makeRow(4, { ts: 101.5, chan: "marker", raw: "!m host mark" }));
  pushBuffer(makeRow(5, { ts: 101.6, port: "p2", raw: "other board" }));
  const pane = makePane();
  pane.rows = buffer.slice();
  rebuild(pane);
  assert.deepEqual(column(pane), ["~-", "0", "~750", "~1000", "~-"],
    "before any anchor, a tick line, a debug line, a marker without @tick, another port");
  state.timeMode = "host";
});

test("a history page's tick lines anchor the older lines it brings", async () => {
  buffer.length = 0; tickAnchors.clear();
  state.maxId = 1000; state.anchorTick = 0; state.timeMode = "tick";
  const pane = makePane({ autoscroll: false });
  pane.rows = [makeRow(900, { ts: 300, raw: "live line" })];
  served = [makeRow(899, { ts: 250.5, raw: "old debug" }),
            makeRow(898, { ts: 250, chan: "event", raw: "!p 4000 v=1" })];
  await loadHistory(pane);
  await tick(0);
  assert.deepEqual(column(pane), ["4000", "~4500", "~54000"],
    "the paged !p line anchors both its own page and the live line above it");
  state.timeMode = "host";
});
