// terminal.js render: tick stamps read state.anchorTick, which the first ticked row after
// clear-all sets. The live append path kept the elements already drawn, so a line stamped before
// that row kept the old zero until something else redrew the pane.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

installDom();
const { state, buffer, pushBuffer } = await import(webuiUrl("state.js"));
await import(webuiUrl("can.js"));   // publishes the !can tick (hooks.canTick)
const T = await import(webuiUrl("terminal.js"));

async function arrive(pane, row) {
  pushBuffer(row);
  state.maxId = row.id;
  pane.queue.push(row);   // api.js feedPanes
  T.scheduleFlush();
  await tick(50);
}

test("a line stamped before the tick zero is set is redrawn against it", async () => {
  buffer.length = 0;
  state.timeMode = "tick";
  pushBuffer(makeRow(1, { ts: 100, chan: "event", raw: "!can 2000 - 100 01" }));   // before clear-all
  state.anchorTick = null;   // clear-all re-zeroes
  const pane = makePane({ clearId: 1 });
  T.panes.push(pane);
  try {
    await arrive(pane, makeRow(2, { ts: 100, raw: "no tick of its own" }));
    const first = pane.vlist.children[0];
    assert.equal(first.children[0].textContent, "~2000", "setup: no zero yet");
    await arrive(pane, makeRow(3, { ts: 101, chan: "event", raw: "!can 3000 - 100 01" }));
    assert.equal(state.anchorTick, 3000);
    assert.equal(pane.vlist.children[0].children[0].textContent, "~-1000");
    assert.equal(pane.vlist.children[1].children[0].textContent, "0");
    const kept = pane.vlist.children[0];
    await arrive(pane, makeRow(4, { ts: 101.5, raw: "later" }));
    assert.equal(pane.vlist.children[0], kept, "with the zero unchanged the append path still runs");
    assert.equal(pane.domEls.length, 3);
  } finally {
    T.panes.splice(T.panes.indexOf(pane), 1);
    state.timeMode = "host";
  }
});
