// The timestamp column: `.ln .ts` had no width, so under rel a pane's rows shifted right where
// a stamp gained a digit (9.901s above 11.901s). pane.js tsColumnWidth is the rule; these try
// to make it shrink, keep a stale width across a time base, or miss a mode's widest form.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, makePane, makeRow } from "./dom_stub.mjs";
import { tsColumnWidth } from "../../mcuscope/webui/pane.js";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { state } = await import(webuiUrl("state.js"));
const { render } = await import(webuiUrl("terminal.js"));

// Feed stamps in order; the widths after each.
function widths(mode, stamps, prev = null) {
  const out = [];
  for (const s of stamps) { prev = tsColumnWidth(prev, mode, s); out.push(prev.ch); }
  return out;
}

test("each time base's stamps: the width follows the widest seen and never narrows", () => {
  assert.deepEqual(widths("host", ["09:59:59.999", "10:00:00.000"]), [12, 12]);
  assert.deepEqual(widths("rel", ["9.901s", "11.901s", "9.999s", "-0.250s", "123.000s"]),
    [6, 7, 7, 7, 8]);
  assert.deepEqual(widths("delta", ["+0.000s", "-0.001s", "+12.500s", "+0.100s"]), [7, 7, 8, 8]);
  // tick: the estimate's `~`, the no-anchor `~-`, a gap's `-`, and a 2^32 - 1 tick.
  assert.deepEqual(widths("tick", ["~-", "0", "~750", "-", "~12345", "4294967295", "~-"]),
    [2, 2, 4, 4, 6, 10, 10]);
});

test("an unchanged width is the same object, and a new time base starts from its own stamps", () => {
  const rel = tsColumnWidth(null, "rel", "11.901s");
  assert.equal(tsColumnWidth(rel, "rel", "1.000s"), rel, "the caller skips the style write on identity");
  assert.deepEqual(tsColumnWidth(rel, "tick", "~5"), { mode: "tick", ch: 2 },
    "rel's 7ch must not pad a tick column that needs 2");
  assert.deepEqual(tsColumnWidth(rel, "host", "10:00:00.000"), { mode: "host", ch: 12 });
});

test("a rendered pane: one width for the window, kept while scrolling back, reset by the mode", () => {
  state.timeMode = "rel"; state.anchorTs = 1000;
  const pane = makePane();
  // 8.700s .. 11.900s: the live window straddles the digit.
  pane.rows = Array.from({ length: 120 }, (_, i) => makeRow(i + 1, { ts: 1000 + i * 0.1 }));
  pane.rows.splice(100, 0, makeRow(1000, { ts: 1010, chan: "marker", raw: "!m mark" }));
  render(pane);
  assert.equal(pane.scrollEl.style["--ts-ch"], "7ch");
  const stamps = pane.vlist.children.map((ln) => ln.children[0]);
  assert.ok(stamps.every((s) => s.className === "ts"), "the marker divider keeps the ts cell first");
  assert.ok(stamps.some((s) => s.textContent === "9.900s") && stamps.some((s) => s.textContent === "11.900s"));

  pane.autoscroll = false; pane.scrollEl.scrollTop = 0;   // back to 0.000s .. 3.200s
  render(pane);
  assert.equal(pane.vlist.children[0].children[0].textContent, "0.000s");
  assert.equal(pane.scrollEl.style["--ts-ch"], "7ch", "scrolling back to short stamps narrowed it");

  const other = makePane({ autoscroll: false });
  other.rows = pane.rows.slice(0, 5);
  render(other);
  assert.equal(other.scrollEl.style["--ts-ch"], "6ch", "one pane's width reached another");

  state.timeMode = "host";
  render(pane);
  assert.equal(pane.scrollEl.style["--ts-ch"], "12ch");
  state.timeMode = "rel";
  render(pane);
  assert.equal(pane.scrollEl.style["--ts-ch"], "6ch", "a returning time base starts from its window");
  state.timeMode = "host"; state.anchorTs = null;
});

test("style.css: the column takes the width right-aligned, and no row kind overrides it", () => {
  const css = readFileSync(new URL("../../mcuscope/webui/style.css", import.meta.url), "utf8");
  const rule = css.match(/^\.ln \.ts \{([^}]*)\}/m);
  assert.ok(rule, ".ln .ts rule");
  assert.match(rule[1], /min-width: var\(--ts-ch, 0\)/);
  assert.match(rule[1], /text-align: right/);
  assert.match(rule[1], /flex: none/);
  const others = css.match(/[^\n}]*\.ts\b[^{]*\{[^}]*\}/g).filter((r) => !/^\.ln \.ts \{/.test(r.trim()));
  assert.ok(others.every((r) => !/width|text-align/.test(r)), others.join("\n"));
});
