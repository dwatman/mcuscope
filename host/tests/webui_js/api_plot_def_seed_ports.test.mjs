// api.js seedPlotDefs with many boards: the seed read the newest 50 !pd rows across every
// port, so one board's rebroadcasts crowded another board's only definition out, and that
// board's typed samples in the backfill window stayed undecoded until its next rebroadcast.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

// Board p1 declared sid 0 once, long ago; board p2 then rebroadcast its own sid 0 1100 times
// (more than one page). Both boards' samples sit in the backfill window.
const DEFS = [{ id: 1, ts: 1, port: "p1", chan: "event", raw: "!pd 0 alpha:u1" }];
for (let i = 0; i < 1100; i++) {
  DEFS.push({ id: 2 + i, ts: 2 + i, port: "p2", chan: "event", raw: "!pd 0 beta:u1 gamma:u1" });
}
const SEED = [
  { id: 5001, ts: 5001, port: "p1", chan: "event", raw: "!ps 0 10 07" },
  { id: 5002, ts: 5002, port: "p2", chan: "event", raw: "!ps 0 10 08,09" },
];

// The daemon's /lines for the !pd query: since_id exclusive, id_to inclusive, order, limit.
const defQueries = [];
globalThis.fetch = async (url) => {
  const u = new URL(String(url), "http://x");
  const q = u.searchParams;
  let body = { lines: [], channels: [], truncated: false };
  if (q.get("match")) {
    defQueries.push(q);
    const since = Number(q.get("since_id") ?? -1);
    const idTo = q.has("id_to") ? Number(q.get("id_to")) : Infinity;
    let hits = DEFS.filter((r) => r.id > since && r.id <= idTo);
    if (q.get("order") !== "asc") hits = hits.slice().reverse();
    const limit = Number(q.get("limit"));
    body = { lines: hits.slice(0, limit), truncated: hits.length > limit };
  } else if (u.pathname === "/lines") {
    body = { lines: SEED.slice().reverse(), truncated: false };
  }
  return { ok: true, status: 200, headers: { get: () => null }, json: async () => body };
};

const { charts } = await import(webuiUrl("plots.js"));
const { connectWs } = await import(webuiUrl("api.js"));

test("every board's definition is seeded, paged over the lookback and bounded by the window", async () => {
  connectWs();
  env.sockets.at(-1).onopen();
  for (let i = 0; i < 6; i++) await tick(0);

  const names = (key) => [...(charts.get(key)?.ys.keys() ?? [])];
  assert.deepEqual(names("p1|s0"), ["alpha"], "p1's sample never decoded: its definition was crowded out");
  assert.deepEqual(names("p2|s0"), ["beta", "gamma"]);

  assert.equal(defQueries.length, 2, "1101 definitions are two pages, and no more requests");
  for (const q of defQueries) {
    assert.equal(q.get("id_to"), "5001", "the search stops at the window's oldest row");
    assert.equal(q.get("order"), "asc");
  }
  assert.equal(defQueries[0].get("since_id"), "0", "floored at the lookback, not unbounded");
  assert.equal(defQueries[1].get("since_id"), "1000", "the second page continues after the first");
});
