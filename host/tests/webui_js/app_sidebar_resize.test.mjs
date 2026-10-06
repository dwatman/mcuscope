// app.js: a narrowed window re-clamps the sidebar; both dividers end a drag on pointerup,
// pointercancel and lostpointercapture. layout.js sideWidthFor is pinned alongside.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
const winHandlers = new Map();
globalThis.addEventListener = (t, fn) => winHandlers.set(t, [...(winHandlers.get(t) || []), fn]);
globalThis.fetch = async () => { throw new Error("offline in tests"); };
env.store.set("mcuscope.layout", JSON.stringify({ sideW: 1000, expanded: false }));
const ws = env.byId("workspace");
ws.clientWidth = 1600;
ws.getBoundingClientRect = () => ({ left: 0, right: ws.clientWidth, top: 0, height: 800 });
const sideBody = document.querySelector(".side-body");
const realQuery = document.querySelector.bind(document);
document.querySelector = (sel) => (sel === ".side-body" ? sideBody : realQuery(sel));
sideBody.getBoundingClientRect = () => ({ top: 0, height: 800 });
await import(webuiUrl("app.js"));
const { sideWidthFor } = await import(webuiUrl("layout.js"));

const r = env.byId("resizer");
const sidebar = () => env.byId("sidebar");
const saved = () => JSON.parse(env.store.get("mcuscope.layout"));
const resize = () => (winHandlers.get("resize") || []).forEach((f) => f({}));

test("sideWidthFor clamps a saved width to a narrower workspace and follows it when expanded", () => {
  assert.equal(sideWidthFor({ sideW: 1000, expanded: false }, 1600), 1000);
  assert.equal(sideWidthFor({ sideW: 1000, expanded: false }, 800), 800 - 326);
  assert.equal(sideWidthFor({ sideW: null, expanded: false }, 800), null);
  assert.equal(sideWidthFor({ sideW: 1000, expanded: true }, 800) < 800 - 300, true);
});

test("a window resize re-clamps the sidebar so the terminal keeps its column", () => {
  assert.equal(ws.style["--side-w"], "1000px");
  ws.clientWidth = 800;
  resize();
  assert.equal(ws.style["--side-w"], "474px");
  assert.equal(r.getAttribute("aria-valuenow"), "474");
  assert.equal(r.getAttribute("aria-valuetext"), "474 px");
  ws.clientWidth = 1600;
  resize();
  assert.equal(ws.style["--side-w"], "1000px", "the stored width returns in a wide window");
  assert.equal(saved().sideW, 1000, "a resize does not overwrite the stored choice");
});

for (const ending of ["pointerup", "pointercancel", "lostpointercapture"]) {
  test(`${ending} ends a sidebar drag, releases the grab and saves the width`, () => {
    const cap = [];
    r.setPointerCapture = (id) => cap.push(["set", id]);
    r.releasePointerCapture = (id) => cap.push(["release", id]);
    r.emit("pointerdown", { pointerId: 7 });
    assert.equal(r.classList.contains("drag"), true);
    r.emit("pointermove", { clientX: 1200 });
    r.emit(ending, { pointerId: 7 });
    assert.equal(r.classList.contains("drag"), false, "left in drag state");
    assert.equal(saved().sideW, 400, "width from clientX 1200 in a 1600 workspace");
    assert.deepEqual(cap.at(-1), ["release", 7]);
    r.emit("pointermove", { clientX: 1000 });
    assert.equal(ws.style["--side-w"], "400px", "a move after the end must not drag");
  });
}

for (const ending of ["pointerup", "pointercancel", "lostpointercapture"]) {
  test(`${ending} ends a CAN/plots divider drag`, () => {
    const d = env.byId("canPlotDivider");
    d.setPointerCapture = () => {};
    d.releasePointerCapture = () => {};
    d.emit("pointerdown", { pointerId: 1 });
    d.emit("pointermove", { clientY: 400 });
    d.emit(ending, { pointerId: 1 });
    assert.equal(d.classList.contains("drag"), false);
    assert.equal(saved().canCap, 50);
    assert.equal(sidebar().style["--can-h"], "50%", "the cap stays a share, not the drag's px");
    assert.equal(d.getAttribute("aria-valuenow"), "50");
  });
}

test("the CAN/plots divider resizes from the keyboard and reads its cap", () => {
  const d = env.byId("canPlotDivider");
  const key = (k, shiftKey = false) => {
    const ev = { key: k, shiftKey, prevented: false, preventDefault() { this.prevented = true; } };
    d.emit("keydown", ev);
    return ev.prevented;
  };
  d.emit("dblclick", {});
  assert.equal(d.getAttribute("aria-valuenow"), "45", "the default cap is the value");
  assert.equal(key("ArrowDown"), true);
  assert.equal(saved().canCap, 50);
  assert.equal(sidebar().style["--can-h"], "50%");
  assert.equal(d.getAttribute("aria-valuetext"), "CAN table up to 50 %");
  key("ArrowUp", true);
  assert.equal(saved().canCap, 30);
  assert.equal(key("Tab"), false, "Tab must keep moving focus");
  assert.equal(saved().canCap, 30);
  d.emit("dblclick", {});
  assert.equal(saved().canCap, null);
  assert.equal(sidebar().style["--can-h"], undefined, "the stylesheet's default applies again");
  assert.equal(d.getAttribute("aria-valuenow"), "45");
});
