// plots.js (registry class 72): the chart rename box.
//
// finish() refocused the title on every exit, including blur. A blur is focus leaving for
// somewhere the user chose (the command input, a pane's regex box); moving focus back to the
// title from inside the blur handler cancels that move in Chromium, so the user's typing went
// to the title span, whose Enter opens the rename box again.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { charts, plotIngest, clearAllCharts } = await import(webuiUrl("plots.js"));

let nextId = 1;
const ingest = (raw) => plotIngest({ id: nextId++, ts: 1000 + nextId, port: "p1", chan: "event", raw });

// Open the rename box on a fresh chart; returns the chart, the box and a focus counter.
function openRename() {
  clearAllCharts();
  ingest("!pd 0 a:u2");
  ingest("!ps 0 3E8 0064");
  const chart = charts.get("p1|s0");
  let input = null;
  chart.titleEl.after = (el) => { input = el; };   // the stub has no after()
  const focus = { title: 0 };
  chart.titleEl.focus = () => { focus.title += 1; };
  chart.titleEl.emit("click");
  assert.ok(input, "the title click opened no rename box");
  return { chart, input, focus };
}

test("a rename left by blur commits and leaves focus where the user sent it", () => {
  const { chart, input, focus } = openRename();
  input.value = "motor";
  input.emit("blur");
  assert.equal(chart.titleEl.textContent, "motor", "blur must still commit the typed title");
  assert.equal(focus.title, 0, "blur pulled focus back to the chart title");
  assert.equal(chart.titleEl.hidden, false);
});

test("Enter and Escape still return focus to the title they came from", () => {
  for (const key of ["Enter", "Escape"]) {
    const { input, focus } = openRename();
    input.emit("keydown", { key, preventDefault() {} });
    assert.equal(focus.title, 1, `${key} left keyboard focus nowhere`);
    input.emit("blur");   // removing the box blurs it: no second commit, no second focus
    assert.equal(focus.title, 1, `${key} then the removal's blur focused twice`);
  }
});
