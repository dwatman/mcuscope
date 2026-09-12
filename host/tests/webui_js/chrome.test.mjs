// The colour store and the picker-safe hex, which the analog charts and the digital lanes
// share. They lived in state.js next to the auth token's retry budget until chrome.js.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();

const { colorFor, saveColor, rgbToHex, buildWindowButtons, syncWindowButtons,
        dropWindowButtons } = await import(webuiUrl("chrome.js"));

test("rgbToHex is safe to hand to <input type=color>", () => {
  assert.equal(rgbToHex("#abcdef"), "#abcdef");
  assert.equal(rgbToHex("#abcdefff"), "#abcdef", "<input type=color> rejects an alpha suffix");
  assert.equal(rgbToHex("rgb(1,2,3)"), "#46c8d8", "a non-hex colour falls back to the default");
  assert.equal(rgbToHex(""), "#46c8d8");
  assert.equal(rgbToHex(null), "#46c8d8");
});

test("a saved colour overrides the palette slot, and persists", () => {
  const stock = colorFor("chanA", 0);
  saveColor("chanA", "#123456");
  assert.equal(colorFor("chanA", 0), "#123456");
  assert.equal(colorFor("chanB", 0), stock, "another channel keeps the palette slot");
  assert.equal(JSON.parse(env.store.get("mcuscope.colors")).chanA, "#123456");
});

test("the palette wraps rather than running out", () => {
  // Names are globally unique per SPEC 2.5, so an eighth channel must still get a colour.
  for (const i of [0, 7, 8, 99]) assert.match(colorFor(`c${i}`, i), /^#[0-9a-f]{6}$/i);
  assert.equal(colorFor("wrapped", 8), colorFor("wrapped2", 0), "slot 8 wraps to slot 0");
});

test("a channel named after an Object.prototype member gets a colour, not a function", () => {
  // SPEC 2.5's name grammar admits `toString`, `constructor` and `__proto__`, and the
  // colour store is keyed directly by device-supplied channel names. On a plain object
  // an unsaved `toString` answered with the inherited function, which canvas silently
  // ignores as a stroke, and saving `__proto__` hit the prototype setter and was dropped.
  for (const [name, slot] of [["toString", 0], ["constructor", 1], ["valueOf", 2],
                              ["hasOwnProperty", 3], ["__proto__", 4]]) {
    assert.match(colorFor(name, slot), /^#[0-9a-f]{6}$/i,
                 `unsaved '${name}' must fall back to the palette`);
    saveColor(name, "#101010");
    assert.equal(colorFor(name, slot), "#101010", `saved '${name}' must persist`);
  }
  // The persisted JSON round-trips through loadColors' own-property copy, so a saved
  // `__proto__` survives a reload rather than vanishing from the serialisation.
  assert.equal(JSON.parse(env.store.get("mcuscope.colors"))["toString"], "#101010");
});

test("a poisoned or hand-edited colour store cannot smuggle a non-string in", () => {
  // localStorage is same-origin user data: a value of the wrong type must fall back to
  // the palette on the next load rather than reach a canvas stroke or an <input value=>.
  env.store.set("mcuscope.colors", JSON.stringify({ good: "#222222", bad: { evil: 1 } }));
  return import(webuiUrl("chrome.js") + "?reload=poisoned").then((m) => {
    assert.equal(m.colorFor("good", 0), "#222222");
    assert.match(m.colorFor("bad", 1), /^#[0-9a-f]{6}$/i, "a non-string value is dropped");
  });
});

test("the window selector marks the current window and reports a click", () => {
  // One selector serves both the analog chart heads and the digital head; they used to
  // carry duplicate copies of this loop.
  const picked = [];
  const group = buildWindowButtons(30, (secs) => picked.push(secs));
  const labels = group.children.map((b) => b.textContent);
  assert.deepEqual(labels, ["5s", "30s", "5m"]);
  assert.deepEqual(group.children.map((b) => b.classList.contains("on")),
                   [false, true, false], "the current window is the marked one");
  group.children[2].emit("click");
  assert.deepEqual(picked, [300]);
  assert.deepEqual(group.children.map((b) => b.classList.contains("on")),
                   [false, false, true], "the group repaints its own selection");
  dropWindowButtons(group);   // leave the shared registry as this test found it
});

// ---- shift-click: one span for every chart and the lanes ------------------------------
//
// Each chart holds its own window and the digital panel its own, so moving three streams
// plus the ad-hoc chart plus the lanes from 30 s to 5 s was five clicks - and a half-done
// change leaves panels showing different spans under a cursor that claims to be shared.

const on = (g) => g.children.map((b) => b.classList.contains("on"));

test("a plain click reaches one panel; the event is passed through", () => {
  const seen = [];
  const a = buildWindowButtons(30, (secs, e) => seen.push([secs, !!(e && e.shiftKey)]));
  const b = buildWindowButtons(30, (secs) => seen.push(["b", secs]));
  a.children[0].emit("click", { shiftKey: false });
  assert.deepEqual(seen, [[5, false]], "a plain click must not touch the other panel");
  assert.deepEqual(on(a), [true, false, false]);
  assert.deepEqual(on(b), [false, true, false], "and must not repaint it either");
  dropWindowButtons(a); dropWindowButtons(b);
});

test("a shift-click applies the span to every selector, and repaints them all", () => {
  const seen = [];
  const a = buildWindowButtons(30, (secs, e) => seen.push(["a", secs, !!(e && e.shiftKey)]));
  const b = buildWindowButtons(30, (secs, e) => seen.push(["b", secs, !!(e && e.shiftKey)]));
  a.children[0].emit("click", { shiftKey: true });
  assert.deepEqual(seen, [["a", 5, true], ["b", 5, true]],
    "the panel that was not clicked must receive the span too, and know it was a shift-click");
  assert.deepEqual(on(a), [true, false, false]);
  assert.deepEqual(on(b), [true, false, false],
    "a head still showing 30s while its chart draws 5s is the half-done state this prevents");
  dropWindowButtons(a); dropWindowButtons(b);
});

test("syncWindowButtons ignores a span that is not on the selector", () => {
  const g = buildWindowButtons(30, () => {});
  syncWindowButtons(7);
  assert.deepEqual(on(g), [false, true, false], "an unknown span must not clear every button");
  syncWindowButtons(300);
  assert.deepEqual(on(g), [false, false, true]);
  dropWindowButtons(g);
});

test("a dropped selector stops receiving shift-clicks", () => {
  // clear-all destroys a chart's DOM; its onSelect would otherwise keep writing the window
  // onto a chart object that is no longer drawn.
  let dead = 0;
  const gone = buildWindowButtons(30, () => { dead += 1; });
  const live = buildWindowButtons(30, () => {});
  dropWindowButtons(gone);
  live.children[0].emit("click", { shiftKey: true });
  assert.equal(dead, 0, "a destroyed chart's selector must not be driven");
  dropWindowButtons(live);
});
