// A CAN id click filters the last pane and shows "unfilter" (can.js); the pane remembers the
// pattern it replaced (terminal.js filterPaneTo). `+ pane` cloned the regex but not that memory,
// so unfilter left the clone filtered; closing the only filtered pane left the button showing
// with nothing to restore.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, makeRow, FakeEl } from "./dom_stub.mjs";

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

const { state, pushBuffer } = await import(webuiUrl("state.js"));
const T = await import(webuiUrl("terminal.js"));
const C = await import(webuiUrl("can.js"));
C.setPaneFilter(T.filterPaneTo);   // app.js's wiring
C.initCan();
T.initTerminal();

let id = 0;
const unfilter = env.byId("canFilterClear");
function clickId(hexId) {
  const r = makeRow(++id, { chan: "event", raw: `!can ${id} - ${hexId} 01` });
  pushBuffer(r); state.maxId = r.id;
  C.canIngest(r);
  C.renderCan();
  const rows = env.byId("canWrap").querySelectorAll("tr").slice(1);
  const tr = rows.find((x) => x.children[0].children[0].textContent.toLowerCase().includes(hexId.toLowerCase()));
  tr.children[0].children[0].emit("click", {});
}
function onePane(regex) {
  while (T.panes.length > 1) T.panes.at(-1).el.querySelector(".closepane").emit("click");
  T.filterPaneTo("");   // forget any earlier click
  const p = T.panes[0];
  p.matchInput.value = regex;
  T.applyRegex(p, regex);
  T.rebuild(p);
  return p;
}

test("a clone of a CAN-filtered pane is restored by unfilter too", () => {
  const first = onePane("mine");
  clickId("321");
  assert.equal(unfilter.hidden, false);
  env.byId("addPaneBtn").emit("click");
  const clone = T.panes.at(-1);
  assert.equal(clone.regexSrc, first.regexSrc, "setup: the clone carries the id pattern");
  unfilter.emit("click");
  assert.equal(first.regexSrc, "mine");
  assert.equal(clone.regexSrc, "mine", "the clone kept the id filter after unfilter");
});

test("closing the only CAN-filtered pane hides unfilter; another left keeps it", () => {
  onePane("");
  env.byId("addPaneBtn").emit("click");
  clickId("100");   // filters the last pane
  env.byId("addPaneBtn").emit("click");   // a clone: two filtered panes now
  T.panes.at(-1).el.querySelector(".closepane").emit("click");
  assert.equal(unfilter.hidden, false, "positive control: a filtered pane is left");
  T.panes.at(-1).el.querySelector(".closepane").emit("click");
  assert.equal(unfilter.hidden, true, "no filtered pane is left to restore");
});
