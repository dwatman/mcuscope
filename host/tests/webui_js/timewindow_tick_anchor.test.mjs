// timewindow.js: tick-anchor thinning and its cap.

import test from "node:test";
import assert from "node:assert/strict";
import { newTickAnchors, noteTickAnchor } from "../../mcuscope/webui/timewindow.js";

test("a consistent anchor under a second after the last is skipped, one past a second is kept", () => {
  const a = newTickAnchors();
  noteTickAnchor(a, "p", 1, 100, 5000);
  noteTickAnchor(a, "p", 2, 100.5, 5500);   // predicted, 0.5 s on
  assert.equal(a.get("p").length, 1, "an anchor the previous one predicts adds nothing");
  noteTickAnchor(a, "p", 3, 101.5, 6500);   // predicted, 1.5 s after the kept one
  assert.deepEqual(a.get("p").map((x) => x.id), [1, 3], "a continuing clock still gets one a second");
});

test("the anchor list is capped per port, oldest first", () => {
  const a = newTickAnchors();
  for (let i = 0; i < 10005; i++) noteTickAnchor(a, "p", i + 1, 100 + i * 0.001, i % 2 ? 0 : 1e6);
  const list = a.get("p");
  assert.equal(list.length, 10000);
  assert.equal(list[0].id, 6, "the five oldest anchors go");
  assert.equal(list.at(-1).id, 10005);
});
