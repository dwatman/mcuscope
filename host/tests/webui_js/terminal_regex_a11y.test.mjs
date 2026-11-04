// terminal.js applyRegex and the pane template: the regex box has an accessible name of its own,
// and says when it is refusing the pattern. Its name was the placeholder, and its refusal only
// a red border plus a title, which a screen reader reads as the box's description at best.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiDir, webuiUrl, makePane } from "./dom_stub.mjs";
import { join } from "node:path";

installDom();
await import(webuiUrl("state.js"));
const { applyRegex } = await import(webuiUrl("terminal.js"));

test("the template's regex box is named", () => {
  const html = readFileSync(join(webuiDir(), "index.html"), "utf8");
  const tag = /<input\b[^>]*\bclass="mini match"[^>]*>/.exec(html);
  assert.ok(tag, "no regex box in the pane template");
  assert.match(tag[0], /\baria-label="Regex filter"/);
});

test("a refused pattern sets aria-invalid, and an accepted or cleared one removes it", () => {
  const pane = makePane();
  const inp = pane.matchInput;
  applyRegex(pane, "(");
  assert.equal(inp.getAttribute("aria-invalid"), "true", "an invalid pattern");
  applyRegex(pane, "ERR|WARN");
  assert.equal(inp.hasAttribute("aria-invalid"), false, "an accepted pattern");
  applyRegex(pane, "x".repeat(5000));
  assert.equal(inp.getAttribute("aria-invalid"), "true", "a pattern over the length cap");
  applyRegex(pane, "");
  assert.equal(inp.hasAttribute("aria-invalid"), false, "a cleared box");
  applyRegex(pane, "\\Z");
  assert.equal(inp.getAttribute("aria-invalid"), "true", "a construct the engines read differently");
});
