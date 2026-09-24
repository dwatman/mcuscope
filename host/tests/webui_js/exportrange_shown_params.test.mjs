// exportrange.js: the shown window as the daemon params it becomes.

import test from "node:test";
import assert from "node:assert/strict";
import { params, defaultRange, SHOWN_EDGE_S } from "../../mcuscope/webui/exportrange.js";

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
