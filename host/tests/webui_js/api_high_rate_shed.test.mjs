// The high-rate shed (api.js tickRate): what trips it, and what a pane shows while it holds.
//
// The rate is rows over the larger of the window's wall time and the capture-time span of its
// rows, so a backlog delivered at once after a stalled page reads as the rate it was captured
// at. While shedding, a live pane is rebuilt from the shared buffer once per window; a paused
// pane stays frozen.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();

globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ lines: [] }) });

const { state, buffer } = await import(webuiUrl("state.js"));
const { panes } = await import(webuiUrl("terminal.js"));
// api.js registers exactly one interval at module scope (the rate window); the stub captures
// timers rather than arming them, so the window is closed on demand.
const before = env.intervals.length;
const { connectWs } = await import(webuiUrl("api.js"));
const rateTick = env.intervals.slice(before)[0].fn;
const warn = document.getElementById("rateWarn");

let sock = null;
let nextId = 1;
let clock = 1000;   // capture time, s
let live = null;
let paused = null;

// `count` rows captured `stepS` seconds apart, delivered in one frame.
function feed(count, stepS) {
  const rows = [];
  for (let i = 0; i < count; i++) rows.push(makeRow(nextId++, { ts: (clock += stepS) }));
  sock.onmessage({ data: JSON.stringify(rows) });
}

// The newest row the pane has taken in: rendered, or queued for the next flush (the stub holds
// the flush timer, so below the shed fed rows wait in the queue).
const lastShown = (pane) => (pane.queue.at(-1) ?? pane.rows.at(-1))?.id;

async function stream() {
  buffer.length = 0;
  state.maxId = 0;
  nextId = 1;
  clock = 1000;
  panes.length = 0;
  live = makePane({ autoscroll: true });
  paused = makePane({ autoscroll: false });
  panes.push(live, paused);
  connectWs();
  sock = env.sockets.at(-1);
  sock.onopen();
  await tick(0);
  rateTick();   // a quiet window: any shed an earlier test left latched lets go
  rateTick();   // and open a fresh one
}

test("a dense burst trips the shed and names it in the badge", async () => {
  await stream();
  feed(3000, 0.0001);   // 3000 rows captured within 0.3 s
  rateTick();
  assert.equal(warn.hidden, false, "a real flood must shed");
  assert.equal(warn.textContent, "display rate limited");
});

test("a backlog captured over 3 s and delivered at once does not trip it", async () => {
  await stream();
  feed(3000, 0.001);    // 1000 lines/s for 3 s, arriving in one frame after a stall
  rateTick();
  assert.equal(warn.hidden, true, "the stall's catch-up read as a flood by arrival time");
  assert.equal(lastShown(live), 3000, "the live pane was not fed");
});

test("while shed, a live pane shows the newest line at each window, not between", async () => {
  await stream();
  feed(3000, 0.0001);
  rateTick();
  assert.equal(warn.hidden, false, "setup: the shed did not trip");
  assert.equal(lastShown(live), 3000, "the window that tripped the shed shows its newest line");
  feed(3000, 0.0001);
  assert.equal(lastShown(live), 3000, "rows reached the pane between windows: nothing was shed");
  rateTick();
  assert.equal(warn.hidden, false, "setup: the flood is still on");
  assert.equal(lastShown(live), 6000, "the live pane went blank instead of showing the newest line");
});

test("while shed, a paused pane stays frozen", async () => {
  await stream();
  feed(10, 0.01);
  rateTick();
  const frozen = paused.rows.length;
  feed(3000, 0.0001);
  rateTick();
  assert.equal(warn.hidden, false, "setup: the shed did not trip");
  const count = paused.pending;
  feed(3000, 0.0001);
  rateTick();
  assert.equal(paused.rows.length, frozen, "the once-a-second refresh moved a paused pane");
  assert.equal(paused.pending, count, "the once-a-second refresh rebuilt a paused pane");
  assert.equal(paused.autoscroll, false);
});

test("a quiet window releases the shed and refills every pane", async () => {
  await stream();
  feed(3000, 0.0001);
  rateTick();
  feed(500, 0.0001);
  rateTick();
  assert.equal(warn.hidden, false, "setup: the shed did not hold");
  rateTick();   // nothing arrived
  assert.equal(warn.hidden, true, "the shed latched on after the flood stopped");
  assert.equal(lastShown(live), 3500);
});

test("rows counted before the tab hid do not stretch the first window after it returns", async () => {
  await stream();
  feed(10, 0.01);                // counted, then the tab hides before the window closes
  document.hidden = true;
  clock += 600;                  // ten minutes hidden
  document.hidden = false;
  document.emit("visibilitychange");
  feed(3000, 0.0001);
  rateTick();
  assert.equal(warn.hidden, false, "the pre-hide rows' capture time diluted a real flood");
});

test("a frame carrying non-row elements still measures and delivers its rows", async () => {
  await stream();
  const rows = [];
  for (let i = 0; i < 3000; i++) rows.push(makeRow(nextId++, { ts: (clock += 0.0001) }));
  sock.onmessage({ data: JSON.stringify([null, { gap: 3 }, ...rows]) });
  assert.ok(buffer.length >= 3000, "an element without a ts threw and lost the frame");
  rateTick();
  assert.equal(warn.hidden, false, "the rows beside the gap object were not measured");
});
