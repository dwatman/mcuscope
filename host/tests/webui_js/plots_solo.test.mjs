// Alt-click on a channel or a lane name shows only that one (SPEC 9.2), and again shows all.
//
// The decision itself is DOM-free (chrome.js soloShow, re-exported by plots.js); the two
// wirings are driven through the real legend and the real lane gutter, because "the event
// never reaches the branch" is the failure this feature actually has.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { state } = await import(webuiUrl("state.js"));
const { charts, plotIngest, soloShow } = await import(webuiUrl("plots.js"));
const { digitalLanes } = await import(webuiUrl("digital.js"));

const showMap = (...pairs) => new Map(pairs);

test("soloing from all-shown leaves exactly one", () => {
  const names = ["a", "b", "c"];
  const out = soloShow(names, showMap(["a", true], ["b", true], ["c", true]), "b");
  assert.deepEqual([...out], [["a", false], ["b", true], ["c", false]]);
});

test("soloing the channel that is already alone shows them all again", () => {
  const names = ["a", "b", "c"];
  const out = soloShow(names, showMap(["a", false], ["b", true], ["c", false]), "b");
  assert.deepEqual([...out], [["a", true], ["b", true], ["c", true]],
    "without this the solo is a one-way trip and every channel has to be clicked back on");
});

test("soloing a hidden channel shows only it", () => {
  const names = ["a", "b", "c"];
  const out = soloShow(names, showMap(["a", true], ["b", false], ["c", true]), "b");
  assert.deepEqual([...out], [["a", false], ["b", true], ["c", false]]);
});

test("another channel being the sole one shown is not the same as this one", () => {
  // The "already alone" branch must compare the name, not just the count, or soloing b while
  // a is the sole shown channel would un-hide everything instead of switching to b.
  const names = ["a", "b"];
  const out = soloShow(names, showMap(["a", true], ["b", false]), "b");
  assert.deepEqual([...out], [["a", false], ["b", true]]);
});

test("an empty chart and an unknown name do not produce a blank panel", () => {
  assert.deepEqual([...soloShow([], new Map(), "a")], []);
  const out = soloShow(["a", "b"], showMap(["a", true], ["b", true]), "zzz");
  assert.deepEqual([...out], [["a", false], ["b", false]],
    "a name off the chart hides everything, which is visible; it cannot show the wrong trace");
});

// ---- the wirings ---------------------------------------------------------------------

let nextId = 0;
function ingest(raw) {
  const row = { id: ++nextId, ts: 1000 + nextId * 0.01, port: "p1", chan: "event", raw };
  state.maxId = row.id;
  plotIngest(row);
}

test("alt-click on the analog legend solos, and a plain click still toggles", () => {
  ingest("!p 1000 a=1 b=2 c=3");
  const chart = charts.get("p1|adhoc");
  assert.deepEqual(chart.names, ["a", "b", "c"]);
  const rows = chart.chansEl.children;
  assert.equal(rows.length, 3, "the legend must have a row per channel");

  rows[1].emit("click", { altKey: true, preventDefault() {} });
  assert.deepEqual(chart.names.map((n) => chart.show.get(n)), [false, true, false]);

  // Keyboard: makeSpanButton hands the activating event through, so Shift+Enter is the same
  // branch. The name span is the second child of the row (swatch, name, [unit]).
  const name = rows[1].children[1];
  name.onkeydown({ key: "Enter", shiftKey: true, preventDefault() {} });
  assert.deepEqual(chart.names.map((n) => chart.show.get(n)), [true, true, true],
    "Shift+Enter on the sole shown channel restores the rest");

  rows[0].emit("click", { preventDefault() {} });
  assert.deepEqual(chart.names.map((n) => chart.show.get(n)), [false, true, true],
    "an ordinary click must still toggle just the one channel");
});

test("alt-click on a lane name solos the digital lanes the same way", () => {
  ingest("!pd 1 f:u1:/b0,b1,b2");
  ingest("!ps 1 100 05");
  const names = [...digitalLanes.keys()];
  assert.equal(names.length, 3, "the packed byte must have built three lanes");
  const lane = digitalLanes.get(names[2]);

  lane.nameEl.onclick({ altKey: true });
  assert.deepEqual(names.map((n) => digitalLanes.get(n).show), [false, false, true]);
  assert.equal(lane.rowEl.classList.contains("off"), false);
  assert.equal(digitalLanes.get(names[0]).rowEl.classList.contains("off"), true,
    "the rows that were hidden must say so, or the gutter disagrees with the canvas");

  lane.nameEl.onclick({ altKey: true });
  assert.deepEqual(names.map((n) => digitalLanes.get(n).show), [true, true, true]);

  lane.nameEl.onclick({});
  assert.deepEqual(names.map((n) => digitalLanes.get(n).show), [true, true, false],
    "a plain click is still a toggle");
});
