// terminal.js clear-all is a group state (REVIEW class 25): a pane added after it starts at its
// point. It started at 0 and rebuilt from the shared buffer, so the new pane showed every line
// clear-all had just hidden, at negative relative times.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, makeRow, FakeEl } from "./dom_stub.mjs";

const env = installDom();
// createPane builds from index.html's own <template>, flattened: it looks elements up by class.
const tpl = readFileSync(new URL("../../mcuscope/webui/index.html", import.meta.url), "utf8")
  .match(/<template id="paneTpl">([\s\S]*?)<\/template>/)[1];
const tplRoot = new FakeEl("div");
for (const m of tpl.matchAll(/<(\w+)[^>]*\bclass="([^"]+)"/g)) {
  const el = new FakeEl(m[1]);
  el.className = m[2];
  tplRoot.appendChild(el);
}
env.byId("paneTpl").content.appendChild(tplRoot);

const { state, buffer, pushBuffer } = await import(webuiUrl("state.js"));
const T = await import(webuiUrl("terminal.js"));
T.initTerminal();

let id = 0;
const arrive = (n) => { for (let i = 0; i < n; i++) { const r = makeRow(++id); pushBuffer(r); state.maxId = r.id; } };
const addPane = () => { env.byId("addPaneBtn").emit("click"); return T.panes.at(-1); };

test("a pane added after clear-all holds none of what it cleared, and fills with what follows", () => {
  arrive(50);
  env.byId("clearAllBtn").emit("click");
  const pane = addPane();
  assert.equal(pane.rows.length, 0, "the new pane refilled with the cleared lines");
  const later = makeRow(++id);
  pushBuffer(later); state.maxId = later.id;
  T.rebuild(pane);
  assert.deepEqual(pane.rows.map((r) => r.id), [later.id]);
});

test("a capture reset drops the clear point: a new pane starts from the new capture", () => {
  T.panes.slice(1).forEach((p) => p.el.querySelector(".closepane").emit("click"));
  arrive(50);
  env.byId("clearAllBtn").emit("click");
  state.captureGen += 1;   // api.js resetForDbReset: ids restart below the old clear point
  buffer.length = 0; id = 0;
  arrive(5);
  assert.equal(addPane().rows.length, 5);
});
