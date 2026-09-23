// state.js userText: invisible formatting characters in outside text are shown, not obeyed,
// and the result is bidi-isolated.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
const { userText } = await import(webuiUrl("state.js"));
const iso = (s) => "\u2068" + s + "\u2069";

// What renders as nothing (Default_Ignorable_Code_Point) and what reorders (Bidi_Control),
// enumerated from the Unicode property rather than listed by hand (REVIEW class 75).
const PROPERTY = /[\p{Default_Ignorable_Code_Point}\p{Bidi_Control}]/u;

test("the gpj.exe spoof shows its override", () => {
  assert.equal(userText("\u202egpj.exe"), iso("<U+202E>gpj.exe"));
});

test("every default-ignorable and bidi control code point is escaped, and only those", () => {
  let escaped = 0;
  for (let cp = 0; cp <= 0x10FFFF; cp++) {
    const s = "a" + String.fromCodePoint(cp) + "b";
    const hex = cp.toString(16).toUpperCase().padStart(4, "0");
    if (PROPERTY.test(String.fromCodePoint(cp))) {
      escaped += 1;
      assert.equal(userText(s), iso(`a<U+${hex}>b`), hex);
    } else if (userText(s) !== iso(s)) {
      assert.fail(`U+${hex} is not invisible but was changed`);
    }
  }
  assert.ok(escaped > 4000, `the property enumerated only ${escaped} code points`);
  assert.equal(userText("\u202e\u202e"), iso("<U+202E><U+202E>"), "every occurrence, not the first");
});

test("an astral code point is named whole, not as two surrogates", () => {
  assert.equal(userText("run\u{E0041}"), iso("run<U+E0041>"));
});

test("an emoji keeps its presentation selector; after a letter or digit it is escaped", () => {
  for (const s of ["\u2764\uFE0F", "\u26A0\uFE0E", "\u{1F600}\uFE0F"]) assert.equal(userText(s), iso(s));
  assert.equal(userText("run\uFE0F"), iso("run<U+FE0F>"));
  assert.equal(userText("run1\uFE0F"), iso("run1<U+FE0F>"));
  assert.equal(userText("\u2764\uFE0F\uFE0F"), iso("\u2764\uFE0F<U+FE0F>"), "one selector per emoji");
  assert.equal(userText("\u2764\uFE00"), iso("\u2764<U+FE00>"), "not a presentation selector");
});

test("plain text and non-strings pass through, isolated", () => {
  assert.equal(userText("run-a"), iso("run-a"));
  assert.equal(userText(7), iso("7"));
});
