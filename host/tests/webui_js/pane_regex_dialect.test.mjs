// The browser half of tests/regex_dialect_cases.json (the Python half is
// tests/test_pane_regex_dialect.py, against the daemon's own matcher).
//
// A pane filters with JavaScript's RegExp and its export sends the same source to the daemon's
// `regex` module, which nothing re-filters: with `\Aboot` the pane showed `Aboot` lines and the
// export held `boot ok`. So a pattern the two engines read differently is refused in the pane,
// visibly, and every pattern it accepts must match exactly the lines the daemon matches.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { installDom, webuiUrl, makePane } from "./dom_stub.mjs";

installDom();
const { regexDialectIssue } = await import(webuiUrl("pane.js"));
const { applyRegex } = await import(webuiUrl("terminal.js"));
const { canFilterPattern } = await import(webuiUrl("can.js"));

const cases = JSON.parse(readFileSync(
  fileURLToPath(new URL("../regex_dialect_cases.json", import.meta.url)), "utf8"));

test("every accepted pattern matches in the pane exactly the lines the daemon matches", () => {
  for (const { pattern, expect } of cases.same) {
    assert.equal(regexDialectIssue(pattern), null, `${pattern} was refused`);
    const pane = makePane();
    applyRegex(pane, pattern);
    assert.ok(pane.regex, `${pattern}: the pane did not arm it (${pane.matchInput.title})`);
    const got = cases.lines.map((l) => pane.regex.test(l));
    assert.deepEqual(got, expect, `${pattern} filters differently in the pane and the export`);
  }
});

test("every construct the engines read differently is refused, saying why", () => {
  for (const { pattern, why } of cases.refused) {
    const pane = makePane();
    applyRegex(pane, pattern);
    assert.equal(pane.regex, null, `${pattern} was armed (${why})`);
    assert.ok(pane.matchInput.classList.contains("invalid"), `${pattern}: the box must turn red`);
    assert.match(pane.matchInput.title, /read differently by the export filter|invalid pattern/, pattern);
  }
});

// Each refusal is warranted: read as the pane would have read it, the pattern matches some line
// differently from the daemon, or only one of the two engines accepts it. A backreference reads
// the same in both and is refused only with the octal forms it cannot be told from.
test("no pattern is refused for nothing", () => {
  for (const { pattern, daemon, why } of cases.refused) {
    let js;
    try { const re = new RegExp(pattern, "s"); js = cases.lines.map((l) => re.test(l)); } catch { js = "refused"; }
    if (why.startsWith("backreferences are refused as a class")) continue;
    assert.notDeepEqual(js, daemon, `${pattern} reads the same in both engines, so refusing it costs a user for nothing`);
  }
});

test("the refusal names the construct", () => {
  const pane = makePane();
  applyRegex(pane, "ERR\\Z");
  assert.ok(pane.matchInput.title.startsWith("\\Z is read differently by the export filter"),
    pane.matchInput.title);
});

test("the CAN id-click patterns stay in the shared dialect", () => {
  for (const e of [{ bus: 1, id: 0x7df, ext: false }, { bus: 2, id: 0x1abcdef, ext: true }]) {
    assert.equal(regexDialectIssue(canFilterPattern(e)), null, canFilterPattern(e));
  }
});
