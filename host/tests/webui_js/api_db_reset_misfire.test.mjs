// api.js: a backfill/live overlap must not read as a capture reset.
//
// The daemon commits rows continuously, so rows committed between the WebSocket subscribe and
// the /lines snapshot read are delivered on the wire AND present in the snapshot. That overlap
// is ordinary and the dedup guard exists for it.
//
// The reset heuristic sat above that guard and compared against `lastWsId`, which the backfill
// was feeding with HTTP-fetched ids. So the test meant "an id arrived below the newest row the
// snapshot returned" rather than "the wire went backward", and every overlapping load was read
// as a capture reset: the terminal buffer, CAN table, digital lanes and time anchors were all
// wiped and the backfill re-run, one to three times per page load. Measured on a live sim: 4
// loads in 6 wiped, and the chart history seed was the visible casualty (it applied, then the
// wipe cleared it, then the re-run skipped because live rows had refilled the charts).

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

test("a row the snapshot already carried is a duplicate, not a capture reset", async () => {
  connectWs();
  const sock = env.sockets.at(-1);
  sock.onopen();
  await tick(0);
  await tick(0);
  await tick(0);
  assert.equal(state.maxId, 10, "the backfill did not ingest the snapshot");
  assert.equal(linesFetches, 1, "expected exactly one snapshot fetch");
  const seeded = buffer.length;

  // The overlap: committed before the snapshot was read, delivered on the wire after it.
  frame(sock, [makeRow(7, { raw: "snap 7" })]);
  await tick(0);

  assert.equal(state.maxId, 10, "the watermark was reset by an ordinary backfill overlap");
  assert.equal(linesFetches, 1, "the overlap re-ran the backfill, so it was read as a reset");
  assert.equal(buffer.length, seeded, "the terminal buffer was wiped by an overlap row");
});

test("the wire genuinely going backward is still a capture reset", async () => {
  const sock = env.sockets.at(-1);
  // A live row above the snapshot, so the wire has a high-water mark of its own.
  frame(sock, [makeRow(11, { raw: "live 11" })]);
  await tick(0);
  assert.equal(state.maxId, 11);

  // Now the daemon's capture is replaced and ids restart low.
  const before = linesFetches;
  frame(sock, [makeRow(1, { raw: "after the reset" })]);
  await tick(0);
  await tick(0);
  await tick(0);
  // Asserted on the reset itself, not on maxId afterwards: the reset re-seeds from the new
  // capture, and this fixture answers that re-seed with the same snapshot, so the watermark
  // legitimately climbs again straight away.
  assert.ok(linesFetches > before, "a real reset must re-seed the terminal from the new capture");
  assert.ok(!buffer.some((r) => r.raw === "live 11"),
    "rows from the old capture must not survive a real reset");
});
