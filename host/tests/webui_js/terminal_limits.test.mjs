// terminal.js and pane.js (registry class 74): a shown limit against the figure it is
// enforced on.
//
// - The regex box refused a pattern past 200 "chars" counted in UTF-16 code units, while the
//   daemon it mirrors counts code points: a 150-emoji pattern read "too long (max 200 chars)".
// - The history walk's budget divider counted every capture line below the page, including
//   the ones at or below the pane's clear point, which the pane never loads.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow } from "./dom_stub.mjs";
import { HISTORY_PAGE, HISTORY_MAX } from "../../mcuscope/webui/pane.js";

installDom();

let dbMax = 0;
globalThis.fetch = async (url) => {
  const q = new URL(url, "http://x");
  const idTo = Number(q.searchParams.get("id_to"));
  const since = Number(q.searchParams.get("since_id") || 0);
  const limit = Number(q.searchParams.get("limit"));
  const ids = [];
  for (let id = Math.min(idTo, dbMax); id > since && ids.length < limit + 1; id--) ids.push(id);
  const lines = ids.slice(0, limit).map((id) => makeRow(id));
  return { ok: true, status: 200, headers: { get: () => null },
           json: async () => ({ lines, truncated: ids.length > limit }) };
};

const { state } = await import(webuiUrl("state.js"));
const { applyRegex, loadHistory } = await import(webuiUrl("terminal.js"));

test("the pattern length cap counts characters, as the daemon's does", () => {
  const pane = makePane();
  applyRegex(pane, "\u{1F600}".repeat(150));   // 150 characters, 300 UTF-16 code units
  assert.ok(pane.regex, `a 150-character pattern was refused: ${pane.matchInput.title}`);
  applyRegex(pane, "\u{1F600}".repeat(201));
  assert.equal(pane.regex, null);
  assert.equal(pane.matchInput.title, "pattern too long (max 200 chars)");
});

test("the budget divider counts only the lines above the pane's clear point", async () => {
  dbMax = 7000;
  state.maxId = 7000;
  const pane = makePane({ autoscroll: false, clearId: 1000, historyLoaded: HISTORY_MAX - HISTORY_PAGE });
  pane.rows = Array.from({ length: 10 }, (_, i) => makeRow(6001 + i));
  await loadHistory(pane);
  assert.equal(pane.rows[0].chan, "gap", "the fixture did not spend the budget");
  assert.equal(pane.rows[0].raw, "gap: 4800 lines not loaded",
    "ids 1001..5800 are not loaded; 1..1000 were cleared and never will be");
});
