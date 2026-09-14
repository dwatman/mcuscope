// app.js applies the remembered sidebar layout at boot and saves each change (SPEC 9.1).
// One import per process, so the corrupt-value boot is its own file (app_layout_corrupt).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };
env.store.set("mcuscope.layout", JSON.stringify({ sideW: 500, hidden: true, canCap: 30 }));
env.byId("workspace").clientWidth = 1600;
await import(webuiUrl("app.js"));

const ws = env.byId("workspace");
const saved = () => JSON.parse(env.store.get("mcuscope.layout"));

test("boot applies the stored width, hidden state and CAN cap", () => {
  assert.equal(ws.style["--side-w"], "500px");
  assert.equal(ws.classList.contains("collapsed"), true);
  assert.equal(env.byId("sidebar").style["--can-h"], "30%");
  assert.equal(env.byId("popoutBtn").textContent, "↔ expand");
});

test("reopen, expand, restore and the divider double-click are each remembered", () => {
  env.byId("reopenBtn").emit("click");
  assert.equal(saved().hidden, false);
  env.byId("popoutBtn").emit("click");
  assert.equal(ws.style["--side-w"], "960px");
  assert.deepEqual([saved().expanded, saved().sideW], [true, 500], "expand keeps the width to come back to");
  env.byId("popoutBtn").emit("click");
  assert.equal(ws.style["--side-w"], "500px", "restore returns to the dragged width, not 360");
  env.byId("resizer").emit("dblclick");
  assert.equal(ws.style["--side-w"], "360px");
  assert.equal(saved().sideW, null);
  env.byId("canPlotDivider").emit("dblclick");
  assert.equal(saved().canCap, null);
  assert.equal(env.byId("sidebar").style["--can-h"], "45%");
  env.byId("collapseBtn").emit("click");
  assert.equal(saved().hidden, true);
});

test("a pointerup with no drag in progress saves nothing", () => {
  const before = env.store.get("mcuscope.layout");
  env.byId("resizer").emit("pointerup", { pointerId: 1 });
  env.byId("canPlotDivider").emit("pointerup", { pointerId: 1 });
  assert.equal(env.store.get("mcuscope.layout"), before);
});

test("a dragged width is saved on release and ends the expanded state", () => {
  env.byId("popoutBtn").emit("click");
  assert.equal(saved().expanded, true);
  ws.getBoundingClientRect = () => ({ right: 1600, left: 0, top: 0, bottom: 900, width: 1600, height: 900 });
  const r = env.byId("resizer");
  r.emit("pointerdown", { pointerId: 1 });
  r.emit("pointermove", { clientX: 1180 });
  r.emit("pointerup", { pointerId: 1 });
  assert.deepEqual([saved().sideW, saved().expanded], [420, false]);
  assert.equal(env.byId("popoutBtn").textContent, "↔ expand");
  r.emit("pointerdown", { pointerId: 1 });
  r.emit("pointermove", { clientX: 100 });
  r.emit("pointerup", { pointerId: 1 });
  assert.equal(saved().sideW, 1274, "a drag past the terminal's column is clamped before it is saved");
});

test("the CAN divider saves its cap as a percent of the sidebar body", () => {
  // The stub's querySelector(".side-body") is a detached element app.js already holds, so the
  // rect is given to every element for the duration of the drag.
  const proto = Object.getPrototypeOf(env.byId("sidebar"));
  const real = proto.getBoundingClientRect;
  proto.getBoundingClientRect = () => ({ top: 100, bottom: 900, height: 800, left: 0, right: 0, width: 0 });
  const d = env.byId("canPlotDivider");
  try {
    d.emit("pointerdown", { pointerId: 1 });
    d.emit("pointermove", { clientY: 300 });
    d.emit("pointerup", { pointerId: 1 });
  } finally {
    proto.getBoundingClientRect = real;
  }
  assert.equal(saved().canCap, 25);
  assert.equal(env.byId("sidebar").style["--can-h"], "25%", "the applied cap is the saved one");
});
