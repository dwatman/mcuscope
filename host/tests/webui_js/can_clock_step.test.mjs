// can.js: a host wall clock stepping back (an NTP step, a manual change) is a restart of the
// table's time, as it is for the charts and lanes (SPEC 9.2, owner ruling O1 option (b)).
//
// The age anchor only moved forward, so after a step back of S seconds every age read about S
// and a periodic id still sending showed red "dead" until a reload; and eviction at the id cap
// took the smallest stamp, which after the step is the ids still sending.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
const { state } = await import(webuiUrl("state.js"));
const C = await import(webuiUrl("can.js"));
env.byId("sidebar").setAttribute("data-view", "both");
C.initCan();

let id = 0;
const frame = (cid, ts) => {
  state.maxId = ++id;
  C.canIngest({ id, ts, port: "p1", chan: "event", raw: `!can ${id} - ${cid.toString(16)} 0${id % 10}` });
};
const rowFor = (cid) => [...C.canRows.values()].find((e) => e.id === cid);
const T = 100000;

test("a periodic id still sending after a step back reads fresh, with its period intact", () => {
  C.clearAllCan();
  for (let i = 0; i < 20; i++) frame(0x100, T + i * 0.1);
  for (let i = 0; i < 20; i++) frame(0x100, T - 3600 + i * 0.1);   // the clock steps back an hour
  const e = rowFor(0x100);
  const age = C.canNow() - e.lastTs;
  assert.ok(age >= 0 && age < 1, `age ${age} s: the step was read as silence`);
  assert.equal(C.canAgeClass(age, e.period, C.canPeriodic(e)), "age-fresh");
  assert.ok(Math.abs(e.period - 100) < 1, `period ${e.period} ms across the step`);
});

test("an id that went silent before the step keeps its true age, never a negative one", () => {
  C.clearAllCan();
  frame(0x200, T);                       // then silent
  for (let i = 0; i < 30; i++) frame(0x100, T + 1 + i * 0.1);
  frame(0x100, T - 3600 + 4);            // step back an hour, 0.1 s after the last frame
  const age = C.canNow() - rowFor(0x200).lastTs;
  assert.ok(age > 3.5 && age < 5, `silent id age ${age} s`);
});

test("a /status reading behind the anchor by more than the slack re-anchors a silent bus", () => {
  C.clearAllCan();
  for (let i = 0; i < 20; i++) frame(0x100, T + i * 0.1);
  // The daemon's clock, stepped back an hour, read at what is still the same instant here.
  C.noteDaemonNow(T + 1.9 - 3600);
  assert.ok(Math.abs(C.canNow() - (T + 1.9 - 3600)) < 0.5, "the table kept the pre-step clock");
  const age = C.canNow() - rowFor(0x100).lastTs;
  assert.ok(age >= 0 && age < 0.5, `age ${age} s after a stepped /status`);
});

test("a row behind the newest by less than the slack is a reordered burst, not a step", () => {
  C.clearAllCan();
  frame(0x100, T);
  frame(0x101, T - 5);                   // another port's queue: an earlier stamp, a later id
  assert.equal(rowFor(0x100).lastTs, T, "a 5 s reorder must not shift the table");
});

test("at the id cap, eviction after a step back takes the silent ids, not the live ones", () => {
  C.clearAllCan();
  for (let i = 0; i < 256; i++) frame(0x200 + i, T + i * 0.001);   // 256 ids, then quiet
  for (let k = 0; k < 10; k++) { frame(0x100, T - 3600 + k * 0.1); frame(0x101, T - 3600 + k * 0.1 + 0.05); }
  assert.ok(rowFor(0x100) && rowFor(0x101), "a live id was evicted in favour of a silent one");
  assert.equal(rowFor(0x100).count, 10);
});

test("a reconnect backfill after a /status reading is not a step back", () => {
  // The socket was down 30 s while capture went on; a status poll landed first and moved the
  // table's clock to the daemon's now. The backfill's rows are older than that reading but
  // newer than every row the table holds: the clock never moved.
  C.clearAllCan();
  frame(0x200, T);                        // then silent
  for (let i = 0; i < 10; i++) frame(0x100, T + i * 0.1);
  C.noteDaemonNow(T + 32);
  for (let i = 0; i < 10; i++) frame(0x100, T + 2 + i * 0.1);
  assert.equal(rowFor(0x200).lastTs, T, "the silent id was moved by a step that never happened");
  assert.ok(Math.abs(rowFor(0x100).period - 100) < 300, `period ${rowFor(0x100).period} ms`);
});

test("a step caught by /status is not applied again by the next frame", () => {
  C.clearAllCan();
  frame(0x200, T);                        // then silent
  for (let i = 0; i < 20; i++) frame(0x100, T + 1 + i * 0.1);
  C.noteDaemonNow(T + 3 - 3600);          // the step, seen first by the status poll
  frame(0x100, T + 3.1 - 3600);           // then the bus carries on
  const age = C.canNow() - rowFor(0x200).lastTs;
  assert.ok(age > 2.5 && age < 4, `silent id age ${age} s: the step was taken twice`);
  assert.ok(Math.abs(rowFor(0x100).period - 100) < 100, `period ${rowFor(0x100).period} ms`);
});
