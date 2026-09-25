// state.js intField: a bounded form field takes a plain decimal integer only. Number() alone read
// "0x3E8", "0b1111101000", "1e3" and "1000.0" as 1000, so a value the user can see is not what
// they meant passed every bounds check downstream.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
const { intField } = await import(webuiUrl("state.js"));

test("every other number grammar Number() reads is refused", () => {
  for (const s of ["0x3E8", "0b1111101000", "0o1750", "1e3", "1000.0", "+1000", "1_000", "Infinity", "- 1"]) {
    assert.ok(Number.isNaN(intField(s)), `${s} read as ${intField(s)}`);
  }
});

test("a plain integer, with surrounding whitespace, still reads", () => {
  assert.equal(intField(" 1000 "), 1000);
  assert.equal(intField("-5"), -5);
  assert.equal(intField("007"), 7);
});
