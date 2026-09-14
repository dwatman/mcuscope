// chrome.js: the roving tabindex every role="radiogroup" shares, and Enter-to-submit in dialogs.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, FakeEl } from "./dom_stub.mjs";

installDom();
const { setRadios, rovingRadios, enterSubmits, buildWindowButtons, showZoom, onZoomControls } =
  await import(webuiUrl("chrome.js"));

// A group of buttons whose click selects it, like #timeSeg; `focused` records focus order.
function group(labels, { disabled = [], hidden = [] } = {}) {
  const g = new FakeEl("div");
  const log = [];
  let sel = labels[0];
  for (const l of labels) {
    const b = new FakeEl("button");
    b.dataset.v = l;
    b.disabled = disabled.includes(l);
    b.hidden = hidden.includes(l);
    b.focus = () => log.push("focus " + l);
    b.addEventListener("click", () => { log.push("click " + l); sel = l; setRadios(g, (x) => x.dataset.v === sel); });
    g.appendChild(b);
  }
  setRadios(g, (x) => x.dataset.v === sel);
  rovingRadios(g);
  const btn = (l) => g.children.find((b) => b.dataset.v === l);
  const key = (from, k) => {
    let prevented = false;
    g.emit("keydown", { key: k, target: btn(from), preventDefault: () => { prevented = true; } });
    return prevented;
  };
  return { g, btn, key, log, sel: () => sel, stops: () => g.children.filter((b) => b.tabIndex === 0).map((b) => b.dataset.v) };
}

test("one tab stop, on the checked button, with aria-checked following", () => {
  const t = group(["host", "tick", "rel"]);
  assert.deepEqual(t.stops(), ["host"]);
  assert.equal(t.btn("tick").tabIndex, -1);
  assert.deepEqual(t.g.children.map((b) => b.getAttribute("aria-checked")), ["true", "false", "false"]);
});

test("a programmatic selection moves the tab stop", () => {
  const t = group(["host", "tick", "rel"]);
  setRadios(t.g, (b) => b.dataset.v === "rel");
  assert.deepEqual(t.stops(), ["rel"]);
  assert.equal(t.btn("rel").classList.contains("on"), true);
  assert.equal(t.btn("host").classList.contains("on"), false);
});

test("arrows select the neighbour and wrap past either end", () => {
  const t = group(["a", "b", "c"]);
  assert.equal(t.key("a", "ArrowRight"), true);
  assert.equal(t.sel(), "b");
  t.key("b", "ArrowDown");
  t.key("c", "ArrowRight");
  assert.equal(t.sel(), "a", "right past the last wraps to the first");
  t.key("a", "ArrowLeft");
  assert.equal(t.sel(), "c", "left past the first wraps to the last");
  t.key("c", "ArrowUp");
  assert.equal(t.sel(), "b");
  assert.deepEqual(t.stops(), ["b"]);
});

test("the move clicks before it focuses, so a click handler cannot pull focus out", () => {
  const t = group(["cmd", "raw"]);
  t.log.length = 0;
  t.key("cmd", "ArrowRight");
  assert.deepEqual(t.log, ["click raw", "focus raw"]);
});

test("a disabled or hidden button is skipped and never the tab stop", () => {
  const t = group(["a", "b", "c", "d"], { disabled: ["b"], hidden: ["c"] });
  t.key("a", "ArrowRight");
  assert.equal(t.sel(), "d", "b is disabled and c hidden");
  t.key("d", "ArrowRight");
  assert.equal(t.sel(), "a");
  setRadios(t.g, (b) => b.dataset.v === "b");
  assert.deepEqual(t.stops(), ["a"], "a checked but disabled button hands the stop to the first usable one");
});

test("other keys pass through untouched", () => {
  const t = group(["a", "b"]);
  t.log.length = 0;
  assert.equal(t.key("a", "Enter"), false);
  assert.equal(t.key("a", "Tab"), false);
  assert.deepEqual(t.log, []);
});

test("window selectors are radiogroups, and the zoom chip is the stop while it stands", () => {
  let left = 0;
  onZoomControls({ leave: () => { left += 1; showZoom(null); }, exit: () => showZoom(null) });
  const win = buildWindowButtons(30, () => {});
  const [s5, s30, s5m, chip] = win.children;
  assert.equal(win.getAttribute("role"), "radiogroup");
  assert.deepEqual([s5, chip].map((b) => b.getAttribute("role")), ["radio", "radio"]);
  assert.equal(s30.tabIndex, 0);
  showZoom("1.20 s");
  assert.deepEqual(win.children.map((b) => b.tabIndex), [-1, -1, -1, 0]);
  assert.equal(chip.getAttribute("aria-checked"), "true");
  assert.equal(s30.getAttribute("aria-checked"), "false");
  win.emit("keydown", { key: "ArrowRight", target: chip, preventDefault() {} });
  assert.equal(left, 1, "arrowing off the chip picks a span, which leaves the zoom");
  assert.equal(chip.hidden, true);
  assert.equal(s5.tabIndex, 0, "right of the chip wraps to the first span");
  assert.equal(s5m.tabIndex, -1);
});

// ---- enterSubmits ----------------------------------------------------------------------

function dialog(pick) {
  const dlg = new FakeEl("dialog");
  const go = new FakeEl("button");
  let pressed = 0;
  go.addEventListener("click", () => { pressed += 1; });
  enterSubmits(dlg, pick || (() => go));
  const enter = (target, over = {}) => {
    let prevented = false;
    dlg.emit("keydown", { key: "Enter", target, preventDefault: () => { prevented = true; }, ...over });
    return prevented;
  };
  return { go, enter, pressed: () => pressed };
}

test("Enter in a field presses the primary button", () => {
  const d = dialog();
  assert.equal(d.enter(new FakeEl("input")), true);
  d.enter(new FakeEl("select"));
  assert.equal(d.pressed(), 2);
});

test("Enter in a textarea, on a button or a link, or mid-composition submits nothing", () => {
  const d = dialog();
  assert.equal(d.enter(new FakeEl("textarea")), false, "a newline in a note, not a submit");
  d.enter(new FakeEl("button"));   // Cancel: Enter activates Cancel itself
  d.enter(new FakeEl("a"));
  d.enter(new FakeEl("input"), { isComposing: true });
  d.enter(new FakeEl("input"), { key: "a" });
  d.enter(new FakeEl("input"), { defaultPrevented: true });
  assert.equal(d.pressed(), 0);
});

test("a disabled primary (a save in flight) or no primary for the field ignores Enter", () => {
  const d = dialog();
  d.go.disabled = true;
  assert.equal(d.enter(new FakeEl("input")), false);
  const none = dialog(() => null);
  assert.equal(none.enter(new FakeEl("input")), false);
  assert.equal(d.pressed() + none.pressed(), 0);
});
