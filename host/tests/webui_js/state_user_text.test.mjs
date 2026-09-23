// state.js userText: invisible formatting characters in outside text are shown, not obeyed,
// and the result is bidi-isolated.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
const { userText } = await import(webuiUrl("state.js"));
const iso = (s) => "\u2068" + s + "\u2069";

const INVISIBLE = [0x061C, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D,
                   0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0xFEFF];
// The neighbours of each range, which are visible or ordinary and must pass through.
const KEPT = [0x061B, 0x061D, 0x200A, 0x2010, 0x2029, 0x202F, 0x2065, 0x206A, 0xFEFE, 0x05D0];

test("the gpj.exe spoof shows its override", () => {
  assert.equal(userText("\u202egpj.exe"), iso("<U+202E>gpj.exe"));
});

test("every invisible formatting character is escaped, and only those", () => {
  for (const cp of INVISIBLE) {
    const hex = cp.toString(16).toUpperCase().padStart(4, "0");
    assert.equal(userText("a" + String.fromCharCode(cp) + "b"), iso(`a<U+${hex}>b`), hex);
  }
  for (const cp of KEPT) {
    const s = "a" + String.fromCharCode(cp) + "b";
    assert.equal(userText(s), iso(s), cp.toString(16));
  }
  assert.equal(userText("\u202e\u202e"), iso("<U+202E><U+202E>"), "every occurrence, not the first");
});

test("plain text and non-strings pass through, isolated", () => {
  assert.equal(userText("run-a"), iso("run-a"));
  assert.equal(userText(7), iso("7"));
});
