// What the sidebar layout and the chart titles read back from localStorage (layout.js).
// Hand-editable, and saved in whatever window the user had at the time.

import test from "node:test";
import assert from "node:assert/strict";
import { webuiUrl } from "./dom_stub.mjs";

const { parseLayout, clampSideW, sideWidthFor, cleanTitle, parseTitles, belowFold } =
  await import(webuiUrl("layout.js"));

const DEFAULT = { sideW: null, expanded: false, hidden: false, canCap: null };

test("a corrupt stored layout falls back to the defaults", () => {
  for (const raw of [null, "", "{nope", "42", "null", "[]", '"wide"']) {
    assert.deepEqual(parseLayout(raw), DEFAULT, `for ${raw}`);
  }
});

test("each field is validated on its own, so one bad value does not cost the rest", () => {
  const got = parseLayout(JSON.stringify({ sideW: "500", expanded: "true", hidden: 1, canCap: 30 }));
  assert.deepEqual(got, { ...DEFAULT, canCap: 30 }, "strings and truthy numbers are not the stored types");
  for (const sideW of [-1, 0, null, 1e999]) {
    assert.equal(parseLayout(JSON.stringify({ sideW })).sideW, null, `sideW ${sideW}`);
  }
  for (const canCap of [0, 4.9, 95.1, 100, -5]) {
    assert.equal(parseLayout(JSON.stringify({ canCap })).canCap, null, `canCap ${canCap}`);
  }
  assert.equal(parseLayout('{"canCap": 5}').canCap, 5);
  assert.equal(parseLayout('{"canCap": 95}').canCap, 95);
});

test("a width saved in a wider window is clamped to leave the terminal its column", () => {
  const saved = parseLayout('{"sideW": 900}');
  assert.equal(sideWidthFor(saved, 1000), 674, "1000 - 326 for the terminal and divider");
  assert.equal(sideWidthFor(saved, 2000), 900, "a window with room applies it as saved");
  assert.equal(sideWidthFor(parseLayout('{"sideW": 120}'), 1600), 260, "never below the minimum");
  assert.equal(sideWidthFor(saved, 0), 900, "a workspace not laid out yet cannot clamp");
  assert.equal(sideWidthFor(DEFAULT, 1600), null, "nothing saved: the stylesheet default");
});

test("a width saved in a narrower window is kept, not stretched", () => {
  assert.equal(sideWidthFor(parseLayout('{"sideW": 300}'), 2560), 300);
});

test("expanded is a share of this window, whatever width was saved beside it", () => {
  const exp = parseLayout('{"sideW": 300, "expanded": true}');
  assert.equal(sideWidthFor(exp, 1600), 960);
  assert.equal(sideWidthFor(exp, 700), 374, "60 percent of 700 would squeeze the terminal");
  assert.equal(clampSideW(10, 400), 260, "a window too small for both keeps the sidebar minimum");
});

test("a typed title is trimmed and bounded; empty or whitespace is no title", () => {
  assert.equal(cleanTitle("  pack  "), "pack");
  for (const t of ["", "   ", "\t\n", null, undefined]) assert.equal(cleanTitle(t), null, JSON.stringify(t));
  assert.equal(cleanTitle("a".repeat(40)).length, 32);
  assert.equal(cleanTitle("a".repeat(31) + "  b"), "a".repeat(31), "no trailing space left by the cut");
});

test("stored titles drop anything that is not a usable string", () => {
  assert.deepEqual({ ...parseTitles("{nope") }, {});
  assert.deepEqual({ ...parseTitles('["a"]') }, {});
  const got = parseTitles(JSON.stringify({ "p|s0": " pack ", "p|s1": "  ", "p|s2": 7, "p|s3": { x: 1 } }));
  assert.deepEqual({ ...got }, { "p|s0": "pack" });
  const proto = parseTitles('{"__proto__": "x", "toString": "y"}');
  assert.equal(proto.toString, "y", "a device-named key is data, not a prototype member");
  assert.equal(Object.getPrototypeOf(proto), null);
});

test("below the fold means starting under the visible bottom, or peeking less than a head", () => {
  const items = [{ name: "a", top: 0 }, { name: "b", top: 470 }, { name: "c", top: 480 }, { name: "d", top: 900 }];
  assert.deepEqual(belowFold(items, 500).map((i) => i.name), ["c", "d"],
    "30 px of b's head is visible; 20 px of c's is not enough to notice");
  assert.deepEqual(belowFold([], 500), []);
});
