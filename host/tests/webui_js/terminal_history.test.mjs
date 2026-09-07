// terminal.js loadHistory + pane.js historyIdTo / planHistoryPage: scrolling a pane to its
// top pulls one older page from the capture, prepends it, and moves the scroll offset by
// what was added. The paging arithmetic is DOM-free (pane.js) and driven directly below;
// the fetch path is driven through a fake /lines.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";
import { historyIdTo, planHistoryPage, HISTORY_PAGE, HISTORY_MAX, HISTORY_HOPS, gapRow }
  from "../../mcuscope/webui/pane.js";

installDom();

// A fake capture: ids 1..dbMax, /lines with id_to inclusive, desc, limit clamped at 1000.
let dbMax = 0;
let queries = [];
let failMatch = false;   // refuse a request carrying match=, as a dialect the daemon rejects
let chanOf = () => "debug";

globalThis.fetch = async (url) => {
  queries.push(url);
  const q = new URL(url, "http://x");
  if (failMatch && q.searchParams.has("match")) {
    return { ok: false, status: 400, headers: { get: () => null },
             json: async () => ({ error: "bad match regex" }) };
  }
  const idTo = q.searchParams.has("id_to") ? Number(q.searchParams.get("id_to")) : dbMax;
  const since = q.searchParams.has("since_id") ? Number(q.searchParams.get("since_id")) : 0;
  const limit = Math.min(Number(q.searchParams.get("limit") || 100), 1000);
  const port = q.searchParams.get("port");
  const chans = q.searchParams.getAll("chan");
  const ids = [];
  for (let id = Math.min(idTo, dbMax); id > since && ids.length < limit + 1; id--) {
    if (chans.length && !chans.includes(chanOf(id))) continue;
    ids.push(id);
  }
  const truncated = ids.length > limit;
  const lines = ids.slice(0, limit).map((id) => makeRow(id, { chan: chanOf(id), port: port || "p1" }));
  return { ok: true, status: 200, headers: { get: () => null }, json: async () => ({ lines, truncated }) };
};

const { state, buffer } = await import(webuiUrl("state.js"));
const { loadHistory, applyRegex } = await import(webuiUrl("terminal.js"));

const ids = (rows) => rows.map((r) => r.id);

function freshPane(over = {}) {
  const pane = makePane({ autoscroll: false, ...over });
  return pane;
}

// ---- the arithmetic ----------------------------------------------------------------

test("historyIdTo asks for the page below the oldest row, and not otherwise", () => {
  const pane = freshPane();
  assert.equal(historyIdTo(pane), null, "an empty pane has nothing to page from");
  pane.rows = [makeRow(500), makeRow(501)];
  assert.equal(historyIdTo(pane), 499);
  pane.historyBusy = true;
  assert.equal(historyIdTo(pane), null, "one fetch at a time");
  pane.historyBusy = false;
  pane.historyDone = true;
  assert.equal(historyIdTo(pane), null, "a finished walk is not re-asked");
  pane.historyDone = false;
  pane.rows = [gapRow(makeRow(500), 499), makeRow(500)];
  assert.equal(historyIdTo(pane), null, "a divider already says the rest is not loaded");
  pane.rows = [makeRow(1)];
  assert.equal(historyIdTo(pane), null, "the first line of the capture has nothing below it");
  pane.rows = [makeRow(500)];
  pane.historyNext = 120;
  assert.equal(historyIdTo(pane), 120, "a walk continues below the last served page");
  pane.historyNext = 0;
  assert.equal(historyIdTo(pane), null, "the served page reached id 1");
});

test("planHistoryPage: a full page walks on, in capture order", () => {
  const lines = Array.from({ length: HISTORY_PAGE }, (_, i) => makeRow(1000 - i));
  const step = planHistoryPage({ lines, truncated: false, served: HISTORY_PAGE, loaded: 0,
                                 oldestServedId: 801 });
  assert.equal(step.done, false);
  assert.equal(step.nextIdTo, 800);
  assert.deepEqual([step.rows[0].id, step.rows.at(-1).id], [801, 1000], "oldest first");
});

test("planHistoryPage: a short unclamped page ends the walk", () => {
  const step = planHistoryPage({ lines: [makeRow(2), makeRow(1)], truncated: false, served: 2,
                                 loaded: 0, oldestServedId: 1 });
  assert.equal(step.done, true);
  assert.equal(step.rows.length, 2);
});

test("planHistoryPage: a page the filter emptied walks on below it rather than ending", () => {
  const step = planHistoryPage({ lines: [], truncated: false, served: HISTORY_PAGE, loaded: 0,
                                 oldestServedId: 301 });
  assert.equal(step.done, false, "the capture still has older lines; they just did not match");
  assert.equal(step.nextIdTo, 300, "asking below the kept rows again would refetch this page forever");
  assert.deepEqual(step.rows, []);
});

test("planHistoryPage: the budget ends the walk with a divider ahead of the page", () => {
  const lines = Array.from({ length: HISTORY_PAGE }, (_, i) => makeRow(5000 - i));
  const step = planHistoryPage({ lines, truncated: true, served: HISTORY_PAGE,
                                 loaded: HISTORY_MAX - HISTORY_PAGE, oldestServedId: 4801 });
  assert.equal(step.done, true);
  assert.equal(step.rows[0].chan, "gap", "a walk that stops short must say so");
  assert.equal(step.rows[0].id, 4800);
  assert.equal(step.rows[0].raw, "gap: 4800 lines not loaded");
  assert.equal(step.rows[1].id, 4801);
});

// ---- the fetch path ------------------------------------------------------------------

test("a top hit prepends one page and moves the scroll offset by what was added", async () => {
  dbMax = 1000; queries = [];
  buffer.length = 0;
  state.maxId = 1000;
  const pane = freshPane();
  pane.rows = Array.from({ length: 50 }, (_, i) => makeRow(951 + i));
  pane.scrollEl.scrollTop = 0;
  await loadHistory(pane);
  assert.equal(queries.length, 1);
  assert.match(queries[0], /order=desc/);
  assert.match(queries[0], new RegExp(`limit=${HISTORY_PAGE}`));
  assert.match(queries[0], /id_to=950\b/);
  assert.ok(!queries[0].includes("port="), "port 'all' is no server filter");
  assert.ok(!queries[0].includes("chan="), "every channel on is no server filter");
  assert.equal(pane.rows.length, 50 + HISTORY_PAGE);
  assert.equal(pane.rows[0].id, 951 - HISTORY_PAGE);
  assert.ok(ids(pane.rows).every((id, i) => id === 751 + i), "the page landed out of order or holed");
  assert.equal(pane.scrollEl.scrollTop, HISTORY_PAGE * 18, "the viewed rows must stay put");
  assert.equal(pane.historyDone, false);
  assert.equal(pane.historyLoaded, HISTORY_PAGE);
  assert.equal(pane.historyBusy, false);
  assert.ok(buffer.length === 0, "history rows belong to the pane, never the shared buffer");
  assert.equal(pane.autoscroll, false, "paging must not resume the pane");
});

test("the pane's port, channels and pattern go to the server, and the rows are re-filtered", async () => {
  dbMax = 1000; queries = [];
  chanOf = (id) => (id % 2 ? "debug" : "resp");
  const pane = freshPane({ port: "p2", channels: new Set(["resp"]) });
  applyRegex(pane, "line 9[0-9]0");
  pane.rows = [makeRow(1000, { port: "p2", chan: "resp" })];
  await loadHistory(pane);
  assert.match(queries[0], /port=p2/);
  assert.match(queries[0], /chan=resp/);
  assert.ok(!queries[0].includes("chan=debug"));
  assert.match(queries[0], /match=line\+9%5B0-9%5D0/);
  // The fake serves 200 resp rows below 1000 (ids 998, 996, ...); only the pattern's survive.
  const got = ids(pane.rows.slice(0, -1));
  assert.deepEqual(got, [900, 910, 920, 930, 940, 950, 960, 970, 980, 990]);
  assert.equal(pane.historyNext, 599, "the next page starts below the SERVED page, not the kept rows");
  chanOf = () => "debug";
});

test("a pattern the daemon refuses is retried without it, filtered here instead", async () => {
  dbMax = 400; queries = []; failMatch = true;
  const pane = freshPane();
  applyRegex(pane, "line 3[0-9]0");
  pane.rows = [makeRow(400)];
  await loadHistory(pane);
  failMatch = false;
  assert.equal(queries.length, 2, "one refusal, one retry");
  assert.match(queries[0], /match=/);
  assert.ok(!queries[1].includes("match="), "the retry must not carry the refused pattern");
  assert.deepEqual(ids(pane.rows.slice(0, -1)), [300, 310, 320, 330, 340, 350, 360, 370, 380, 390]);
  assert.ok(pane.regex, "the pane's own filter stays armed");
});

test("the walk ends at the first line of the capture", async () => {
  dbMax = 100; queries = [];
  const pane = freshPane();
  pane.rows = [makeRow(100)];
  await loadHistory(pane);
  assert.equal(pane.rows[0].id, 1);
  assert.equal(pane.historyDone, true, "a short page means nothing older exists");
  await loadHistory(pane);
  assert.equal(queries.length, 1, "a finished walk must not fetch again");
});

test("a failed fetch reports and releases the pane for the next attempt", async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("connection refused"); };
  const { hooks } = await import(webuiUrl("state.js"));
  const errs = [];
  hooks.reportError = (m) => errs.push(m);
  try {
    const pane = freshPane();
    pane.rows = [makeRow(100)];
    await loadHistory(pane);
    assert.equal(pane.historyBusy, false, "a throw must not wedge the pane in busy");
    assert.equal(pane.rows.length, 1);
    assert.match(errs.join(), /history failed: connection refused/);
  } finally {
    globalThis.fetch = realFetch;
    hooks.reportError = () => {};
  }
});

test("a rebuild drops the capture pages and restarts the walk from the buffer", async () => {
  dbMax = 1000; queries = [];
  buffer.length = 0;
  for (let i = 951; i <= 1000; i++) buffer.push(makeRow(i));
  const { rebuild, setAutoscroll } = await import(webuiUrl("terminal.js"));
  const pane = freshPane({ autoscroll: true });
  rebuild(pane);
  setAutoscroll(pane, false);   // as a scroll-up does: the pane freezes at maxId
  await loadHistory(pane);
  assert.equal(pane.rows[0].id, 751);
  rebuild(pane);
  assert.equal(pane.rows[0].id, 951, "the rows re-derive from the buffer");
  assert.equal(pane.historyLoaded, 0);
  assert.equal(pane.historyNext, null);
  await tick(0);
});

test("one top hit walks past pages the pane's own filter empties, up to HISTORY_HOPS", async () => {
  dbMax = 1000; queries = [];
  buffer.length = 0;
  state.maxId = 1000;
  // The fake capture ignores match=, so the pane re-filters every page itself: ids 751..950
  // are all "line 7xx".."line 9xx" and miss, ids 551..699 hit.
  const pane = freshPane();
  applyRegex(pane, "^line [1-6]");
  pane.rows = Array.from({ length: 50 }, (_, i) => makeRow(951 + i));
  pane.scrollEl.scrollTop = 0;
  await loadHistory(pane);
  assert.equal(queries.length, 2, "the emptied page must not stall the walk at the top");
  assert.match(queries[1], /id_to=750\b/);
  assert.equal(pane.rows[0].id, 551);
  assert.equal(pane.rows.length, 50 + 149);
  assert.equal(pane.scrollEl.scrollTop, 149 * 18);
  assert.equal(pane.historyBusy, false);

  // Nothing matches at all: the walk gives up after HISTORY_HOPS pages, not never (and not
  // because the capture ran out: 5000 rows hold more than HISTORY_HOPS pages).
  dbMax = 5000; queries = [];
  state.maxId = 5000;
  const dry = freshPane();
  applyRegex(dry, "^nothing matches this");
  dry.rows = Array.from({ length: 50 }, (_, i) => makeRow(4951 + i));
  await loadHistory(dry);
  assert.equal(queries.length, HISTORY_HOPS);
  assert.equal(dry.rows.length, 50);
  assert.equal(dry.historyBusy, false);
  assert.equal(dry.historyDone, false, "the capture still has older lines");
});

test("a cleared pane does not refill with what it cleared", async () => {
  dbMax = 1000; queries = [];
  buffer.length = 0;
  state.maxId = 1000;
  const pane = freshPane();
  pane.clearId = 900;   // "clear" at id 900: rows up to it are gone from this pane
  pane.rows = Array.from({ length: 50 }, (_, i) => makeRow(951 + i));
  await loadHistory(pane);
  assert.equal(queries.length, 1);
  assert.match(queries[0], /since_id=900\b/);
  assert.equal(pane.rows[0].id, 901, "ids 1..900 were cleared");
  assert.equal(pane.rows.length, 100);
  assert.equal(historyIdTo(pane), null, "nothing older to ask for above the clear point");
  const done = freshPane();
  done.clearId = 950;
  done.rows = [makeRow(951)];
  assert.equal(historyIdTo(done), null, "the oldest row sits right on the clear point");
});
