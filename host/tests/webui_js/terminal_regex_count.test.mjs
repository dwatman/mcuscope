// terminal.js updateShown: with a regex set the footer reads "N / M lines", M being the rows in
// scope (port and channel) the pattern chooses from. Every render ran it, 30 times a second per
// live pane, and it rescanned the whole shared buffer each time. The count is now carried
// forward over the rows appended since; it must still come out equal to a full recount across a
// buffer trim, a clear, a filter change and a pause.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

installDom();
const { buffer, pushBuffer, state, BUFFER_MAX, BUFFER_SLACK } = await import(webuiUrl("state.js"));
const { panes, applyRegex, rebuild, render, scheduleFlush, setAutoscroll } = await import(webuiUrl("terminal.js"));

let reads = 0;
let nextId = 1;
// A row whose channel read is counted, so a rescan of it shows.
function countedRow(id, chan) {
  const row = makeRow(id);
  Object.defineProperty(row, "chan", { get() { reads += 1; return chan; }, enumerable: true });
  return row;
}

function fullCount(pane) {
  return buffer.filter((r) => r.id > pane.clearId && pane.channels.has(r.chan)).length;
}

function shown(pane) {
  const m = /^(\d+) \/ (\d+) lines$/.exec(pane.shownEl.textContent);
  assert.ok(m, `unexpected readout ${pane.shownEl.textContent}`);
  return { n: +m[1], of: +m[2] };
}

// Live rows through the buffer and the pane's queue, as api.js feedPanes feeds them, then one flush.
async function stream(pane, count, chan = "debug") {
  for (let i = 0; i < count; i++) {
    const row = makeRow(nextId++, { chan, raw: i % 2 ? "hit " + nextId : "miss" });
    pushBuffer(row);
    pane.fedId = row.id;
    if (pane.channels.has(chan) && row.raw.startsWith("hit")) pane.queue.push(row);
  }
  scheduleFlush();
  await tick(60);
}

function fresh() {
  buffer.length = 0; state.maxId = 0; nextId = 1; reads = 0;
  panes.length = 0;
  const pane = makePane({ autoscroll: true, viewH: 300, channels: new Set(["debug", "event"]) });
  panes.push(pane);
  for (let i = 0; i < 300; i++) pushBuffer(countedRow(nextId++, i % 3 ? "debug" : "cmd"));
  applyRegex(pane, "hit");
  rebuild(pane);
  return pane;
}

test("appended rows extend the count without reading the rows already counted", async () => {
  const pane = fresh();
  assert.equal(shown(pane).of, fullCount(pane));
  reads = 0;
  await stream(pane, 40);
  assert.equal(reads, 0, "a render re-read rows it had already counted: the whole buffer was rescanned");
  assert.equal(shown(pane).of, fullCount(pane));
  await stream(pane, 10, "cmd");   // out of scope: counted as nothing
  assert.equal(shown(pane).of, fullCount(pane));
});

test("a buffer trim, a clear and a filter change each count afresh", async () => {
  const pane = fresh();
  await stream(pane, BUFFER_MAX + BUFFER_SLACK);   // past the trim
  assert.ok(buffer[0].id > 1, "the buffer did not trim; the case proves nothing");
  assert.equal(shown(pane).of, fullCount(pane), "rows trimmed out of the buffer are still counted");

  pane.clearId = state.maxId - 7;   // what a pane clear leaves, before its rebuild
  rebuild(pane);
  await stream(pane, 20);
  assert.equal(shown(pane).of, fullCount(pane));

  pane.channels.delete("debug");
  rebuild(pane);
  assert.equal(shown(pane).of, fullCount(pane), "a channel filter change kept the old count");
});

test("a paused pane counts its frozen rows, and resuming counts the buffer again", async () => {
  const pane = fresh();
  await stream(pane, 30);
  setAutoscroll(pane, false);
  rebuild(pane);   // a re-filter while paused renders from the freeze
  const frozen = shown(pane).of;
  assert.equal(frozen, fullCount(pane));
  await stream(pane, 30);
  rebuild(pane);
  assert.equal(shown(pane).of, frozen, "a paused pane's count grew with rows past its freeze");
  setAutoscroll(pane, true);
  assert.equal(shown(pane).of, fullCount(pane));
  assert.ok(shown(pane).of > frozen);
});

test("a clear point raised under the pane (a staged clear, api.js) counts afresh", async () => {
  const pane = fresh();
  await stream(pane, 20);
  pane.clearId = state.maxId - 5;   // no rebuild: feedStaged raises it row by row
  await stream(pane, 4);
  assert.equal(shown(pane).of, fullCount(pane), "rows under the raised clear point are still counted");
});

test("a scroll render of a paused, cleared pane counts its own snapshot", async () => {
  const pane = fresh();
  pane.clearId = 100;
  rebuild(pane);
  await stream(pane, 20);
  setAutoscroll(pane, false);
  const frozen = buffer.filter((r) => r.id > pane.clearId && r.id <= pane.frozenId && pane.channels.has(r.chan)).length;
  await stream(pane, 20);
  render(pane);   // what a scroll of the paused pane runs
  assert.equal(shown(pane).of, frozen, "the count was carried over from the live buffer into the snapshot");
});
