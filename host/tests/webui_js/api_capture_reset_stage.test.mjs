// api.js: a capture reset (SPEC 3.4) and the staging area, which have to agree.
//
// `staging` exists because the /lines backfill and the live stream race: rows that arrive
// before the snapshot has been merged must wait, or they advance state.maxId and the whole
// snapshot is then dropped by the `row.id <= state.maxId` guard. The first connect had that
// discipline; the two paths here did not, and both ended with the terminal reading live while
// holding nothing.
//
//  - The re-seed after a reset re-ran the backfill with the live path wide open. The token is
//    applied synchronously mid-frame, so the new capture's very first rows - the ones sharing
//    the token's own frame - landed before the re-seed's fetch resolved and took the watermark
//    with them: every history row it returned was dropped (measured: 0 of 5 survived).
//  - The drain sorted the whole queue by id. A reset landing mid-staging leaves two id spaces
//    in one queue, so the DEAD capture's high ids sorted last, were folded into the buffer the
//    token had just wiped, and jammed the watermark at the old high-water mark. Every later row
//    of the new capture then read as a duplicate, and the token is sent once: the UI never moved
//    again.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();

// What the next /lines backfill answers, and when. `gate` holds the answer so a test can put
// frames on the wire while the fetch is still in flight, which is the whole race.
let served = [];
let gate = null;
let linesFetches = 0;

globalThis.fetch = async (url) => {
  const u = String(url);
  let body = {};
  if (u.includes("/lines?match=")) body = { lines: [] };              // !pd definition seed
  else if (u.includes("/lines")) {
    linesFetches += 1;
    if (gate) await gate;
    body = { lines: [...served].reverse() };                          // desc, as the daemon serves
  } else if (u.includes("/plot/channels")) body = { channels: [] };
  else if (u.includes("/plot/series")) body = { points: [] };
  return { ok: true, status: 200, json: async () => body };
};

const { state, buffer } = await import(webuiUrl("state.js"));
const { panes } = await import(webuiUrl("terminal.js"));
const { connectWs, reconnectStream } = await import(webuiUrl("api.js"));

panes.push(makePane());

const frame = (sock, rows) => sock.onmessage({ data: JSON.stringify(rows) });
const raws = () => buffer.map((r) => r.raw);
// Long enough for a backfill's await chain (definition seed, plot seeds, the drain) to settle.
const settle = async () => { for (let i = 0; i < 6; i++) await tick(0); };

function hold() {
  let release;
  gate = new Promise((r) => { release = r; });
  return () => { gate = null; release(); };
}

let sock = null;

test("the opening token is adopted mid-drain and the rows behind it still land", async () => {
  // The first-load variant: the token arrives while staging is holding, and a fresh page holds
  // nothing from any other capture, so it is adopted rather than treated as a change. The rows
  // behind it are the same capture's and merge normally - out of order on the wire, in id
  // order into the buffer, because within one capture the ids are one sequence.
  served = [];
  const release = hold();
  connectWs();
  sock = env.sockets.at(-1);
  sock.onopen();
  frame(sock, [{ capture: "cap-a" }, makeRow(2, { raw: "a-2" }), makeRow(1, { raw: "a-1" })]);
  assert.deepEqual(raws(), [], "rows must be held until the backfill has merged");
  release();
  await settle();

  assert.deepEqual(raws(), ["a-1", "a-2"]);
  assert.equal(state.maxId, 2);
  assert.equal(linesFetches, 1, "the adopted first token re-seeded as though it were a change");
});

test("the re-seed after a reset is not raced by the live rows in the token's own frame", async () => {
  const release = hold();
  served = [1, 2, 3, 4, 5].map((i) => makeRow(i, { raw: "hist-" + i }));

  // One frame, as the daemon sends it: the new capture's identity at the head, then the first
  // rows of that capture. noteCapture fires synchronously here, so the re-seed's fetch is in
  // flight before the rows behind the token are looked at.
  frame(sock, [{ capture: "cap-b" },
               makeRow(6, { raw: "live-6" }), makeRow(7, { raw: "live-7" })]);
  await tick(0);
  assert.equal(state.maxId, 0, "the stale watermark must be dropped by the reset");
  assert.deepEqual(raws(), [], "the token's own frame raced the re-seed instead of staging");

  release();
  await settle();

  assert.deepEqual(raws(), ["hist-1", "hist-2", "hist-3", "hist-4", "hist-5", "live-6", "live-7"],
    "the re-seeded history was dropped by a watermark the live rows had already advanced");
  assert.equal(state.maxId, 7, "the watermark must end on the newest row of the NEW capture");
});

test("a reset landing mid-staging is not sorted across", async () => {
  // The dead capture's last row is staged, then the purge happens on the daemon and the next
  // frame carries the new token and the new capture's first row - whose id is far BELOW it.
  const release = hold();
  served = [];
  reconnectStream();
  sock = env.sockets.at(-1);
  sock.onopen();
  frame(sock, [makeRow(600, { raw: "old-600" })]);
  frame(sock, [{ capture: "cap-c" }, makeRow(1, { raw: "new-1" })]);
  release();
  await settle();

  assert.deepEqual(raws(), ["new-1"],
    "a row of the dead capture survived the wipe its own token performed");
  assert.equal(state.maxId, 1,
    "the watermark jammed at the old capture's high-water mark: every new row is now a duplicate");

  // The consequence the daemon cannot correct: the token is sent once, so a jammed watermark
  // is permanent for the life of the page.
  frame(sock, [makeRow(2, { raw: "new-2" })]);
  await tick(0);
  assert.deepEqual(raws(), ["new-1", "new-2"], "the next live row of the new capture was dropped");
});

test("a malformed staged row does not cost the rows behind it", async () => {
  // The live path guards every row (see onmessage); the drain did not, so one undecodable row
  // abandoned every staged row after it - silently, and with the watermark hiding the hole.
  const release = hold();
  served = [];
  reconnectStream();
  sock = env.sockets.at(-1);
  sock.onopen();
  frame(sock, [{ id: 5, ts: 1.0, port: "p1", chan: "event" }]);   // an event line with no raw
  frame(sock, [makeRow(6, { raw: "good-6" }), makeRow(7, { raw: "good-7" })]);
  release();
  await settle();

  assert.ok(buffer.some((r) => r.raw === "good-6") && buffer.some((r) => r.raw === "good-7"),
    "a malformed staged row took the rows behind it with it");
  assert.equal(state.maxId, 7);
});
