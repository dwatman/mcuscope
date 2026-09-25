// terminal.js loadHistory: one top hit walks at most HISTORY_HOPS pages. A walk whose filter
// empties all of them stops with the view still at the top, where no scroll event can ask
// again, and the hint told the user to scroll to the top. The footer now names what the walk
// read and offers "search older", which walks on.

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

// /lines: a full page of rows below id_to, none of which the pane's pattern matches, down to
// the capture's first row, `start`.
const asked = [];
let start = 1;
globalThis.fetch = async (url) => {
  const idTo = Number(new URL(url, "http://x").searchParams.get("id_to"));
  asked.push(idTo);
  const lines = [];
  for (let id = idTo; id > idTo - 200 && id >= start; id--) lines.push(makeRow(id, { raw: "noise" }));
  return { ok: true, status: 200, headers: { get: () => null },
           json: async () => ({ lines, truncated: lines.length === 200 }) };
};

const { state, buffer } = await import(webuiUrl("state.js"));
const T = await import(webuiUrl("terminal.js"));
const { HISTORY_HOPS, HISTORY_PAGE } = await import(webuiUrl("pane.js"));
for (let id = 100001; id <= 100010; id++) buffer.push(makeRow(id, { raw: "wanted " + id }));
state.maxId = 100010;
T.initTerminal();
const pane = T.panes[0];
const older = pane.el.querySelector(".older");

test("a walk its filter empties offers to search older, and that fetches the next page", async () => {
  T.applyRegex(pane, "wanted");
  T.rebuild(pane);
  T.setAutoscroll(pane, false);
  assert.equal(older.hidden, true, "positive control: nothing walked yet, no control");
  asked.length = 0;
  await T.loadHistory(pane);
  assert.equal(asked.length, HISTORY_HOPS);
  assert.equal(pane.hintEl.textContent, `no match in the last ${HISTORY_HOPS * HISTORY_PAGE} lines`);
  assert.equal(older.hidden, false);
  older.emit("click");
  await tick();
  assert.equal(asked[HISTORY_HOPS], asked[HISTORY_HOPS - 1] - HISTORY_PAGE, "the sixth page, below the fifth");
  await tick(10);
  assert.equal(pane.hintEl.textContent, `no match in the last ${2 * HISTORY_HOPS * HISTORY_PAGE} lines`);
});

test("a page that lands rows ends the offer", async () => {
  T.applyRegex(pane, "noise|wanted");
  if (pane.autoscroll) T.setAutoscroll(pane, false);
  T.rebuild(pane);   // re-filtering resets the walk
  assert.equal(pane.historyMiss, 0);
  pane.historyMiss = 1000;   // as an earlier walk left it
  await T.loadHistory(pane);
  assert.equal(pane.historyMiss, 0);
  assert.equal(older.hidden, true);
  assert.equal(pane.hintEl.textContent, "scroll to the top for older lines");
});

test("a walk that reaches the capture's start with no match offers nothing more", async () => {
  T.applyRegex(pane, "wanted");
  if (pane.autoscroll) T.setAutoscroll(pane, false);
  T.rebuild(pane);   // re-filtering resets the walk
  start = 100001 - 300;   // the capture begins 300 lines below the buffer
  try {
    await T.loadHistory(pane);
    assert.equal(pane.historyMiss, 300, "setup: the walk read the capture's start without a match");
    assert.equal(pane.hintEl.textContent, "no older lines to load");
    assert.equal(older.hidden, true, "search older offered beside \"no older lines to load\"");
  } finally {
    start = 1;
  }
});
