// The colour store and the picker-safe hex, which the analog charts and the digital lanes
// share. They lived in state.js next to the auth token's retry budget until chrome.js.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();

const { colorFor, saveColor, rgbToHex, buildWindowButtons } =
  await import(webuiUrl("chrome.js"));

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
});
