// can.js: the !can decoder, the table formatters and the CSV export.
//
// parseCanEvent mirrors protocol.parse_can_event, so the browser and the daemon must agree
// on what a valid frame is; the formatters and csvField are only ever seen through the
// rendered table, which is what this drives.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { canIngest, renderCan, canRows, clearAllCan, initCan } = await import(webuiUrl("can.js"));

let nextId = 1;
function ingest(raw, over = {}) {
  canIngest({ id: nextId++, ts: 1000, port: "p1", chan: "event", raw, ...over });
}
const reset = () => { clearAllCan(); nextId = 1; };

// The rendered table as arrays of cell text, so the formatters can be read back.
function table() {
  renderCan();
  const rows = env.byId("canWrap").querySelectorAll("tr");
  return rows.map((tr) => tr.children.map((td) => td.textContent));
}
const header = () => table()[0];
const bodyRows = () => table().slice(1);

test("a valid frame lands as one row per (port, id)", () => {
  reset();
  ingest("!can 100 - 123 DEADBEEF");
  assert.equal(canRows.size, 1);
  const e = [...canRows.values()][0];
  assert.deepEqual({ id: e.id, ext: e.ext, rtr: e.rtr, dlc: e.dlc, hex: e.hex, count: e.count },
    { id: 0x123, ext: false, rtr: false, dlc: 4, hex: "DEADBEEF", count: 1 });
  ingest("!can 200 - 123 DEADBEEF");
  assert.equal(canRows.size, 1, "the same id must update the row, not add one");
  assert.equal([...canRows.values()][0].count, 2);
});

test("flags, an 0x-prefixed id, an empty payload and RTR all decode", () => {
  reset();
  ingest("!can 100 x 0x1FFFFFFF -");
  ingest("!can 100 r 200 8");
  ingest("!can 100 xr 0x1ABCDEF 3");
  const rows = [...canRows.values()];
  assert.deepEqual(rows.map((r) => [r.id, r.ext, r.rtr, r.dlc, r.hex]), [
    [0x1FFFFFFF, true, false, 0, ""],
    [0x200, false, true, 8, ""],
    [0x1ABCDEF, true, true, 3, ""],
  ]);
});

test("a malformed frame is dropped, exactly as the daemon drops it", () => {
  reset();
  const bad = [
    "!can",                          // no fields
    "!can 100 - 123",                // too few
    "!can 100 - 123 DE AD",          // too many
    "!can x - 123 DEAD",             // non-numeric tick
    "!can 4294967296 - 123 DEAD",    // tick past 2^32
    "!can 100 q 123 DEAD",           // unknown flag
    "!can 100 - zzz DEAD",           // id not hex
    "!can 100 - 123 DEA",            // odd-length payload
    "!can 100 - 123 DEADBEEFDEADBEEF11",  // payload past 8 bytes
    "!can 100 r 123 9",              // RTR dlc past 8
    "!can 100 r 123 DEAD",           // RTR dlc must be a single digit
    "!can 100 - 123 GG",             // payload not hex
    "!candy 100 - 123 DEAD",         // not the !can event
  ];
  for (const raw of bad) ingest(raw);
  assert.equal(canRows.size, 0, "a malformed frame must not reach the table");
});

test("whitespace is collapsed the way Python str.split does", () => {
  reset();
  ingest("!can   100   -   123   DEAD  ");
  assert.equal(canRows.size, 1);
  assert.equal([...canRows.values()][0].hex, "DEAD");
  // canIngest gates on /^!can\b/ before parsing, so an indented line is not a CAN event
  // at all. The daemon stores the line as received, so this only arises from odd firmware.
  ingest("  !can 100 - 456 DEAD");
  assert.equal(canRows.size, 1);
});

test("only event rows on the !can token are decoded", () => {
  reset();
  ingest("!can 100 - 123 DEAD", { chan: "debug" });
  ingest("!can 100 - 123 DEAD", { chan: "resp" });
  assert.equal(canRows.size, 0);
  ingest("!can 100 - 123 DEAD", { chan: "event" });
  assert.equal(canRows.size, 1);
});

test("the id, data, count and period columns format as the CAN tool shows them", () => {
  reset();
  ingest("!can 100 - 7f DEADBEEF", { ts: 1000 });
  ingest("!can 200 - 7f DEADBEEF", { ts: 1000.02 });   // 20 ms apart
  ingest("!can 300 x 0x1abcdef -", { ts: 1000 });
  ingest("!can 400 r 55 4", { ts: 1000 });

  assert.deepEqual(header(), ["id", "dlc", "data", "count", "ms", "age"]);
  // Rows sort by port, then by numeric id: 0x055, 0x07F, 0x1ABCDEF.
  assert.deepEqual(bodyRows().map((r) => r.slice(0, 5)), [
    ["055rtr", "4", "remote", "1", "-"],
    ["07F", "4", "DE AD BE EF", "2", "20"],
    ["01ABCDEFext", "0", "-", "1", "-"],
  ]);
});

test("a second port adds the port column", () => {
  reset();
  ingest("!can 100 - 1 DE", { port: "a" });
  assert.deepEqual(header(), ["id", "dlc", "data", "count", "ms", "age"]);
  ingest("!can 100 - 1 DE", { port: "b" });
  assert.deepEqual(header(), ["port", "id", "dlc", "data", "count", "ms", "age"]);
  assert.deepEqual(bodyRows().map((r) => r[0]), ["a", "b"]);
});

test("an empty table renders the empty state, not a header", () => {
  reset();
  renderCan();
  assert.equal(env.byId("canWrap").textContent,
    "No CAN frames seen yet. !can events populate this live.");
  assert.equal(env.byId("canCount").textContent, "");
  ingest("!can 100 - 1 DE");
  renderCan();
  assert.equal(env.byId("canCount").textContent, "1 id");
  ingest("!can 100 - 2 DE");
  renderCan();
  assert.equal(env.byId("canCount").textContent, "2 ids");
});

test("the age column is measured on the daemon's clock, not the browser's", () => {
  reset();
  // A daemon 30 s ahead of this machine used to render as "-30000ms", coloured fresh.
  ingest("!can 100 - 1 DE", { ts: Date.now() / 1000 + 30 });
  const age = bodyRows()[0].at(-1);
  assert.match(age, /^\d+ms$/, `age read as ${age}`);
});

test("the CSV export escapes a formula-shaped field", () => {
  reset();
  initCan();
  ingest("!can 100 - 123 DEADBEEF", { port: "=cmd|calc" });
  ingest("!can 100 - 456 -", { port: "p,1" });
  env.byId("canExport").emit("click");

  const csv = env.blobs.at(-1).parts.join("");
  const lines = csv.trim().split("\n");
  assert.equal(lines[0], "port,id,ext,rtr,dlc,data,count,period_ms,age_s");
  assert.match(lines[1], /^'=cmd\|calc,123,0,0,4,DEADBEEF,1,,/,
    "a leading = must be neutralized against spreadsheet formula injection");
  assert.match(lines[2], /^"p,1",456,0,0,0,,1,,/, "a comma must be quoted");
});

test("an empty table exports nothing at all", () => {
  reset();
  const before = env.blobs.length;
  env.byId("canExport").emit("click");
  assert.equal(env.blobs.length, before, "an empty export would download an empty file");
});
