// app.js: the sidebar divider resizes from the keyboard and the width persists like a drag;
// hiding and reopening the sidebar keeps keyboard focus on the control that undoes it.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };
env.store.set("mcuscope.layout", JSON.stringify({ sideW: 400, expanded: true }));
env.byId("workspace").clientWidth = 1600;   // widest sidebar: 1600 - 326 = 1274
await import(webuiUrl("app.js"));

const ws = env.byId("workspace");
const r = env.byId("resizer");
const saved = () => JSON.parse(env.store.get("mcuscope.layout"));
function key(k, shiftKey = false) {
  let prevented = false;
  r.emit("keydown", { key: k, shiftKey, preventDefault: () => { prevented = true; } });
  return prevented;
}

test("Left widens by 20 from the width in force, ends expanded, saves and reports the value", () => {
  assert.equal(ws.style["--side-w"], "960px", "booted expanded");
  assert.equal(key("ArrowLeft"), true);
  assert.equal(ws.style["--side-w"], "980px");
  assert.deepEqual([saved().sideW, saved().expanded], [980, false]);
  assert.equal(r.getAttribute("aria-valuenow"), "980");
  key("ArrowRight", true);
  assert.equal(saved().sideW, 880, "Shift moves 100");
});

test("arrows stop at the widest and narrowest widths instead of running past them", () => {
  for (let i = 0; i < 10; i++) key("ArrowLeft", true);
  assert.equal(saved().sideW, 1274, "leaves the terminal its column");
  key("ArrowLeft");
  assert.equal(saved().sideW, 1274);
  for (let i = 0; i < 20; i++) key("ArrowRight", true);
  assert.equal(saved().sideW, 260);
  key("ArrowRight");
  assert.equal(ws.style["--side-w"], "260px");
});

test("other keys neither resize nor save, so Tab still leaves the divider", () => {
  const before = env.store.get("mcuscope.layout");
  assert.equal(key("Tab"), false);
  assert.equal(key("ArrowUp"), false);
  assert.equal(key("Enter"), false);
  assert.equal(env.store.get("mcuscope.layout"), before);
});

test("hide moves focus to the reopen tab, and reopen moves it back to hide", () => {
  const focused = [];
  env.byId("reopenBtn").focus = () => focused.push("reopen");
  env.byId("collapseBtn").focus = () => focused.push("collapse");
  env.byId("collapseBtn").emit("click");
  env.byId("reopenBtn").emit("click");
  assert.deepEqual(focused, ["reopen", "collapse"]);
});

test("the sidebar view switch takes arrow keys", () => {
  assert.ok((env.byId("sideSeg").handlers.get("keydown") || []).length, "no roving handler on #sideSeg");
  assert.ok((env.byId("timeSeg").handlers.get("keydown") || []).length, "no roving handler on #timeSeg");
  assert.ok((env.byId("modeToggle").handlers.get("keydown") || []).length, "no roving handler on #modeToggle");
});
