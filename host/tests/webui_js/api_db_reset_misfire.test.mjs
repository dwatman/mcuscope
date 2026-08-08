// api.js: a capture reset is the daemon's `capture` token changing, not id arithmetic.
//
// Two generations of inference lived here first. "An id arrived below the newest row the
// snapshot returned" is the ORDINARY case - rows committed between the WebSocket subscribe
// and the /lines read are delivered on the wire AND present in the snapshot - so every
// overlapping load read as a reset: the terminal buffer, CAN table, digital lanes and time
// anchors were all wiped and the backfill re-run, one to three times per page load (measured
// on a live sim: 4 loads in 6). The timestamp arm that followed covered a silent target but
// still could not see a restored backup whose ids sit higher than the ones held.
//
// SPEC 3.4 now puts the fact on the wire, so this file asserts the fact is used and the
// arithmetic is not: an overlap must be silent, and a new token must wipe even when every
// id is higher than everything held.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();

// Snapshot ids 1..10. The plot/definition seeds answer empty: this file is about the reset.
const SNAPSHOT = Array.from({ length: 10 }, (_, i) => makeRow(i + 1, { raw: `snap ${i + 1}` }));
let linesFetches = 0;

globalThis.fetch = async (url) => {
  const u = String(url);
  let body = {};
  if (u.includes("/lines?match=")) body = { lines: [] };            // !pd definition seed
  else if (u.includes("/lines")) { linesFetches += 1; body = { lines: [...SNAPSHOT].reverse() }; }
  else if (u.includes("/plot/channels")) body = { channels: [] };
  else if (u.includes("/plot/series")) body = { points: [] };
  return { ok: true, status: 200, json: async () => body };
};

const { state, buffer } = await import(webuiUrl("state.js"));
const { connectWs } = await import(webuiUrl("api.js"));

function frame(sock, rows) {
  sock.onmessage({ data: JSON.stringify(rows) });
}

test("the first capture token seen is adopted, not treated as a change", async () => {
  connectWs();
  const sock = env.sockets.at(-1);
  sock.onopen();
  await tick(0);
  await tick(0);
  await tick(0);
  assert.equal(state.maxId, 10, "the backfill did not ingest the snapshot");
  assert.equal(linesFetches, 1, "expected exactly one snapshot fetch");

  // The daemon leads every connection with its capture identity. A fresh page holds nothing
  // from any other capture, so this must not wipe the history the backfill just seeded.
  frame(sock, [{ capture: "cap-a" }]);
  await tick(0);
  assert.equal(linesFetches, 1, "the first capture token was read as a reset");
  assert.equal(buffer.length, 10, "the backfilled rows were wiped by the opening token");
});

test("a row the snapshot already carried is a duplicate, not a capture reset", async () => {
  const sock = env.sockets.at(-1);
  const seeded = buffer.length;

  // The overlap: committed before the snapshot was read, delivered on the wire after it.
  frame(sock, [makeRow(7, { raw: "snap 7" })]);
  await tick(0);

  assert.equal(state.maxId, 10, "the watermark was reset by an ordinary backfill overlap");
  assert.equal(linesFetches, 1, "the overlap re-ran the backfill, so it was read as a reset");
  assert.equal(buffer.length, seeded, "the terminal buffer was wiped by an overlap row");
});

test("ids restarting low is NOT a reset on its own", async () => {
  // Deliberately the inverse of the old heuristic. Low ids with the capture unchanged mean
  // a duplicate or a late frame, and the watermark is the whole answer.
  const sock = env.sockets.at(-1);
  frame(sock, [makeRow(11, { raw: "live 11" })]);
  await tick(0);
  assert.equal(state.maxId, 11);

  const before = linesFetches;
  frame(sock, [makeRow(1, { raw: "not a reset" })]);
  await tick(0);
  await tick(0);
  assert.equal(linesFetches, before, "low ids alone re-ran the backfill");
  assert.ok(buffer.some((r) => r.raw === "live 11"), "held rows were wiped without a new capture");
});

test("a new capture token wipes and re-seeds, even with every id higher than those held", async () => {
  // The case no id arithmetic can see: a capture restored from a backup, or one whose highest
  // id was purged and handed out again. The ids climb exactly as they always do.
  const sock = env.sockets.at(-1);
  const before = linesFetches;

  frame(sock, [{ capture: "cap-b" }, makeRow(500, { raw: "other capture" })]);
  await tick(0);
  await tick(0);
  await tick(0);

  assert.ok(linesFetches > before, "a new capture token did not re-seed from the new capture");
  assert.ok(!buffer.some((r) => r.raw === "live 11"),
    "rows from the old capture must not survive a new capture token");
});

test("a reset on a silent target is caught with nothing but a keepalive", async () => {
  // The residual hole every id-based test had: a page whose whole content came from the
  // backfill has never seen a live row, so there is no backward step to notice. A board that
  // says nothing until it is asked is exactly that page. The keepalive carries the token.
  const sock = env.sockets.at(-1);
  const before = linesFetches;
  frame(sock, [{ capture: "cap-c" }]);
  await tick(0);
  await tick(0);
  await tick(0);
  assert.ok(linesFetches > before, "a reset on a silent target went undetected");
});
