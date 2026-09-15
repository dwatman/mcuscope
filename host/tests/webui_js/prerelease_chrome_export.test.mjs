// exportdlg.js and state.js downloads, pre-release round: overlapping session fills write once,
// an Export pressed across a `reset range` waits for the fill that owns the select, the portless
// plot key sends no port, and which paths go out as a token-less navigation.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const SESSIONS = [
  { id: 4, name: "run-a", lines: 120, started_ts: 1000, ended_ts: 2000, auto: false },
  { id: 7, name: "run-b", lines: 5, started_ts: 3000, ended_ts: null, auto: false },
];
const held = [];
let holdSessions = false;
let fetches = [];
let answer = null;   // (path) => response, for the download tests

globalThis.fetch = async (url) => {
  const u = String(url);
  fetches.push(u);
  if (answer) return answer(u);
  const res = { ok: true, status: 200, json: async () => ({ sessions: SESSIONS }) };
  if (holdSessions && u.startsWith("/sessions")) return new Promise((r) => held.push(() => r(res)));
  return res;
};

const { downloadPath, setToken } = await import(webuiUrl("state.js"));
const { initExportDialog, openExportDialog, plotExportPath } = await import(webuiUrl("exportdlg.js"));
initExportDialog();

async function settle() { for (let i = 0; i < 6; i++) await tick(0); }

const built = [];
function openDialog() {
  env.localStorage.setItem("mcuscope.exportRange",
    JSON.stringify({ mode: "session", session: "4", fromTs: null, toTs: null }));
  openExportDialog({ kind: "can", watermark: null, shown: null, options: [],
                     build: (p) => { built.push(p.get("session")); return null; } });
}
const sessionOptions = () => env.byId("expSession").children.map((o) => o.value);

// ---- E-5 --------------------------------------------------------------------------------

for (const order of ["older first", "newer first"]) {
  test(`E-5: reset range while the open's /sessions is out lists each session once (${order})`, async () => {
    holdSessions = true;
    openDialog();
    env.byId("expReset").emit("click", {});
    await settle();
    assert.equal(held.length, 2);
    const [older, newer] = held.splice(0);
    if (order === "older first") { older(); await settle(); newer(); }
    else { newer(); await settle(); older(); }
    await settle();
    holdSessions = false;
    assert.deepEqual(sessionOptions(), ["4", "7"]);
    assert.equal(env.byId("expSession").value, "7", "reset returns to the open session");
  });
}

test("E-5: Export pressed before a reset range waits for the fill that owns the select", async () => {
  holdSessions = true;
  openDialog();
  await settle();
  built.length = 0;
  env.byId("expGo").emit("click", {});      // awaits the open's fill
  await settle();
  env.byId("expReset").emit("click", {});   // supersedes it
  await settle();
  const [older, newer] = held.splice(0);
  older();
  await settle();
  assert.deepEqual(built, [], "exported from an empty select before the newest fill landed");
  newer();
  await settle();
  holdSessions = false;
  assert.deepEqual(built, ["7"], "the export covers the session the dialog shows");
});

// ---- F-17 -------------------------------------------------------------------------------

test("F-17: a plot key with no port ('-') sends no port; any real alias is sent", () => {
  const v = { format: "csv", decode: false, changes: false, deadband: "" };
  const q = (port) => new URLSearchParams(plotExportPath(new URLSearchParams(), v, ["a"], port).split("?")[1]);
  assert.equal(q("-").has("port"), false, "'-' is the portless key, not an alias the daemon knows");
  assert.equal(q("sim").get("port"), "sim");
  assert.equal(q("-x").get("port"), "-x", "only the exact key is portless");
});

// ---- E-9 and F-18: which downloads navigate ------------------------------------------------

function anchors() {
  const created = [];
  const orig = env.document.createElement;
  env.document.createElement = (t) => {
    const el = orig(t);
    if (String(t).toLowerCase() === "a") created.push(el);
    return el;
  };
  return { created, restore: () => { env.document.createElement = orig; } };
}

test("F-18, FW-9: /can/frames, the whole-range and the session .db exports stream as a navigation", async () => {
  setToken(null);
  for (const path of ["/can/frames?since_ts=1&format=csv", "/lines/export?format=jsonl",
                      "/plot/export?names=a&format=csv", "/sessions/2/export"]) {
    fetches = [];
    const a = anchors();
    assert.equal(await downloadPath(path, "x.csv", "export"), null);
    a.restore();
    // Fetched once for its headers (a session .db checks the list instead), never read whole.
    assert.equal(fetches.length, 1, `${path} was fetched more than once`);
    assert.equal(a.created.at(-1).href, path);
  }
});
