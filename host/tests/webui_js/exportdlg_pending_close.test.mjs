// exportdlg.js: an Export pending when its dialog closed (FW-1).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
installExportDaemon(env);

// `route` answers a request first; returning undefined falls through to the export double.
const daemon = globalThis.fetch;
let route = null;
globalThis.fetch = async (url, opt = {}) => {
  const r = route && route(String(url), opt);
  return (r && (await r)) || daemon(url, opt);
};
const ok = (body) => ({ ok: true, status: 200, headers: { get: () => null },
                        json: async () => body, blob: async () => new Blob(["x"]) });

const { setToken, STATUS_TIMEOUT_MS } = await import(webuiUrl("state.js"));
const { initExportDialog, openExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();

async function settle() { for (let i = 0; i < 10; i++) await tick(0); }

// Requests matching `pred` wait until released; `answer(url, opt)` is what they then get.
function holdOn(pred, answer) {
  const held = [];
  route = (u, opt) => (pred(u, opt) ? new Promise((r) => held.push(() => r(answer(u, opt)))) : undefined);
  return held;
}

const dlgOpen = () => env.byId("exportDlg").hasAttribute("open");

// ---- FW-1: an Export pending when its dialog closes ----------------------------------------

const SESSIONS = { sessions: [{ id: 7, name: "run-b", lines: 5, started_ts: 3000, ended_ts: null, auto: false }] };
const closes = {
  Cancel: () => env.byId("expCancel").emit("click", {}),
  "the x": () => env.byId("expClose").emit("click", {}),
  Escape: () => env.byId("exportDlg").emit("cancel", { preventDefault() {} }),
};

for (const [how, close] of Object.entries(closes)) {
  test(`FW-1: ${how} ends an Export waiting on the session list; the next dialog is its own`, async () => {
    const held = holdOn((u) => u.startsWith("/sessions"), () => ok(SESSIONS));
    const built = [];
    openExportDialog({ kind: "lines", watermark: null, shown: null, options: [],
                       build: () => { built.push("A"); return null; } });
    env.byId("expGo").emit("click", {});
    await settle();
    close();
    openExportDialog({ kind: "can", watermark: null, shown: null, options: [],
                       build: () => { built.push("B"); return null; } });
    await settle();
    assert.equal(env.byId("expGo").disabled, false, "the closed dialog's Export holds the next one's button");
    env.byId("expGo").emit("click", {});   // B's own Export, waiting on B's list
    await settle();
    held[0]();                             // A's list answers
    await settle();
    assert.deepEqual(built, [], "the closed dialog's Export ran");
    assert.equal(dlgOpen(), true, "the closed dialog's Export closed the next one");
    assert.equal(env.byId("expGo").disabled, true, "the closed Export released B's pending button");
    held[1]();
    await settle();
    route = null;
    assert.deepEqual(built, ["B"], "B's own Export still goes");
    assert.equal(dlgOpen(), false);
  });
}

test("FW-1: an Export pending when Cancel closed the dialog does nothing once the list answers", async () => {
  const held = holdOn((u) => u.startsWith("/sessions"), () => ok(SESSIONS));
  const built = [];
  openExportDialog({ kind: "lines", watermark: null, shown: null, options: [],
                     build: () => { built.push("A"); return null; } });
  env.byId("expGo").emit("click", {});
  await settle();
  env.byId("expCancel").emit("click", {});
  held[0]();
  await settle();
  route = null;
  assert.deepEqual(built, [], "Cancel did not end the pending Export");
});

test("FW-1: a download answering after Cancel neither reports into nor releases the next dialog", async () => {
  setToken("tok");   // a token fetches the export, so its answer comes back to the dialog
  const refused = { ok: false, status: 400, headers: { get: () => null }, json: async () => ({ error: "refused" }) };
  const held = holdOn((u) => u.includes("/export"), (u) => (u.startsWith("/lines") ? refused : ok({})));
  const inner = route;
  route = (u, opt) => (u.startsWith("/sessions") ? ok(SESSIONS) : inner(u, opt));
  openExportDialog({ kind: "lines", watermark: null, shown: null, options: [],
                     build: () => "/lines/export?format=text" });
  env.byId("expGo").emit("click", {});
  await settle();
  assert.equal(held.length, 1);
  env.byId("expCancel").emit("click", {});
  openExportDialog({ kind: "plot", watermark: null, shown: null, options: [],
                     build: () => "/plot/export?names=a&format=long" });
  env.byId("expGo").emit("click", {});
  await settle();
  assert.equal(held.length, 2, "B's download is out");
  held[0]();   // A's refusal
  await settle();
  assert.equal(env.byId("expErr").textContent, "", "A's refusal landed in B");
  assert.equal(dlgOpen(), true);
  assert.equal(env.byId("expGo").disabled, true, "A's end released B's button while B's download is out");
  held[1]();
  await settle();
  route = null;
  setToken(null);
  assert.equal(dlgOpen(), false, "B's own download closes B");
});

test("FW-1: a session list that never answers leaves Export usable over the whole capture", async () => {
  const realTimeout = AbortSignal.timeout;
  const armed = [];
  AbortSignal.timeout = (ms) => { const ac = new AbortController(); armed.push({ ms, ac }); return ac.signal; };
  route = (u, opt) => (u.startsWith("/sessions")
    ? new Promise((_, rej) => opt.signal?.addEventListener("abort", () => rej(opt.signal.reason)))
    : undefined);
  env.localStorage.setItem("mcuscope.exportRange",
    JSON.stringify({ mode: "session", session: "4", fromTs: null, toTs: null }));
  const built = [];
  openExportDialog({ kind: "can", watermark: null, shown: null, options: [],
                     build: (p) => { built.push(p.get("session")); return null; } });
  env.byId("expGo").emit("click", {});
  await settle();
  assert.deepEqual(built, [], "exported before the list or its deadline");
  assert.deepEqual(armed.map((a) => a.ms), [STATUS_TIMEOUT_MS], "the list fetch has no deadline");
  assert.ok(STATUS_TIMEOUT_MS < 5000);
  armed[0].ac.abort(new DOMException("timed out", "TimeoutError"));
  await settle();
  AbortSignal.timeout = realTimeout;
  route = null;
  assert.deepEqual(built, [null], "the whole capture, as the select then offers");
  assert.deepEqual(env.byId("expSession").children.map((o) => o.textContent), ["whole capture"]);
  assert.equal(env.byId("expErr").textContent, "could not list sessions: no reply from daemon");
  assert.equal(env.byId("expGo").disabled, false);
});
