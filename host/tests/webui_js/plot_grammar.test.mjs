// The browser half of the shared plot-grammar fixture (tests/plot_grammar_cases.json).
//
// protocol.py and plots.js decode the same grammar (SPEC 2.5) from two hand-written mirrors,
// in seven places: value grammar, name grammar, enum labels, bit lanes, channel spec,
// definition uniqueness, sample decode. Each has drifted at least once, and each drift shows
// up the same way: the browser charts a stream the daemon stored as a generic event, so the
// panel works until the page is reloaded and `mcu plot` shows nothing. csv_cell_cases.json
// closed that class for the CSV cell; this is the same treatment for the plot grammar.
//
// tests/test_plot_grammar_fixture.py runs the identical file against mcuscope.protocol.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
await import(webuiUrl("state.js"));
const { parsePlotDef, parsePlotAdhoc, decodePlotSample } = await import(webuiUrl("plots.js"));

const cases = JSON.parse(readFileSync(
  fileURLToPath(new URL("../plot_grammar_cases.json", import.meta.url)), "utf8"));

test("!pd definitions", () => {
  for (const c of cases.def) {
    assert.equal(parsePlotDef(c.line) !== null, c.valid, `${c.line} -- ${c.why}`);
  }
});

test("!p ad-hoc lines", () => {
  for (const c of cases.adhoc) {
    assert.equal(parsePlotAdhoc(c.line) !== null, c.valid, `${c.line} -- ${c.why}`);
  }
});

test("!ps samples against their definition", () => {
  for (const c of cases.sample) {
    const def = parsePlotDef(c.def);
    assert.ok(def, `the fixture's own definition must parse: ${c.def}`);
    assert.equal(decodePlotSample(c.line, def) !== null, c.decodes, `${c.line} -- ${c.why}`);
  }
});
