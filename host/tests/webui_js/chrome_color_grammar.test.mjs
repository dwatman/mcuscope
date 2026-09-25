// chrome.js loadColors: a saved channel colour loads only as `#rrggbb`, the form the picker
// writes. localStorage is hand-editable, and a canvas ignores a stroke it cannot parse, so a
// stored "garbage" drew the lane in whatever colour the previous lane left.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
env.localStorage.setItem("mcuscope.colors", JSON.stringify(
  { bad: "garbage", short: "#abc", named: "red", good: "#12AB9f" }));
const { colorFor } = await import(webuiUrl("chrome.js"));

test("a saved colour that is not #rrggbb falls back to the name's palette colour", () => {
  for (const name of ["bad", "short", "named"]) {
    assert.match(colorFor(name), /^#[0-9a-f]{6}$/, name);
    assert.notEqual(colorFor(name), JSON.parse(env.store.get("mcuscope.colors"))[name], name);
  }
  assert.equal(colorFor("good"), "#12AB9f", "positive control: a well-formed override loads");
});
