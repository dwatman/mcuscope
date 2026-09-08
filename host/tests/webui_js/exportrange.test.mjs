// exportrange.js: the range one export dialog remembers, and the params it becomes.
//
// DOM-free, so it is driven directly. What is worth breaking here is the storage read (the
// key is hand-editable and survives a version change) and the watermark rule, which is the
// only thing standing between a paused surface and an export of the live edge.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
const { defaultRange, validate, loadRange, saveRange, reset, inverted, params, MODES } =
  await import(webuiUrl("exportrange.js"));

const KEY = "mcuscope.exportRange";

test("the default range is the open session and nothing else", () => {
  assert.deepEqual(defaultRange(), { mode: "session", session: null, fromTs: null, toTs: null });
  assert.deepEqual(reset(), defaultRange(), "the whole-session button must return the default");
});

test("a saved range comes back as it went in", () => {
  env.localStorage.clear();
  saveRange({ mode: "clock", session: null, fromTs: 1700000000, toTs: 1700003600 });
  assert.deepEqual(loadRange(),
    { mode: "clock", session: null, fromTs: 1700000000, toTs: 1700003600 });
  saveRange({ mode: "session", session: "7", fromTs: null, toTs: null });
  assert.equal(loadRange().session, "7");
  env.localStorage.clear();
});

test("nothing readable in storage falls back to the default, whole", () => {
  for (const poison of [
    "not json at all",
    "null",
    "[]",
    '"session"',
    '{"mode":"whenever"}',                             // unknown mode
    '{"mode":"clock","fromTs":"yesterday","toTs":5}',  // one good field, one not
    '{"mode":"clock","fromTs":null,"toTs":null}',      // no bounds under a bounded mode
    '{"session":3}',                                   // no mode
  ]) {
    env.localStorage.setItem(KEY, poison);
    const got = loadRange();
    assert.ok(MODES.includes(got.mode), `${poison} produced mode ${got.mode}`);
    if (poison.includes("yesterday")) {
      assert.equal(got.fromTs, null,
        "a half-valid clock range must not survive: the export would cover a span nobody picked");
      assert.equal(got.toTs, 5, "the readable half of a mixed range stays");
    }
  }
  env.localStorage.setItem(KEY, "not json at all");
  assert.deepEqual(loadRange(), defaultRange());
  env.localStorage.clear();
});

test("a session ref is only sent once one is picked", () => {
  const p = params({ ...defaultRange() }, {});
  assert.equal([...p].length, 0, "the default range must send nothing: the daemon's own default");
  const q = params({ ...defaultRange(), session: "3" }, {});
  assert.equal(q.get("session"), "3");
  assert.equal(q.has("since_ts"), false);
});

test("clock mode sends the bounds it has, and only those", () => {
  const both = params({ mode: "clock", session: null, fromTs: 100.5, toTs: 200.5 }, {});
  assert.equal(both.get("since_ts"), "100.5");
  assert.equal(both.get("until_ts"), "200.5");
  const openEnded = params({ mode: "clock", session: null, fromTs: 100.5, toTs: null }, {});
  assert.equal(openEnded.get("since_ts"), "100.5");
  assert.equal(openEnded.has("until_ts"), false, "an unset side must stay unbounded");
  assert.equal(openEnded.has("session"), false, "a clock range is not scoped to a session");
});

test("shown mode is the panel's own window", () => {
  const p = params({ ...defaultRange(), mode: "shown" }, { watermark: 42, shownLastMs: 30000 });
  assert.equal(p.get("last_ms"), "30000");
  assert.equal(p.get("id_to"), "42");
  const noSpan = params({ ...defaultRange(), mode: "shown" }, { watermark: 42 });
  assert.equal(noSpan.has("last_ms"), false, "a panel with no window must not send last_ms=null");
  assert.equal(params({ ...defaultRange(), mode: "shown" },
    { watermark: 42, shownLastMs: 1500.6 }).get("last_ms"), "1501",
    "last_ms is milliseconds and must go out whole");
});

test("the watermark bounds EVERY mode, not just the shown window", () => {
  for (const range of [
    { mode: "session", session: null, fromTs: null, toTs: null },
    { mode: "session", session: "3", fromTs: null, toTs: null },
    { mode: "clock", session: null, fromTs: 100, toTs: 200 },
    { mode: "clock", session: null, fromTs: 100, toTs: null },
    { mode: "shown", session: null, fromTs: null, toTs: null },
  ]) {
    const p = params(range, { watermark: 99, shownLastMs: 5000 });
    assert.equal(p.get("id_to"), "99",
      `${range.mode} lost the freeze bound: a paused surface would export past what it shows`);
  }
});

test("a live surface sends no bound at all", () => {
  for (const mode of MODES) {
    const p = params({ ...defaultRange(), mode, fromTs: 1, toTs: 2 },
                     { watermark: null, shownLastMs: 5000 });
    assert.equal(p.has("id_to"), false, `${mode} invented a bound for a live surface`);
  }
});

test("a watermark of 0 is a bound, not an absent one", () => {
  // freeze.js answers 0 for a member frozen before it held any row; that exports nothing,
  // which is correct, and must not be read as "live" the way a falsy check would.
  const p = params(defaultRange(), { watermark: 0 });
  assert.equal(p.get("id_to"), "0");
});

test("inverted clock bounds are refused, and only clock bounds can invert", () => {
  assert.equal(inverted({ mode: "clock", session: null, fromTs: 200, toTs: 100 }), true);
  assert.equal(inverted({ mode: "clock", session: null, fromTs: 100, toTs: 100 }), false,
    "an instant is an empty range, not an inverted one");
  assert.equal(inverted({ mode: "clock", session: null, fromTs: 200, toTs: null }), false,
    "one-sided cannot invert");
  assert.equal(inverted({ mode: "session", session: null, fromTs: 200, toTs: 100 }), false,
    "stale clock bounds under another mode are not sent, so they cannot be refused either");
});

test("validate round-trips through storage twice without drifting", () => {
  env.localStorage.clear();
  const r = { mode: "clock", session: 12, fromTs: 5, toTs: 6 };
  saveRange(r);
  const once = loadRange();
  saveRange(once);
  assert.deepEqual(loadRange(), once, "a second save must not renumber or re-type anything");
  assert.equal(validate(r).session, "12", "a numeric session id is kept as its string ref");
  env.localStorage.clear();
});
