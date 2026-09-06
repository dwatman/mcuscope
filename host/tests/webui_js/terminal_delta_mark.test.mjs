// terminal.js: the delta timestamp column, the <mark> around a regex hit, and the
// "shown / total" readout a pattern turns the line count into.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { fmtDelta } = await import(webuiUrl("timewindow.js"));
const { state, buffer } = await import(webuiUrl("state.js"));
const { render, rebuild, applyRegex, matches, refillRegexBudget } =
  await import(webuiUrl("terminal.js"));

test("fmtDelta is the gap to the previous row, signed only when negative", () => {
  assert.equal(fmtDelta(1000.041, 1000), "+0.041s");
  assert.equal(fmtDelta(1000, null), "+0.000s", "the first row has nothing above it");
  assert.equal(fmtDelta(1000, 1000), "+0.000s");
  assert.equal(fmtDelta(999.5, 1000), "-0.500s", "a backfilled row can land behind its neighbour");
  assert.equal(fmtDelta(1012.3456, 1000), "+12.346s");
});

function tsColumn(pane) {
  render(pane);
  return pane.vlist.children.map((ln) => ln.children[0].textContent);
}

test("delta mode prints each row relative to the row displayed above it", () => {
  const pane = makePane();
  pane.rows = [makeRow(1, { ts: 1000 }), makeRow(2, { ts: 1000.041 }), makeRow(3, { ts: 1002 })];
  state.timeMode = "delta";
  try {
    assert.deepEqual(tsColumn(pane), ["+0.000s", "+0.041s", "+1.959s"]);
  } finally {
    state.timeMode = "host";
  }
});

test("delta mode reads against the pane's previous row, not the DOM window's first", () => {
  // The virtualizer renders a window into pane.rows; the first rendered row still has a
  // predecessor in the pane, and its delta is against that row.
  const pane = makePane({ autoscroll: true });
  pane.rows = Array.from({ length: 500 }, (_, i) => makeRow(i + 1, { ts: 1000 + i * 0.25 }));
  state.timeMode = "delta";
  try {
    const col = tsColumn(pane);
    assert.ok(pane.winFirst > 0, "the fixture must render a window that does not start at row 0");
    assert.equal(col[0], "+0.250s");
    assert.ok(col.every((c) => c === "+0.250s"), col.join(","));
  } finally {
    state.timeMode = "host";
  }
});

function msgOf(pane, i) {
  render(pane);
  const ln = pane.vlist.children[i];
  return ln.children.find((c) => c.className === "msg");
}

test("a regex hit is wrapped in <mark>, the rest of the line as plain text", () => {
  const pane = makePane();
  applyRegex(pane, "ERR \\d+");
  pane.rows = [makeRow(1, { chan: "resp", raw: "<1 ERR 3 bad args" }),
               makeRow(2, { chan: "resp", raw: "<2 OK" })];
  const msg = msgOf(pane, 0);
  assert.equal(msg.textContent, "<1 ERR 3 bad args", "the text must read whole");
  const mark = msg.children.find((c) => c.tagName === "MARK");
  assert.ok(mark, "the hit is not marked");
  assert.equal(mark.textContent, "ERR 3");
  assert.deepEqual(msg.children.map((c) => c.textContent), ["<1 ", "ERR 3", " bad args"]);
  const plain = msgOf(pane, 1);
  assert.equal(plain.children.length, 0, "a row with no hit carries no mark");
  assert.equal(plain.textContent, "<2 OK");
});

test("an empty match marks nothing", () => {
  const pane = makePane();
  applyRegex(pane, "x*");   // matches the empty string at 0
  pane.rows = [makeRow(1, { raw: "abc" })];
  const msg = msgOf(pane, 0);
  assert.equal(msg.children.length, 0);
  assert.equal(msg.textContent, "abc");
});

test("the mark is charged to the same budget as the filter, and a render refills it", () => {
  const pane = makePane();
  applyRegex(pane, "^line");
  pane.rows = Array.from({ length: 40 }, (_, i) => makeRow(i + 1));
  for (let i = 0; i < 200; i++) render(pane);   // paused pane re-rendered on scroll: no live refill
  assert.ok(pane.regex, "an ordinary pattern must survive any number of renders");
  const evil = makePane();
  applyRegex(evil, "(a+)+$");
  evil.rows = [makeRow(1, { raw: "a".repeat(26) + "b" })];
  render(evil);
  assert.equal(evil.regex, null, "a backtracking pattern must be dropped by the mark path too");
  assert.equal(evil.matchInput.classList.contains("invalid"), true);
  refillRegexBudget(evil);
  assert.equal(matches(evil, makeRow(2, { raw: "anything" })), true, "dropped means unfiltered");
});

test("with a pattern set the readout says shown / rows in scope", () => {
  buffer.length = 0;
  for (let i = 1; i <= 10; i++) {
    buffer.push(makeRow(i, { chan: i > 8 ? "resp" : "debug", raw: i % 2 ? "odd" : "even" }));
  }
  const pane = makePane();
  rebuild(pane);
  assert.equal(pane.shownEl.textContent, "10 lines", "no pattern: the plain count");
  applyRegex(pane, "odd");
  rebuild(pane);
  assert.equal(pane.shownEl.textContent, "5 / 10 lines");
  pane.channels = new Set(["debug"]);
  rebuild(pane);
  assert.equal(pane.shownEl.textContent, "4 / 8 lines",
    "the total is what the pattern chooses from: rows passing the port and channel filters");
  pane.clearId = 4;
  rebuild(pane);
  assert.equal(pane.shownEl.textContent, "2 / 4 lines", "cleared rows are out of the total too");
});
