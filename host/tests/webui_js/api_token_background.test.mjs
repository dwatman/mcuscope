// R72-1: every request no user action makes (the stream's seeds and backfills in api.js, the
// 5 s status poll in statusbar.js) passes `background`, so a 401 on it shows the token badge
// and opens no window.prompt over whatever is being typed.
//
// Each case answers 401 to one request only and a working capture to the rest, so the flow
// reaches it; the prompt budget is re-armed first, so a foreground 401 there would prompt.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const channel = (port) => ({ name: "temp", port, sid: null, kind: "analog", last_ts: 1000, count: 1 });
let refuse = () => false;   // which request answers 401
let refused = [];
globalThis.fetch = async (url) => {
  const u = new URL(String(url), "http://x");
  const q = u.searchParams;
  const ok = (body) => ({ ok: true, status: 200, headers: { get: () => null }, json: async () => body });
  if (refuse(u)) {
    refused.push(u.pathname + u.search);
    return { ok: false, status: 401, headers: { get: () => null },
             json: async () => ({ error: "unauthorized" }) };
  }
  if (u.pathname === "/plot/channels") {
    return ok({ channels: [channel(q.get("port") || "p1")], ports: ["p1", "p2"] });
  }
  if (u.pathname === "/plot/series") return ok({ points: [] });
  if (u.pathname === "/lines") {
    return ok({ lines: q.get("match") ? [] : [{ id: 10, ts: 1000, port: "p1", chan: "debug", raw: "x" }],
                truncated: false });
  }
  if (u.pathname === "/status") return ok({ version: "0", ports: [], db_size_bytes: 0 });
  return ok({});
};
let prompts = 0;
globalThis.prompt = () => { prompts += 1; return null; };

const S = await import(webuiUrl("state.js"));
const { connectWs } = await import(webuiUrl("api.js"));
const { refreshStatus } = await import(webuiUrl("statusbar.js"));
// Whether the badge was shown during a case: a later request that is answered hides it again.
const badge = env.byId("tokenBadge");
let badgeShown = false, badgeHidden = true;
Object.defineProperty(badge, "hidden", {
  get: () => badgeHidden,
  set: (v) => { badgeHidden = v; if (!v) badgeShown = true; },
});

async function settle() { for (let i = 0; i < 12; i++) await tick(0); }

// Open a stream: a first connect (watermark 0) or a reconnect after `maxId`.
async function stream(maxId) {
  S.state.maxId = maxId;
  connectWs();
  env.sockets.at(-1).onopen();
  await settle();
}

const CASES = [
  ["the first connect's /lines", (u) => u.pathname === "/lines" && u.searchParams.get("order") === "desc"
     && !u.searchParams.has("since_id"), () => stream(0)],
  ["the plot definition seed", (u) => u.pathname === "/lines" && u.searchParams.has("match"), () => stream(0)],
  ["the unfiltered channel list", (u) => u.pathname === "/plot/channels" && !u.searchParams.has("port"),
   () => stream(0)],
  ["the per-port channel list", (u) => u.pathname === "/plot/channels" && u.searchParams.has("port"),
   () => stream(0)],
  ["the series seed", (u) => u.pathname === "/plot/series", () => stream(0)],
  ["the reconnect backfill", (u) => u.pathname === "/lines" && u.searchParams.has("since_id")
     && !u.searchParams.has("match"), () => stream(5)],
  ["the status poll", (u) => u.pathname === "/status", () => refreshStatus()],
];

for (const [name, pred, run] of CASES) {
  test(`a 401 on ${name} shows the badge and opens no prompt`, async () => {
    S.setToken(null);
    S.resetTokenPrompt();
    badge.hidden = true;
    badgeShown = false;
    prompts = 0; refused = []; refuse = pred;
    await run();
    refuse = () => false;
    assert.ok(refused.length > 0, `${name} was never requested, so nothing was tested`);
    assert.equal(prompts, 0, `${name} opened a prompt: ${refused.join(" | ")}`);
    assert.equal(badgeShown, true, "the badge is the way to the prompt");
  });
}

test("positive control: a foreground 401 on the same fake prompts", async () => {
  S.setToken(null);
  S.resetTokenPrompt();
  prompts = 0; refuse = () => true;
  await assert.rejects(S.api("GET", "/lines?order=desc&limit=200"));
  refuse = () => false;
  assert.equal(prompts, 1);
});
