// terminal.js: `selfScroll` marks the scroll event the code's own offset move fires, so the
// handler neither resumes a paused pane nor pages history for it. A rebuild set it whether or
// not the offset moved; when nothing moved no event came to clear it, and it swallowed the
// user's next scroll: after a filter change, a paused pane's first top hit loaded nothing.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, makeRow, FakeEl, tick } from "./dom_stub.mjs";

const env = installDom();
const tpl = readFileSync(new URL("../../mcuscope/webui/index.html", import.meta.url), "utf8")
  .match(/<template id="paneTpl">([\s\S]*?)<\/template>/)[1];
const tplRoot = new FakeEl("div");
for (const m of tpl.matchAll(/<(\w+)[^>]*\bclass="([^"]+)"/g)) {
  const el = new FakeEl(m[1]);
  el.className = m[2];
  tplRoot.appendChild(el);
}
env.byId("paneTpl").content.appendChild(tplRoot);

let pages = 0;
globalThis.fetch = async () => {
  pages += 1;
  return { ok: true, status: 200, headers: { get: () => null },
           json: async () => ({ lines: [], truncated: false }) };
};

const { state, buffer } = await import(webuiUrl("state.js"));
const T = await import(webuiUrl("terminal.js"));
for (let id = 1001; id <= 1400; id++) buffer.push(makeRow(id, { port: id % 2 ? "p1" : "p2" }));
state.maxId = 1400;
state.knownAliases = ["p1", "p2"];   // the port select offers them (populatePortSelect)
T.initTerminal();
const pane = T.panes[0];
// A filter change, through the pane's own handler (rebuild), as a channel or regex edit is.
const pickPort = (v) => { pane.portSel.value = v; pane.portSel.emit("change"); };

// A browser's scroller: the offset is clamped to the content, and a move fires one scroll event.
const sc = pane.scrollEl;
let top = 0;
Object.defineProperty(sc, "scrollHeight", { get: () => pane.rows.length * 18 });
Object.defineProperty(sc, "scrollTop", {
  get: () => Math.max(0, Math.min(top, sc.scrollHeight - sc.clientHeight)),
  set: (v) => { top = v; },
});
const userScroll = (to) => { top = to; sc.emit("scroll"); };
const browserScrollEvent = () => sc.emit("scroll");   // what a clamp or a code move fires

function pausedMidway(at) {
  if (!pane.autoscroll) T.setAutoscroll(pane, true);
  pickPort("all");
  T.setAutoscroll(pane, false);
  userScroll(at);
  assert.equal(pane.autoscroll, false, "setup: scrolled up in a paused pane");
}

test("after a filter change that moved nothing, the first top hit pages history", async () => {
  pausedMidway(2000);
  pickPort("all");   // the same rows: nothing moves
  pages = 0;
  userScroll(0);
  await tick();
  assert.equal(pages, 1, "the top hit was swallowed as the rebuild's own scroll");
});

test("a filter change that clamps the offset: its scroll event is ours, the next one the user's", async () => {
  pausedMidway(5000);
  pickPort("p1");   // half the rows go: the offset clamps
  assert.notEqual(sc.scrollTop, 5000, "setup: the offset clamped");
  pages = 0;
  browserScrollEvent();
  await tick();
  assert.equal(pane.autoscroll, false, "the clamp's own event resumed the paused pane");
  assert.equal(pages, 0);
  userScroll(0);
  await tick();
  assert.equal(pages, 1);
});
