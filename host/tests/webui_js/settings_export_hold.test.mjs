// settings.js: a session row's `.db` export sent as a navigation holds its button, with a
// "preparing download..." note, for EXPORT_HOLD_MS after the anchor click. The navigation
// returns before the daemon has built the copy, so without the hold a second click during the
// build downloads (and builds) twice. The token path, a fetch, is held only until it is saved.
// A held button is aria-disabled, not disabled, so it keeps keyboard focus.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const HOLD = 5000;
const PATH = "/sessions/2/export";

// A monotonic clock the test advances: performance.now and every timer of 100 ms or more, or a
// negative one (an expired hold must not schedule a release at all). Short ones (tick) stay
// real so awaited promises still settle. Date.now is a separate wall clock a test can step.
let now = 1_000_000;
performance.now = () => now;
let wall = 1_700_000_000_000;
Date.now = () => wall;
const realSet = globalThis.setTimeout, realClear = globalThis.clearTimeout;
let timers = [];
globalThis.setTimeout = (fn, ms = 0, ...a) => {
  if (ms >= 0 && ms < 100) return realSet(fn, ms, ...a);
  const t = { fn, at: now + ms, cleared: false };
  timers.push(t);
  return t;
};
globalThis.clearTimeout = (t) => { if (t && typeof t === "object" && "at" in t) t.cleared = true; else realClear(t); };
async function advance(ms) {
  now += ms;
  const due = timers.filter((t) => !t.cleared && t.at <= now);
  timers = timers.filter((t) => !due.includes(t));
  for (const t of due) t.fn();
  await settle();
}
const pending = () => timers.filter((t) => !t.cleared).length;

let sessionsByName;
const names = [], dbFetches = [], navigations = [], saved = [], reported = [];
const ok = (body) => ({ ok: true, status: 200, headers: { get: () => null }, json: async () => body });

globalThis.fetch = async (url) => {
  const u = String(url);
  if (u.startsWith("/sessions?name=")) { names.push(u); return ok({ sessions: sessionsByName, active: null }); }
  if (u === PATH) {
    dbFetches.push(u);
    return { ok: true, status: 200, headers: { get: () => 'attachment; filename="r.db"' },
             blob: async () => new Blob(["db"]) };
  }
  if (u === "/sessions/2/bundle") {
    return { ok: true, status: 200, headers: { get: () => null }, blob: async () => new Blob(["zip"]) };
  }
  if (u.startsWith("/sessions")) {
    return ok({ sessions: [{ id: 2, name: "r", started_ts: 1, ended_ts: 2, lines: 3, auto: false }] });
  }
  if (u.startsWith("/devices")) return ok({ devices: [] });
  if (u.startsWith("/status")) return ok({ db_size_bytes: 0, db_content_bytes: 0 });
  if (u === "/plotjuggler") return ok({ enabled: false, dest: "127.0.0.1:9870" });
  return ok({ path: "/c.toml", exists: true, restart_required: false, token_set: false,
              server: { host: "127.0.0.1", port: 8558 },
              storage: { db_path: "", retention_days: 7, max_db_bytes: 0, min_sessions: 1, auto_session: false },
              ports: [], update: { check: false } });
};

// An anchor click is a navigation when its href is the export path, a saved Blob otherwise.
const create = env.document.createElement;
env.document.createElement = (tag) => {
  const el = create(tag);
  if (String(tag).toLowerCase() === "a") {
    el.click = () => (String(el.href).startsWith("blob:") ? saved : navigations).push(el.href);
  }
  return el;
};

const { hooks, setToken } = await import(webuiUrl("state.js"));
const { initSettings } = await import(webuiUrl("settings.js"));
initSettings();
hooks.reportError = (m) => reported.push(m);

async function settle() { for (let i = 0; i < 8; i++) await tick(0); }
async function open() {
  env.byId("settingsDlg").removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  await settle();
}
const cell = () => env.byId("cfgSessionsBody").children[0].children[3];
const btn = () => cell().children.find((b) => b.textContent === "export");
const note = () => cell().children.find((c) => c.textContent === "preparing download...");
const held = (b = btn()) => b.getAttribute("aria-disabled") === "true";

// A navigation's preflight (the sessions check) answered only when the test says so.
function holdPreflight() {
  const answers = [];
  let asked = 0;
  const plain = globalThis.fetch;
  globalThis.fetch = (url, opt) => {
    if (!String(url).startsWith("/sessions?name=")) return plain(url, opt);
    asked += 1;
    return new Promise((r) => { answers.push(() => r(plain(url, opt))); });
  };
  return { asked: () => asked, answer: () => answers.shift()(), restore: () => { globalThis.fetch = plain; } };
}

// Each test starts past any earlier hold, with no pending timer and no token.
async function reset({ exists = true } = {}) {
  now += 60_000;
  timers = [];
  sessionsByName = exists ? [{ id: 2, name: "r" }] : [];
  for (const a of [names, dbFetches, navigations, saved, reported]) a.length = 0;
  setToken(null);
  await open();
}
const click = async () => { btn().emit("click", {}); await settle(); };

test("the note is in the row, after the buttons, and hidden until an export", async () => {
  await reset();
  assert.deepEqual(cell().children.map((c) => c.textContent),
    ["export", "bundle", "delete", "preparing download..."]);
  assert.equal(note().hidden, true);
  assert.equal(held(), false);
});

test("a second click during the hold neither preflights nor navigates again", async () => {
  await reset();
  await click();
  assert.deepEqual(navigations, [PATH], "positive control: the first click navigates");
  assert.equal(names.length, 1);
  assert.equal(held(), true, "released as soon as the anchor was clicked");
  assert.equal(note().hidden, false, "no note while the daemon builds the copy");
  await advance(300);
  await click();
  await advance(HOLD - 301);
  await click();
  assert.deepEqual(navigations, [PATH], "a click inside the hold downloaded again");
  assert.equal(names.length, 1, "a click inside the hold preflighted again");
  assert.equal(held(), true);
});

test("the hold ends after EXPORT_HOLD_MS: note cleared, no timer left, and a click downloads again", async () => {
  await reset();
  await click();
  await advance(HOLD - 1);
  assert.equal(held(), true, "released before the hold ended");
  await advance(1);
  assert.equal(held(), false, "still held after the hold ended");
  assert.equal(note().hidden, true, "the note outlived the hold");
  assert.equal(pending(), 0, "the ended hold left a timer behind");
  await click();
  assert.deepEqual(navigations, [PATH, PATH]);
  assert.equal(names.length, 2);
});

test("a table re-rendered during the hold keeps the new row's button held, then frees it", async () => {
  await reset();
  await click();
  await advance(1000);
  await open();   // a reopen or a delete re-renders every row
  assert.equal(held(), true, "the re-rendered row handed back a live button");
  assert.equal(note().hidden, false, "the re-rendered row lost the note");
  await click();
  assert.deepEqual(navigations, [PATH]);
  await advance(HOLD - 1000);
  assert.equal(held(), false);
  assert.equal(note().hidden, true);
  await click();
  assert.deepEqual(navigations, [PATH, PATH]);
});

test("an ended hold lets go of its rows: a later hold does not reach a row rendered during the first", async () => {
  await reset();
  await click();
  await open();
  const during = btn();
  assert.equal(held(during), true, "setup: the re-rendered row is held");
  await advance(HOLD);
  assert.equal(held(during), false);
  await open();
  await click();
  assert.equal(held(), true, "positive control: the second hold holds the current row");
  assert.equal(held(during), false, "the second hold reached a row of the first, still kept");
});

test("a wall clock stepped back an hour during the hold does not stretch a re-rendered row's hold", async () => {
  await reset();
  await click();
  await advance(1000);
  wall -= 3_600_000;   // NTP steps the clock back
  await open();
  assert.equal(held(), true, "positive control: the re-rendered row is held");
  await advance(HOLD - 1000);
  assert.equal(held(), false, "the re-rendered row's hold followed the wall clock");
  await click();
  assert.deepEqual(navigations, [PATH, PATH]);
});

test("a table re-rendered during the preflight holds the new row; a second click there downloads nothing", async () => {
  await reset();
  const pf = holdPreflight();
  try {
    btn().emit("click", {});
    await settle();
    await advance(1500);   // the preflight takes 1.5 s
    await open();
    assert.equal(held(), true, "a row rendered during the preflight handed back a live button");
    await click();
    assert.equal(pf.asked(), 1, "a click on the re-rendered row during the preflight preflighted again");
    pf.answer();
    await settle();
  } finally { pf.restore(); }
  assert.deepEqual(navigations, [PATH], "positive control: the first click navigates");
  await click();
  assert.deepEqual(navigations, [PATH], "a click on the re-rendered row after the navigation downloaded again");
  await advance(HOLD - 1);
  assert.equal(held(), true, "the hold ended before EXPORT_HOLD_MS after the navigation");
  await advance(1);
  assert.equal(held(), false, "the provisional hold outlived the exact one");
  assert.equal(note().hidden, true);
});

test("a table re-rendered during a preflight that is refused frees the new row at once", async () => {
  await reset({ exists: false });
  const pf = holdPreflight();
  try {
    btn().emit("click", {});
    await settle();
    await open();
    assert.equal(held(), true, "positive control: the re-rendered row is held during the preflight");
    pf.answer();
    await settle();
  } finally { pf.restore(); }
  assert.deepEqual(reported, ["session export failed: no such session: 2"]);
  assert.equal(held(), false, "a refused preflight left the re-rendered row held");
  assert.equal(note().hidden, true, "a refused preflight left the re-rendered row's note up");
  assert.equal(pending(), 0);
});

// A browser blurs a focused control the moment it is disabled; the stub does not, so the
// button gets that behaviour here.
function browserDisabled(el) {
  let v = el.disabled;
  Object.defineProperty(el, "disabled", {
    get: () => v,
    set: (x) => { v = x; if (x && env.document.activeElement === el) el.blur(); },
  });
  return el;
}

test("the focused export button keeps keyboard focus through the preflight and the hold", async () => {
  await reset();
  const probe = browserDisabled(env.document.createElement("button"));
  probe.focus();
  probe.disabled = true;
  assert.notEqual(env.document.activeElement, probe, "positive control: disabling blurs");
  const b = browserDisabled(btn());
  b.focus();
  const pf = holdPreflight();
  try {
    b.emit("click", {});
    await settle();
    assert.equal(env.document.activeElement, b, "focus lost during the preflight");
    pf.answer();
    await settle();
  } finally { pf.restore(); }
  assert.deepEqual(navigations, [PATH]);
  assert.equal(held(b), true);
  assert.equal(env.document.activeElement, b, "focus lost when the hold began");
  await advance(HOLD);
  assert.equal(held(b), false);
  assert.equal(env.document.activeElement, b);
});

test("with a token the export is fetched and saved, released at once, and never held", async () => {
  await reset();
  setToken("t0k");
  await click();
  assert.deepEqual(dbFetches, [PATH]);
  assert.equal(saved.length, 1);
  assert.deepEqual(navigations, []);
  assert.equal(held(), false, "the blob path took the navigation hold");
  assert.equal(note().hidden, true, "the blob path showed the preparing note");
  assert.equal(pending(), 0, "a hold timer was scheduled");
  await click();
  assert.deepEqual(dbFetches, [PATH, PATH], "a second export right after a saved one is refused");
});

test("with a token a second click while the fetch is in flight still fetches once", async () => {
  await reset();
  setToken("t0k");
  btn().emit("click", {});
  btn().emit("click", {});
  await settle();
  assert.deepEqual(dbFetches, [PATH]);
});

test("a token set while the preflight is pending still holds the navigation that follows", async () => {
  await reset();
  const pf = holdPreflight();
  try {
    btn().emit("click", {});
    await settle();
    setToken("t0k");   // e.g. entered in another dialog before the daemon answered
    pf.answer();
    await settle();
  } finally { pf.restore(); }
  assert.deepEqual(navigations, [PATH], "positive control: the navigation went out");
  assert.equal(held(), true, "the path was judged after the call, when it had a token");
  assert.equal(note().hidden, false);
});

test("a refused navigation export is reported and not held", async () => {
  await reset({ exists: false });
  await click();
  assert.deepEqual(reported, ["session export failed: no such session: 2"]);
  assert.deepEqual(navigations, []);
  assert.equal(held(), false, "a refusal took the hold");
  assert.equal(note().hidden, true, "a refusal showed the preparing note");
  sessionsByName = [{ id: 2, name: "r" }];
  await click();
  assert.deepEqual(navigations, [PATH], "the retry after a refusal did not download");
});

test("the bundle, always fetched, is never held", async () => {
  await reset();
  cell().children.find((b) => b.textContent === "bundle").emit("click", {});
  await settle();
  const bundle = cell().children.find((b) => b.textContent === "bundle");
  assert.equal(saved.length, 1);
  assert.equal(held(bundle), false);
  assert.equal(note().hidden, true);
  assert.equal(held(), false);
});
