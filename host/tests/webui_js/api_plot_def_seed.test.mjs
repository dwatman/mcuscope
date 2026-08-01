// api.js: the backfill must seed the !pd definitions its own window does not carry.
//
// A typed !ps sample is undecodable until its !pd has been seen. The first-connect seed is
// the newest 200 rows - about 2 s of a 20 Hz capture - while a target rebroadcasts !pd only
// every few seconds, so the definition usually sits just outside the window. Measured
// against the simulator on 2026-08-01: a load whose window caught no !pd decoded 0 of 122
// typed samples, and the typed and digital charts came up empty while the ad-hoc chart,
// which carries its own names and needs no definition, was full. The owner saw exactly that
// in the browser ("2 cycles of sine, 4 points on the other graph, one sample on digital").
//
// The store is not at fault: it held every channel at an identical count. This is the
// client's decode, so the fix and this test are both client-side.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

// The seed window: typed samples for stream 0, and one ad-hoc !p, but NO !pd - exactly the
// case that shipped broken.
const SEED_ROWS = [
  { id: 4, ts: 1004, port: "p1", chan: "event", raw: "!ps 0 3E9 0178,0BF3,BF186B13" },
  { id: 3, ts: 1003, port: "p1", chan: "event", raw: "!ps 0 3E8 02EF,0BC4,BEF1001B" },
  { id: 2, ts: 1002, port: "p1", chan: "event", raw: "!p 3E8 sine=0.5000" },
];
// The definition lives further back in the capture, reachable only by the seeding query.
const DEF_ROWS = [
  { id: 1, ts: 1001, port: "p1", chan: "event", raw: "!pd 0 tri:s2*0.01:V ramp:u2 ftest:f4" },
];

const seen = [];
globalThis.fetch = async (url) => {
  seen.push(String(url));
  const isDefQuery = String(url).includes("match=");
  return {
    ok: true,
    status: 200,
    json: async () => ({ lines: isDefQuery ? DEF_ROWS : SEED_ROWS }),
  };
};

const { charts, plotIngest } = await import(webuiUrl("plots.js"));
const { connectWs } = await import(webuiUrl("api.js"));

void plotIngest;   // imported so the module graph matches the live one

test("the backfill seeds !pd definitions its own window does not carry", async () => {
  connectWs();
  const sock = env.sockets.at(-1);
  assert.ok(sock, "connectWs did not open a socket");
  sock.onopen();
  await tick(0);
  await tick(0);

  const defQueries = seen.filter((u) => u.includes("match="));
  assert.equal(defQueries.length, 1, `expected one !pd seeding query, got ${seen.join(" | ")}`);

  // Bounded: an unbounded regex scan over a capture with no !pd at all costs a full table
  // scan on every page load (170 ms over 169k lines, and linear from there).
  assert.match(defQueries[0], /since_id=\d+/,
    "the !pd search must be anchored, or a capture with no plot streams full-scans");
  assert.match(defQueries[0], /limit=\d+/);

  // The payoff: stream 0's typed samples decoded, which without the seed they cannot.
  const names = [];
  for (const c of charts.values()) names.push(...c.ys.keys());
  for (const want of ["tri", "ramp", "ftest"]) {
    assert.ok(names.includes(want),
      `channel ${want} never decoded: the typed chart is empty while ad-hoc is full `
      + `(saw ${JSON.stringify(names)})`);
  }

  // Discrimination: the samples really carry values, rather than the channel merely
  // existing with an empty array.
  let points = 0;
  for (const c of charts.values()) {
    for (const arr of c.ys.values()) points += arr.filter((v) => v !== null).length;
  }
  assert.ok(points >= 6, `expected the two typed samples decoded across 3 channels, got ${points}`);
});
