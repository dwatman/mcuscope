// terminal.js: a paused pane's `~N` tick estimates read the anchors snapshotted at the pause
// (REVIEW class 26). The live store keeps ANCHOR_CAP anchors per port and drops the oldest, so a
// long pause at a steady board rotated it past the frozen rows and every estimate read `~-`.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow } from "./dom_stub.mjs";

installDom();
const { state, buffer, pushBuffer, tickAnchors } = await import(webuiUrl("state.js"));
await import(webuiUrl("can.js"));   // publishes the !can tick (hooks.canTick)
const { rebuild, render, setAutoscroll } = await import(webuiUrl("terminal.js"));
const { noteTickAnchor } = await import(webuiUrl("timewindow.js"));

const stamp = (pane, i) => pane.vlist.children[i].children[0].textContent;

function pausedOnEstimate() {
  buffer.length = 0;
  tickAnchors.clear();
  state.timeMode = "tick";
  state.anchorTick = null;
  pushBuffer(makeRow(10, { ts: 100, chan: "event", raw: "!can 7000 - 100 01" }));
  pushBuffer(makeRow(11, { ts: 100.25, raw: "debug line" }));
  state.maxId = 11;
  const pane = makePane();
  rebuild(pane);
  assert.equal(stamp(pane, 1), "~250", "setup: the debug line's estimate");
  setAutoscroll(pane, false);
  // A steady board for about 3 h: one anchor a second, more than the store keeps.
  for (let i = 0; i <= 10001; i++) noteTickAnchor(tickAnchors, "p1", 12 + i, 101 + i, 8000 + i * 5000);
  return pane;
}

test("a paused pane's estimate survives the anchor store rotating past it", () => {
  const pane = pausedOnEstimate();
  render(pane);
  assert.equal(stamp(pane, 1), "~250");
  const live = makePane();
  rebuild(live);
  assert.equal(stamp(live, 1), "~-", "positive control: the live store no longer holds the anchor");
  state.timeMode = "host";
});

test("a history page loaded while paused lands its anchors in the snapshot", async () => {
  const pane = pausedOnEstimate();
  pane.frozenAnchors.map.get("p1").length = 0;   // as if the pane had frozen before any anchor
  const served = [makeRow(5, { ts: 99, chan: "event", raw: "!can 5000 - 100 01" })];
  globalThis.fetch = async () => ({ ok: true, status: 200, headers: { get: () => null },
                                    json: async () => ({ lines: served, truncated: false }) });
  pane.scrollEl.scrollTop = 0;
  const { loadHistory } = await import(webuiUrl("terminal.js"));
  await loadHistory(pane);
  const i = pane.rows.findIndex((r) => r.id === 11);
  assert.equal(stamp(pane, i), "~-750", "the debug line reads the paged anchor: 5000 + 1250 - 7000");
  state.timeMode = "host";
});

test("resuming reads the live store again", () => {
  const pane = pausedOnEstimate();
  setAutoscroll(pane, true);
  assert.equal(pane.frozenAnchors, null);
  state.timeMode = "host";
});

test("a snapshot of a capture since reset is not read", () => {
  const pane = pausedOnEstimate();
  state.captureGen += 1;   // api.js resetForDbReset keeps the pane paused
  render(pane);
  assert.equal(stamp(pane, 1), "~-", "the old capture's anchors timed a row of the new one");
  state.timeMode = "host";
});

test("a paged anchor below a full snapshot is kept: the snapshot is not a ring", () => {
  const snap = new Map();
  for (let i = 0; i < 10000; i++) noteTickAnchor(snap, "p1", 100 + i, 100 + i, i * 5000);
  noteTickAnchor(snap, "p1", 5, 99, 1, Infinity);
  assert.equal(snap.get("p1")[0].id, 5);
  const ring = new Map([["p1", snap.get("p1").slice(1)]]);
  noteTickAnchor(ring, "p1", 6, 99, 1);
  assert.notEqual(ring.get("p1")[0].id, 6, "positive control: the live store drops it at once");
});
