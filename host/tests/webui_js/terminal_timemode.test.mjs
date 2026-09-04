// terminal.js: the stored time base is an enum, and the label is derived from it.
//
// timeMode was type-checked out of localStorage but not range-checked, so a hand-edited
// termState (or one written by a build that added a fourth mode) put an unknown string into
// state.timeMode: no button in #timeSeg lit, and the plots header read the literal string
// "undefined" while every consumer silently fell back to host time (REVIEW class 34).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

localStorage.setItem("termState", JSON.stringify({ timeMode: "abs", panes: [] }));

const { state } = await import(webuiUrl("state.js"));
const { initTerminal } = await import(webuiUrl("terminal.js"));

test("a stored time mode outside the enum falls back to host, label included", () => {
  initTerminal();
  assert.equal(state.timeMode, "host", "an unknown mode must not become the time base");
  assert.equal(env.byId("plotXLabel").textContent, "x: host",
    "the header read \"undefined\": the label is looked up by the stored value");
});
