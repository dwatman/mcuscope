// A pane showing nothing must say why (pane.js emptyPaneText), the footer hint must not
// promise history a paused pane cannot load (paneHint), and the port tag column follows the
// number of attached ports. Each message is asserted by text unique to its cause, since
// "empty" is exactly the state where two causes are otherwise indistinguishable.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { emptyPaneText, paneHint, HISTORY_PAGE } = await import(webuiUrl("pane.js"));
const { state, buffer, pushBuffer } = await import(webuiUrl("state.js"));
const before = env.intervals.length;
const { panes, rebuild, render, scheduleFlush, setKnownPorts, setAutoscroll } =
  await import(webuiUrl("terminal.js"));
const emptyTimer = env.intervals.slice(before).find((i) => String(i.fn).includes("renderEmpty"));

const shown = (pane) => {
  const kids = pane.vlist.children;
  return kids.length === 1 && kids[0].className === "empty-state" ? kids[0].textContent : null;
};
const base = { total: 0, scoped: 0, cleared: false, ports: 1, port: "all", channels: 6, regex: false };

test("the copy names each cause, and a non-empty pane has none", () => {
  assert.match(emptyPaneText({ ...base, ports: 0 }), /^No lines yet\. Attach a serial port/);
  assert.equal(emptyPaneText(base), "Waiting for the first line.");
  assert.match(emptyPaneText({ ...base, cleared: true, ports: 0 }), /^Cleared\./,
    "a cleared pane is cleared, not an onboarding prompt, even with no ports");
  assert.equal(emptyPaneText({ ...base, total: 5, channels: 0 }),
    "No channels ticked: tick one above to show lines.");
  assert.equal(emptyPaneText({ ...base, total: 5 }), "5 lines, none on the ticked channels.");
  assert.equal(emptyPaneText({ ...base, total: 5, port: "mcu" }),
    "No lines from mcu on the ticked channels.");
  assert.equal(emptyPaneText({ ...base, total: 5, scoped: 3, regex: true }),
    "3 lines in scope, none match the regex.");
  assert.equal(emptyPaneText({ ...base, total: 5, scoped: 3 }), null,
    "rows in scope and no pattern: nothing would make this pane empty, so no message");
});

test("the hint promises older lines only where a top hit can fetch them", () => {
  const pane = makePane();
  assert.equal(paneHint(pane), "dbl-click a line to copy");
  pane.autoscroll = false;
  assert.equal(paneHint(pane), "no older lines to load", "an empty paused pane has nothing to page from");
  pane.rows = [makeRow(50)];
  assert.equal(paneHint(pane), "scroll to the top for older lines");
  pane.historyBusy = true;
  assert.equal(paneHint(pane), "loading older lines...");
  pane.historyBusy = false;
  pane.historyDone = true;
  assert.equal(paneHint(pane), "no older lines to load");
  pane.historyDone = false;
  pane.clearId = 49;   // cleared at the oldest row: the floor forbids refilling what was cleared
  assert.equal(paneHint(pane), "no older lines to load");
  assert.ok(HISTORY_PAGE > 0);
});

test("a rendered empty pane shows its cause and follows the state it is in", async () => {
  buffer.length = 0;
  state.knownAliases = [];
  const pane = makePane();
  panes.push(pane);
  rebuild(pane);
  assert.match(shown(pane), /^No lines yet\./);
  assert.equal(pane.hintEl.textContent, "dbl-click a line to copy");

  setKnownPorts(["mcu"]);
  emptyTimer.fn();   // the 1 s refresh: nothing re-renders an empty pane on its own
  assert.equal(shown(pane), "Waiting for the first line.");

  pushBuffer(makeRow(1, { port: "mcu", chan: "debug", raw: "boot" }));
  pane.channels = new Set(["resp"]);
  emptyTimer.fn();
  assert.equal(shown(pane), "1 lines, none on the ticked channels.",
    "a line that arrived but does not match must not leave 'waiting' on screen");

  pane.channels = new Set();
  rebuild(pane);
  assert.match(shown(pane), /^No channels ticked/);

  pane.channels = new Set(["debug"]);
  pane.regex = /nomatch/; pane.regexSrc = "nomatch";
  rebuild(pane);
  assert.equal(shown(pane), "1 lines in scope, none match the regex.");

  // A matching line through the live append path must replace the message, not sit under it.
  pane.regex = null; pane.regexSrc = "";
  pane.channels = new Set(["debug"]);
  pane.rows = []; render(pane);
  const row = makeRow(2, { port: "mcu", raw: "hello" });
  pushBuffer(row);
  pane.queue.push(row);
  scheduleFlush();
  await tick(50);
  assert.equal(shown(pane), null);
  assert.equal(pane.vlist.children.length, 1);
  assert.equal(pane.vlist.children[0].__row, row);

  pane.clearId = state.maxId; pane.rows = []; render(pane);
  assert.match(shown(pane), /^Cleared\./);
  panes.splice(panes.indexOf(pane), 1);
});

test("pausing moves the hint to history, and resuming brings the copy hint back", () => {
  buffer.length = 0;
  pushBuffer(makeRow(10, { port: "mcu" }));
  const pane = makePane();
  panes.push(pane);
  rebuild(pane);
  setAutoscroll(pane, false);
  assert.equal(pane.hintEl.textContent, "scroll to the top for older lines");
  setAutoscroll(pane, true);
  assert.equal(pane.hintEl.textContent, "dbl-click a line to copy");
  panes.splice(panes.indexOf(pane), 1);
});

test("the port tag column appears with a second port and goes when it leaves", () => {
  buffer.length = 0;
  setKnownPorts(["a"]);
  for (let i = 1; i <= 3; i++) pushBuffer(makeRow(100 + i, { port: i === 2 ? "b" : "a" }));
  const pane = makePane();
  panes.push(pane);
  rebuild(pane);
  const tags = () => pane.vlist.children.map((ln) => ln.children.filter((c) => c.className === "port-tag").length);
  assert.deepEqual(tags(), [0, 0, 0], "one port attached: no tag column");

  setKnownPorts(["a", "b"]);
  assert.deepEqual(tags(), [1, 1, 1], "the second port must re-render the rows already on screen");

  setKnownPorts(["a", "c"]);   // a different second port: still two, nothing to redo
  assert.deepEqual(tags(), [1, 1, 1]);

  setKnownPorts(["a"]);
  assert.deepEqual(tags(), [0, 0, 0], "the tag column goes when the second port does");

  pane.port = "a";
  setKnownPorts(["a", "b"]);
  assert.deepEqual(tags().slice(0, 2), [0, 0], "a pane filtered to one port never shows the tag");
  panes.splice(panes.indexOf(pane), 1);
  setKnownPorts([]);
});
