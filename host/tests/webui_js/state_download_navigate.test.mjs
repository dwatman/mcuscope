// state.js downloadPath: which paths go out as a token-less navigation.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();

const SESSIONS = [
  { id: 4, name: "run-a", lines: 120, started_ts: 1000, ended_ts: 2000, auto: false },
  { id: 7, name: "run-b", lines: 5, started_ts: 3000, ended_ts: null, auto: false },
];
let fetches = [];

globalThis.fetch = async (url) => {
  const u = String(url);
  fetches.push(u);
  return { ok: true, status: 200, json: async () => ({ sessions: SESSIONS }) };
};

const { downloadPath, setToken } = await import(webuiUrl("state.js"));

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
    assert.equal(a.created.at(-1).href, path.startsWith("/sessions/") ? path + "?wait=1" : path);
  }
});
