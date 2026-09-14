// Pre-release round 2026-09-15: tick-anchor thinning and its cap (F-19, F-20), and the shown
// window as the daemon params it becomes (D-2).

import test from "node:test";
import assert from "node:assert/strict";
import { newTickAnchors, noteTickAnchor } from "../../mcuscope/webui/timewindow.js";
import { params, defaultRange, SHOWN_EDGE_S } from "../../mcuscope/webui/exportrange.js";

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

test("the shown window goes as since_ts just below its first edge and until_ts at its last", () => {
  const p = params({ ...defaultRange(), mode: "shown" },
                   { watermark: 42, shown: { fromTs: 1000.25, toTs: 1003.5 } });
  assert.equal(p.get("since_ts"), String(1000.25 - SHOWN_EDGE_S),
    "since_ts is exclusive at the daemon, so a row at exactly the first edge must still be in");
  assert.ok(Number(p.get("since_ts")) < 1000.25);
  assert.equal(p.get("until_ts"), "1003.5");
  assert.equal(p.get("id_to"), "42");
  assert.equal(p.has("last_ms"), false, "a duration is measured from the id_to row, not the surface");
  const none = params({ ...defaultRange(), mode: "shown" }, { watermark: 42, shown: null });
  assert.equal(none.has("since_ts") || none.has("until_ts"), false, "no window, no bound");
  const clock = params({ mode: "clock", session: null, fromTs: 5, toTs: 6 },
                       { watermark: null, shown: { fromTs: 1, toTs: 2 } });
  assert.equal(clock.get("since_ts"), "5", "the shown window applies to the shown mode only");
});
