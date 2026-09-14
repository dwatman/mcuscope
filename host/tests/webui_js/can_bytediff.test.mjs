// can.js changedBytes: which byte of a CAN payload moved since the last frame for that id.
//
// "I pressed the button, which byte moved" is the question the latest-per-id table exists to
// answer, and it is answered wrong in both directions: flagging nothing hides the change,
// flagging everything (a dlc change, a first frame) says nothing at all.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { changedBytes, canIngest, renderCan, clearAllCan, initCan, setCanPaused, setCanFilter } =
  await import(webuiUrl("can.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();
env.byId("sidebar").setAttribute("data-view", "both");   // the tick idles while CAN is hidden
const before = env.intervals.length;
initCan();
const tickFn = env.intervals.slice(before).find((t) => t.ms === 1000).fn;

test("equal payloads flag nothing, and one byte differing flags exactly that index", () => {
  assert.deepEqual(changedBytes("DEADBEEF", "DEADBEEF"), [false, false, false, false]);
  assert.deepEqual(changedBytes("DEADBEEF", "DEAD00EF"), [false, false, true, false]);
  assert.deepEqual(changedBytes("DEADBEEF", "00AD00EF"), [true, false, true, false]);
  assert.deepEqual(changedBytes("DE", "DF"), [true]);
});

test("a length change flags nothing: it is a different message, not a moved byte", () => {
  assert.deepEqual(changedBytes("DEAD", "DEADBEEF"), [false, false, false, false]);
  assert.deepEqual(changedBytes("DEADBEEF", "DEAD"), [false, false]);
});

test("a first frame and an empty payload flag nothing", () => {
  assert.deepEqual(changedBytes("", "DEAD"), [false, false]);
  assert.deepEqual(changedBytes(null, "DEAD"), [false, false]);
  assert.deepEqual(changedBytes(undefined, "DEAD"), [false, false]);
  assert.deepEqual(changedBytes("DEAD", ""), [], "an rtr row has no payload at all");
  assert.deepEqual(changedBytes("", ""), []);
});

// The rendered cell, as spans: [text, class] per byte.
function dataCell() {
  renderCan();
  const tr = env.byId("canWrap").querySelectorAll("tr")[1];
  return tr.children[2].children.map((s) => [s.textContent.trim(), s.className]);
}

test("the moved byte is highlighted in the table, and the highlight clears when it stops", () => {
  clearAllCan();
  let id = 1;
  const frame = (payload) => canIngest({ id: id++, ts: 1000 + id, port: "p1", chan: "event",
                                         raw: `!can ${id} - 100 ${payload}` });

  frame("DEADBEEF");
  assert.deepEqual(dataCell(), [["DE", "byte"], ["AD", "byte"], ["BE", "byte"], ["EF", "byte"]],
    "the first frame for an id has nothing to compare against");

  frame("DEAD00EF");
  assert.deepEqual(dataCell(), [["DE", "byte"], ["AD", "byte"], ["00", "byte chg"], ["EF", "byte"]]);

  // A tick with the same payload: the highlight marks the last move, it does not stick.
  frame("DEAD00EF");
  assert.deepEqual(dataCell().map((b) => b[1]), ["byte", "byte", "byte", "byte"],
    "an id that has gone quiet must not stay lit for the life of the page");

  frame("DEAD00E0");
  assert.deepEqual(dataCell().map((b) => b[1]), ["byte", "byte", "byte", "byte chg"]);
  clearAllCan();
});

test("an rtr row and an empty payload render their text, not an empty cell", () => {
  clearAllCan();
  canIngest({ id: 1, ts: 1000, port: "p1", chan: "event", raw: "!can 1 r 200 8" });
  renderCan();
  assert.equal(env.byId("canWrap").querySelectorAll("tr")[1].children[2].textContent, "remote");
  clearAllCan();
  canIngest({ id: 2, ts: 1000, port: "p1", chan: "event", raw: "!can 1 - 300 -" });
  renderCan();
  assert.equal(env.byId("canWrap").querySelectorAll("tr")[1].children[2].textContent, "-");
  clearAllCan();
});

// ---- per-frame diff: several frames land between two paints -----------------------------

let seq = 1;
const send = (payload, id = "100") =>
  canIngest({ id: seq++, ts: 2000 + seq / 1000, port: "p1", chan: "event", raw: `!can ${seq} - ${id} ${payload}` });
// Paints, like the tick does: each call consumes the moved mask.
const lit = () => dataCell().map((b) => b[1] === "byte chg");
// Reads the cells as they stand, without painting.
const litNow = () => env.byId("canWrap").querySelectorAll("tr")[1].children[2].children.map((b) => b.className === "byte chg");

test("a byte that changes and changes back between two paints still lights", () => {
  clearAllCan();
  send("11223344");
  renderCan();
  send("11FF3344");
  send("11223344");   // back to the painted payload: a paint-to-paint diff sees nothing
  assert.deepEqual(lit(), [false, true, false, false]);
  assert.deepEqual(dataCell().map((b) => b[0]), ["11", "22", "33", "44"]);
});

test("bytes moved in different frames all light, at any frame rate", () => {
  clearAllCan();
  send("00000000");
  renderCan();
  // A 100 Hz id between two 1 Hz paints: byte 0 counts, byte 3 moves once.
  for (let i = 1; i <= 100; i++) send((i % 256).toString(16).padStart(2, "0").toUpperCase() + "0000" + (i === 50 ? "01" : "00"));
  assert.deepEqual(lit(), [true, false, false, true],
    "only the counter byte and the byte that blipped moved; a whole-payload light says nothing");
});

test("a lit byte clears on the next tick even when the id has gone silent", () => {
  clearAllCan();
  send("AA");
  renderCan();
  send("AB");
  renderCan();
  assert.deepEqual(litNow(), [true]);
  tickFn();   // no frame since: canDirty is false, so only the lit flag can repaint it
  assert.deepEqual(litNow(), [false], "the highlight stuck on a silent bus");
});

test("a dlc change lights nothing, and the new shape diffs from its own first frame", () => {
  clearAllCan();
  send("1122334455667788");
  renderCan();
  send("FF22334455667788");   // byte 0 moves at 8 bytes...
  send("11223344");           // ...then the shape changes before the paint
  assert.deepEqual(lit(), [false, false, false, false], "a bit set at the old length lit a byte of the new one");
  send("AABBCCDD");
  send("AABBCCDE");           // same 4-byte shape as 11223344, so every byte moved
  assert.deepEqual(lit(), [true, true, true, true]);
  send("AABBCCDE");
  renderCan();
  send("AABBCCDF0102");       // 4 bytes to 6, then byte 5 moves within the new shape
  send("AABBCCDF0103");
  assert.deepEqual(lit(), [false, false, false, false, false, true]);
  send("-");                  // an empty payload is a shape of its own
  renderCan();
  assert.equal(env.byId("canWrap").querySelectorAll("tr")[1].children[2].textContent, "-");
});

test("an rtr frame between two data frames resets the diff", () => {
  clearAllCan();
  send("1122");
  renderCan();
  canIngest({ id: seq++, ts: 3000, port: "p1", chan: "event", raw: "!can 1 r 100 2" });
  send("1123");
  assert.deepEqual(lit(), [false, false], "the byte moved against a payload from before the remote frame");
});

test("an 8-byte payload paints bytes two to eight with a leading space, the fifth's and seventh's being the wrap points", () => {
  clearAllCan();
  send("0102030405060708");
  renderCan();
  assert.deepEqual(dataCell().map((b) => b[0]), ["01", "02", "03", "04", "05", "06", "07", "08"]);
  const spans = env.byId("canWrap").querySelectorAll("tr")[1].children[2].children;
  assert.deepEqual(spans.map((b) => b.textContent.startsWith(" ")), [false, true, true, true, true, true, true, true],
    "style.css lets the line wrap only at the fifth and seventh bytes' spaces; the spaces keep a copied payload readable");
});

test("a row revealed by the filter lights nothing that moved while it was hidden", () => {
  clearAllCan();
  send("11223344", "200");
  send("AABBCCDD", "100");
  renderCan();
  setCanFilter("200");
  send("AABBCC00", "100");   // 0x100 changes while filtered out
  renderCan();
  setCanFilter("");
  const row100 = env.byId("canWrap").querySelectorAll("tr")[1];
  assert.equal(row100.children[0].textContent, "100");
  assert.deepEqual(row100.children[2].children.map((b) => b.className === "byte chg"), [false, false, false, false],
    "the hidden row kept its mask and lit on reveal");
  clearAllCan();
});

// ---- pause and resume ------------------------------------------------------------------

test("a paused table keeps the highlight it froze with, through frames, ticks and a rebuild", () => {
  clearAllCan();
  send("11223344");
  renderCan();
  send("11FF3344");
  setCanPaused(true);   // paints the snapshot: byte 1 moved since the last paint
  assert.deepEqual(litNow(), [false, true, false, false]);
  for (let i = 0; i < 20; i++) send(`${i % 2 ? "AA" : "BB"}CC${i % 2 ? "DD" : "EE"}44`);
  renderCan();
  assert.deepEqual(lit(), [false, true, false, false], "live frames churned the frozen highlight");
  assert.deepEqual(dataCell().map((b) => b[0]), ["11", "FF", "33", "44"]);
  renderCan();
  assert.deepEqual(lit(), [false, true, false, false], "a second paint of the frozen table consumed its mask");
  const table = env.byId("canWrap").children[0];
  setCanFilter("1");    // a rebuild while paused: fresh cells, same frozen mask
  assert.notEqual(env.byId("canWrap").children[0], table, "the filter did not rebuild the table");
  assert.deepEqual(litNow(), [false, true, false, false], "the rebuilt paused table lost its highlight");
  setCanFilter("");
  setCanPaused(false);
});

test("resuming does not light what moved while the table was frozen", () => {
  clearAllCan();
  send("11223344");
  renderCan();
  setCanPaused(true);
  send("FFFFFFFF");
  send("EEEEEEEE");
  setCanPaused(false);   // paints the live payload
  assert.deepEqual(litNow(), [false, false, false, false], "resume lit every byte that moved during the pause");
  assert.deepEqual(dataCell().map((b) => b[0]), ["EE", "EE", "EE", "EE"]);
  send("EEEE00EE");
  assert.deepEqual(lit(), [false, false, true, false], "the first frame after resume diffs normally");
  clearAllCan();
});
