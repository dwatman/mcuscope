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

const { changedBytes, canIngest, renderCan, clearAllCan, initCan } = await import(webuiUrl("can.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();
initCan();

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
