// state.js lineTick on a firmware marker, against protocol.parse_marker (the Python half is
// tests/test_protocol_tokenizer.py test_marker_tick_is_a_space_delimited_token, over the same
// whitespace set): the tick is the first token after "!m ", split on runs of spaces only, so
// `!m \t@5 hi` is a marker whose text is `\t@5 hi` and which has no tick. The terminal's divider
// strips the same prefix.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow } from "./dom_stub.mjs";

installDom();
const { lineTick } = await import(webuiUrl("state.js"));
const { render } = await import(webuiUrl("terminal.js"));

// Every whitespace but the space: JS `\s` (which also holds U+FEFF), plus what Python's
// str.split() splits on and `\s` lacks, U+001C-001F and U+0085.
const NON_SPACE_WS = [
  ...Array.from({ length: 0x10000 }, (_, i) => String.fromCharCode(i)).filter((c) => /\s/.test(c)),
  "\x1c", "\x1d", "\x1e", "\x1f", "\x85",
].filter((c) => c !== " ");
let id = 1;
const tick = (raw) => lineTick({ id: id++, ts: 100, port: "p1", chan: "marker", raw });

function divider(raw) {
  const pane = makePane();
  pane.rows = [makeRow(id++, { chan: "marker", raw })];
  render(pane);
  return pane.vlist.children[0].children[1].textContent;
}

test("a non-space byte before the tick word makes it text, as the daemon reads it", () => {
  for (const ws of NON_SPACE_WS) {
    const raw = `!m ${ws}@5 hi`;
    assert.equal(tick(raw), null, JSON.stringify(raw));
    assert.equal(divider(raw), `marker: ${ws}@5 hi`, "the divider must not strip a tick the line has not got");
  }
  assert.equal(tick("!m   @5   hi"), 5, "positive control: spaces around the tick");
  assert.equal(divider("!m   @5   hi"), "marker: hi");
});

test("the tag is the line's first bytes, not its first token", () => {
  assert.equal(tick(" !m @5 hi"), null, "parse_marker partitions at the first space: no `!m` head");
  assert.equal(tick("!m @5 hi\r"), 5, "one line terminator is dropped, as normalize_line");
});
