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

const { canIngest, renderCan, canRows, clearAllCan, initCan, csvField } =
  await import(webuiUrl("can.js"));

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
    // Out of range for the flags the frame itself carries. The daemon keeps these as
    // generic events with no can_frames row, so a table that showed them disagreed with
    // GET /can/frames and `mcu can` about the same line.
    "!can 100 - 800 DEAD",           // 0x800 past the 11-bit standard id
    "!can 100 r 800 4",              // and on the RTR path
    "!can 100 x 20000000 DEAD",      // past the 29-bit extended id
    "!can 100 - 11111111111111111 DEAD",   // past parse_hex_int's 16-digit cap
    // Past protocol.is_decimal_token's 20-digit cap. Zero-padded on purpose: the token is
    // numerically 0, so the 2^32 range check accepts it and the length is the only clause
    // rejecting it - which is exactly how the two earlier misses in this mirror happened.
    "!can " + "0".repeat(21) + " - 123 DEAD",
  ];
  for (const raw of bad) ingest(raw);
  assert.equal(canRows.size, 0, "a malformed frame must not reach the table");
});

test("the tick digit cap discriminates at the bound, not past it", () => {
  reset();
  ingest("!can " + "0".repeat(20) + " - 123 DEAD");   // 20 digits: inside the daemon's cap
  assert.equal(canRows.size, 1, "a tick at the cap is legal and must decode");
});

test("an id is accepted with either spelling of the 0x prefix, as parse_hex_int is", () => {
  // protocol.parse_hex_int strips text[:2] in ("0x", "0X"); the mirror was lowercase-only,
  // so the sidebar silently dropped a frame GET /can/frames and `mcu can` both had.
  reset();
  ingest("!can 100 - 0X123 DEAD");
  ingest("!can 200 x 0X1ABCDEF -");
  assert.deepEqual([...canRows.values()].map((r) => r.id), [0x123, 0x1ABCDEF]);
});

test("the id range is the daemon's, per frame, not a single limit", () => {
  reset();
  ingest("!can 100 - 7FF DEAD");        // the largest standard id
  ingest("!can 100 x 1FFFFFFF DEAD");   // the largest extended id
  assert.deepEqual([...canRows.values()].map((r) => r.id), [0x7FF, 0x1FFFFFFF]);
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

test("a second port adds divider rows, not a port column", () => {
  reset();
  ingest("!can 100 - 1 DE", { port: "a" });
  assert.deepEqual(header(), ["id", "dlc", "data", "count", "ms", "age"]);
  assert.equal(bodyRows().length, 1, "a single group has no divider");
  ingest("!can 100 - 1 DE", { port: "b" });
  assert.deepEqual(header(), ["id", "dlc", "data", "count", "ms", "age"]);
  assert.deepEqual(bodyRows().map((r) => r[0]), ["\u25BE a CAN1", "001", "\u25BE b CAN1", "001"]);
});

// The event name carries the bus (SPEC 2.5). These mirror protocol.parse_can_event, so a
// spelling the daemon files as a frame must land here too, and one it does not must not.
test("the bus digit in the event name decodes; can1 is bus 1; can0 and can22 are not buses", () => {
  reset();
  ingest("!can 100 - 100 DE");
  ingest("!can1 100 - 101 DE");
  ingest("!can2 100 - 102 DE");
  ingest("!can9 100 x 0C0012 DE");
  assert.deepEqual([...canRows.values()].map((r) => [r.bus, r.id]),
    [[1, 0x100], [1, 0x101], [2, 0x102], [9, 0xC0012]]);
  for (const bad of ["!can0 100 - 1 DE", "!can22 100 - 1 DE", "!canx 100 - 1 DE", "!can 100 - 1 DE extra"]) {
    ingest(bad);
  }
  assert.equal(canRows.size, 4, "a non-bus spelling must not make a row");
  ingest("!can2 200 - 100 DE");
  assert.equal(canRows.size, 5, "the same id on another bus is another row");
});

test("a second bus gets a divider, sorts after bus 1, and its rows carry the tint attribute", () => {
  reset();
  ingest("!can2 100 - 610 DE");
  ingest("!can 100 - 100 DE");
  ingest("!can2 100 - 611 DE");
  assert.deepEqual(bodyRows().map((r) => r[0]),
    ["\u25BE p1 CAN1", "100", "\u25BE p1 CAN2", "610", "611"]);
  const trs = env.byId("canWrap").querySelectorAll("tr").slice(1);
  assert.deepEqual(trs.map((tr) => tr.dataset.bus ?? null), [null, null, null, "2", "2"],
    "bus 1 rows and dividers are untinted; bus 2 rows are tinted");
});

test("clicking a divider collapses its group to the divider and persists the choice", () => {
  reset();
  env.localStorage.clear();
  ingest("!can 100 - 100 DE");
  ingest("!can2 100 - 610 DE");
  ingest("!can2 100 - 611 DE");
  renderCan();
  let trs = env.byId("canWrap").querySelectorAll("tr");
  trs[3].click();   // the CAN2 divider
  assert.deepEqual(bodyRows().map((r) => r[0]), ["\u25BE p1 CAN1", "100", "\u25B8 p1 CAN2 (2 ids)"]);
  assert.deepEqual(JSON.parse(env.localStorage.getItem("canCollapsed")), ["p1 CAN2"]);
  assert.equal(env.byId("canCount").textContent, "3 ids", "the count is of the model, not the view");
  renderCan();
  trs = env.byId("canWrap").querySelectorAll("tr");
  trs[3].click();
  assert.deepEqual(bodyRows().map((r) => r[0]), ["\u25BE p1 CAN1", "100", "\u25BE p1 CAN2", "610", "611"]);
  assert.deepEqual(JSON.parse(env.localStorage.getItem("canCollapsed")), []);
  // A saved choice applies to a fresh table, and garbage in storage is ignored.
  env.localStorage.setItem("canCollapsed", JSON.stringify(["p1 CAN1"]));
  ingest("!can 100 - 101 DE");
  assert.deepEqual(bodyRows().map((r) => r[0]), ["\u25B8 p1 CAN1 (2 ids)", "\u25BE p1 CAN2", "610", "611"]);
  env.localStorage.setItem("canCollapsed", "{not json");
  ingest("!can 100 - 102 DE");
  assert.equal(bodyRows().length, 7, "2 dividers, 3 CAN1 ids and 2 CAN2 ids: garbage means nothing collapsed");
  env.localStorage.clear();
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

test("clearing the table clears the age clock with it", () => {
  // clearAllCan is the capture-reset path too (api.js resetForDbReset), and a new capture's
  // timestamps can start well below the old one's - a fresh DB, or a daemon restart against a
  // board that re-zeroed. The anchor only ever moves forward, so keeping it left canNow() in
  // the old capture's future and every frame of the new capture rendered aged by the whole
  // gap, for good. state.js re-zeroes anchorTs/anchorTick in this same reset path.
  reset();
  ingest("!can 100 - 1 DE", { ts: Date.now() / 1000 + 3600 });   // an hour ahead
  reset();
  ingest("!can 100 - 1 DE", { ts: Date.now() / 1000 });          // the new capture
  const age = bodyRows()[0].at(-1);
  assert.match(age, /^\d+ms$/, `age read as ${age}`);
  assert.ok(Number(age.replace("ms", "")) < 1000,
    `the cleared table kept the old capture's clock: age read as ${age}`);
});

test("the CSV export escapes a formula-shaped field", () => {
  reset();
  initCan();
  ingest("!can 100 - 123 DEADBEEF", { port: "=cmd|calc" });
  ingest("!can 100 - 456 -", { port: "p,1" });
  env.byId("canExport").emit("click");

  const csv = env.blobs.at(-1).parts.join("");
  const lines = csv.trim().split("\n");
  assert.equal(lines[0], "port,bus,id,ext,rtr,dlc,data,count,period_ms,age_s");
  assert.match(lines[1], /^'=cmd\|calc,1,123,0,0,4,DEADBEEF,1,,/,
    "a leading = must be neutralized against spreadsheet formula injection");
  assert.match(lines[2], /^"p,1",1,456,0,0,0,,1,,/, "a comma must be quoted");
});

test("an empty table exports nothing at all", () => {
  reset();
  const before = env.blobs.length;
  env.byId("canExport").emit("click");
  assert.equal(env.blobs.length, before, "an empty export would download an empty file");
});

test("a collapsed group is still exported, and bus is always a CSV column", () => {
  reset();
  env.localStorage.setItem("canCollapsed", JSON.stringify(["p1 CAN2"]));
  ingest("!can 100 - 100 DE");
  ingest("!can2 100 - 610 DEAD");
  renderCan();
  env.byId("canExport").emit("click");
  const csv = env.blobs.at(-1).parts.join("");
  const lines = csv.trim().split("\n");
  assert.equal(lines[0], "port,bus,id,ext,rtr,dlc,data,count,period_ms,age_s");
  assert.deepEqual(lines.slice(1).map((l) => l.split(",").slice(0, 3)),
    [["p1", "1", "100"], ["p1", "2", "610"]]);
  env.localStorage.clear();
});

test("csvField matches the daemon's _csv_cell rule for rule", async () => {
  // The same fixture test_security.py asserts against server.py _csv_cell, so a rule
  // changed on either side fails the other. Regenerate the file from _csv_cell.
  const { readFile } = await import("node:fs/promises");
  const cases = JSON.parse(
    await readFile(new URL("../csv_cell_cases.json", import.meta.url), "utf-8"));
  assert.ok(cases.length >= 10, "the fixture went missing or was emptied");
  for (const [value, expected] of cases) {
    assert.equal(csvField(value), expected, `input ${JSON.stringify(value)}`);
  }
});
