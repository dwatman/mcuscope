// state.js splitTokens, which plots.js and can.js decode with, splits a line on runs of U+0020
// only, as protocol.split_tokens does (SPEC 2.1). With /\s+/ the browser split on a tab where the daemon
// kept it inside a token, and the daemon (str.split) split on 0x1C-0x1F where the browser did not:
// either way one side charted or tabled a line the other stored as a generic event. The decoders
// also publish the tick of the lines they accept (state.js lineTick reads it through hooks).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
const { hooks, splitTokens } = await import(webuiUrl("state.js"));
const { parsePlotAdhoc, parsePlotDef, decodePlotSample } = await import(webuiUrl("plots.js"));
await import(webuiUrl("can.js"));

test("tokens split on spaces only, runs collapse, one line terminator is dropped", () => {
  assert.deepEqual(splitTokens("  !p  1   a=1  "), ["!p", "1", "a=1"]);
  assert.deepEqual(splitTokens("!p\t1 a=1"), ["!p\t1", "a=1"]);
  assert.deepEqual(splitTokens("!p 1 a=1\r\n"), ["!p", "1", "a=1"]);
  assert.deepEqual(splitTokens("!p 1 a=1\r\r"), ["!p", "1", "a=1\r"], "only one terminator, as normalize_line");
});

test("a tab or a 0x1F byte is part of a token, so the line is no sample", () => {
  assert.ok(parsePlotAdhoc("!p  1  a=1"), "space runs are fine");
  assert.equal(parsePlotAdhoc("!p\t1 a=1"), null);
  assert.equal(parsePlotAdhoc("!p 1\ta=1"), null);
  assert.equal(parsePlotDef("!pd 3 c:s1 \x1fdZ:u4"), null, "the daemon keeps \\x1f inside the name");
  assert.equal(parsePlotDef("!pd 3 c:s1\x1fdZ:u4"), null);
  const def = parsePlotDef("!pd 0 v:u1");
  assert.ok(decodePlotSample("!ps 0 1 01", def));
  assert.equal(decodePlotSample("!ps 0 1\t01", def), null);
});

test("the tick hooks answer for exactly the lines their decoder accepts", () => {
  assert.equal(hooks.adhocTick("!p 42 a=1"), 42);
  assert.equal(hooks.adhocTick("!p\t42 a=1"), null);
  assert.equal(hooks.adhocTick("!p 42 a=x"), null, "a line the decoder rejects anchors nothing");
  assert.equal(hooks.canTick("!can 77 - 100 DEAD"), 77);
  assert.equal(hooks.canTick("!can2 77 x 1ABCDEF -"), 77);
  assert.equal(hooks.canTick("!can\t77 - 100 DEAD"), null);
  assert.equal(hooks.canTick("!can 4000000000 zz 100 DEAD"), null, "bad flags: no frame, no tick");
});
