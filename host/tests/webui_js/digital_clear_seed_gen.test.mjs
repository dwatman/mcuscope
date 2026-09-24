// digital.js: a digital-only clear must move the seed generation api.js's backfill gate
// reads (plots.js plotSeedGen). The lanes are fed through plotIngest, so before the fix they
// were covered only because clearAllDigital is never called without clearAllCharts beside it -
// an unstated coupling, and a refill of the lanes with what was cleared if it ever broke.
//
// digital.js is imported BEFORE plots.js on purpose. plots.js owns the token and registers the
// bump (onSeedBump), because a static import the other way is a cycle: plots.js calls into
// digital.js at its top level, so evaluating from the digital side would read a binding still in
// its TDZ. This order is what fails if that cycle is reintroduced.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();

const D = await import(webuiUrl("digital.js"));
const P = await import(webuiUrl("plots.js"));
const { state } = await import(webuiUrl("state.js"));

let nextId = 0;
const hex = (n) => n.toString(16).padStart(8, "0");
function row(raw, ts) {
  state.maxId = ++nextId;
  P.plotIngest({ id: nextId, ts, port: "p1", chan: "event", raw });
}

// One analog stream and one bit lane, so both surfaces hold something to clear.
function stream() {
  row("!pd 0 v:u2 f:u1:/b0", 99);
  for (let i = 0; i < 4; i++) row(`!ps 0 ${hex(1000 + i * 50)} 0001,0${i % 2}`, 100 + i * 0.1);
}

test("clearAllDigital alone moves the seed generation; ingest alone does not", () => {
  const born = P.plotSeedGen();
  stream();
  assert.ok(D.digitalLanes.size > 0, "the fixture built no lane, so nothing was cleared below");
  assert.equal(P.plotSeedGen(), born,
    "capturing samples must not invalidate a seed: only a clear does");
  D.clearAllDigital();   // alone: no clearAllCharts beside it, as api.js resetForDbReset pairs them
  assert.notEqual(P.plotSeedGen(), born,
    "a digital-only clear left the gate open, so a backfill in flight refills the lanes");
  assert.equal(D.digitalLanes.size, 0, "the clear must still empty the panel");
});

test("each clear moves it again, so two clears during one backfill cannot alias", () => {
  const a = P.plotSeedGen();
  D.clearAllDigital();
  const b = P.plotSeedGen();
  assert.notEqual(b, a, "an empty panel still clears: the gate is about the seed, not the lanes");
  D.clearAllDigital();
  assert.notEqual(P.plotSeedGen(), b);
});
