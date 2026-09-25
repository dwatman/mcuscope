// state.js, owner rulings E-3 and E-9: a token-less export is preflighted before the navigation,
// so a daemon refusal is shown and the dialog stays open; a session .db export asks the daemon's
// `check=1` instead, which answers without building the copy, and neither is ever read into the tab.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const SESSIONS = [{ id: 7, name: "run-b", lines: 5, started_ts: 3000, ended_ts: null, auto: false }];
let route = null;         // (url, opt) => response or promise; the test's daemon
let fetches = [];         // [url, opt]
let reads = 0;            // response bodies read (json, text or blob)

globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  fetches.push([u, opt]);
  if (u.startsWith("/sessions?limit=")) return res(200, { sessions: SESSIONS });
  return route(u, opt);
};

function res(status, body, { json = true } = {}) {
  return {
    ok: status >= 200 && status < 300, status, headers: { get: () => null },
    json: async () => { reads++; if (!json) throw new SyntaxError("Unexpected token"); return body; },
    text: async () => { reads++; return String(body); },
    blob: async () => { reads++; return new Blob(["body"]); },
  };
}

// A request that answers only when released, or rejects when its signal aborts.
function held() {
  const h = { release: null, opt: null };
  h.route = (u, opt) => new Promise((resolve, reject) => {
    h.opt = opt;
    h.release = resolve;
    if (opt.signal) opt.signal.addEventListener("abort", () => reject(opt.signal.reason));
  });
  return h;
}

const navigations = [];
const create = env.document.createElement;
env.document.createElement = (tag) => {
  const el = create(tag);
  if (String(tag).toLowerCase() === "a") {
    el.click = () => { if (!el.href.startsWith("blob:")) navigations.push(el.href); };
  }
  return el;
};

const { downloadPath, setToken, STATUS_TIMEOUT_MS } = await import(webuiUrl("state.js"));
const { initExportDialog, openExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();

async function settle() { for (let i = 0; i < 8; i++) await tick(0); }
const dlgOpen = () => env.byId("exportDlg").hasAttribute("open");
const PATH = "/plot/export?names=ftest&format=long&decode=1&changes=1&deadband=ftest%3A0.5";

async function openAndExport(path = PATH) {
  setToken(null);
  fetches = []; reads = 0; navigations.length = 0;
  env.byId("exportDlg").removeAttribute("open");
  openExportDialog({ kind: "plot", watermark: null, shown: null, options: [], build: () => path });
  await settle();
  env.byId("expGo").emit("click", {});
  await settle();
}
const exportFetches = () => fetches.filter(([u]) => !u.startsWith("/sessions?limit="));

test("E-3: a preflight refused with a JSON error shows it inline and keeps the dialog open", async () => {
  route = () => res(400, { error: "deadband needs name=value: ftest:0.5" });
  await openAndExport();
  assert.equal(env.byId("expErr").textContent, "plot export failed: deadband needs name=value: ftest:0.5");
  assert.equal(dlgOpen(), true);
  assert.deepEqual(navigations, [], "a refused export must not navigate");
  assert.equal(env.byId("expGo").disabled, false);
});

test("E-3: a refusal whose body is not JSON names the status", async () => {
  route = () => res(500, "Internal Server Error", { json: false });
  await openAndExport();
  assert.equal(env.byId("expErr").textContent, "plot export failed: HTTP 500");
  assert.equal(dlgOpen(), true);
  assert.deepEqual(navigations, []);
});

test("E-3: an ok preflight aborts the body unread, then navigates and closes the dialog", async () => {
  route = () => res(200, "a,b\n");
  await openAndExport();
  const mine = exportFetches();
  assert.equal(mine.length, 1);
  assert.equal(mine[0][0], PATH);
  assert.equal(mine[0][1].signal.aborted, true, "the preflight body must be aborted at the headers");
  assert.equal(reads, 1, "only the session list is read; the export body never is");
  assert.deepEqual(navigations, [PATH]);
  assert.equal(dlgOpen(), false);
});

test("E-3: a preflight with no headers in STATUS_TIMEOUT_MS says no reply and does not navigate", async () => {
  const h = held();
  route = h.route;
  const realSetTimeout = globalThis.setTimeout;
  const armed = [];
  globalThis.setTimeout = (fn, ms, ...a) => (ms === STATUS_TIMEOUT_MS ? (armed.push(fn), 0) : realSetTimeout(fn, ms, ...a));
  try {
    await openAndExport();
    assert.equal(armed.length, 1, "the preflight must arm the 4 s deadline");
    assert.equal(env.byId("expGo").disabled, true, "still waiting on the headers");
    armed[0]();
    await settle();
  } finally {
    globalThis.setTimeout = realSetTimeout;
  }
  assert.equal(h.opt.signal.aborted, true);
  assert.equal(env.byId("expErr").textContent, "plot export failed: no reply from daemon");
  assert.equal(dlgOpen(), true);
  assert.deepEqual(navigations, []);
});

test("E-3: Cancel while the preflight is out means no navigation when it answers ok", async () => {
  const h = held();
  route = h.route;
  await openAndExport();
  env.byId("expCancel").emit("click", {});
  h.release(res(200, ""));
  await settle();
  assert.deepEqual(navigations, [], "a cancelled export must not start its download");
  assert.equal(env.byId("expErr").textContent, "");
});

test("Cancel while a token export's body is out saves nothing", async () => {
  const h = held();
  route = h.route;
  reads = 0;
  setToken("t");
  env.byId("exportDlg").removeAttribute("open");
  openExportDialog({ kind: "plot", watermark: null, shown: null, options: [], build: () => PATH });
  await settle();
  env.byId("expGo").emit("click", {});
  await settle();
  const blobs = env.blobs.length;
  env.byId("expCancel").emit("click", {});
  h.release(res(200, "x"));
  await settle();
  setToken(null);
  assert.equal(reads, 2, "the session list and, on the token path, the export body");
  assert.equal(env.blobs.length, blobs, "a cancelled export must not be saved");
});

test("E-9: a session .db export of a deleted session is reported, not navigated", async () => {
  setToken(null);
  fetches = []; navigations.length = 0;
  route = (u) => (u === "/sessions/2/export?check=1&wait=1" ? res(400, { error: "no such session: 2" })
    : res(500, "no"));
  const msg = await downloadPath("/sessions/2/export", "run.db", "session export");
  assert.equal(msg, "session export failed: no such session: 2");
  assert.deepEqual(fetches.map(([u]) => u), ["/sessions/2/export?check=1&wait=1"],
    "only the check is requested, never the copy");
  assert.deepEqual(navigations, []);
});

test("E-9, FW-9: a session .db export that still exists navigates without requesting the copy", async () => {
  setToken(null);
  fetches = []; reads = 0; navigations.length = 0;
  route = (u) => (u === "/sessions/2/export?check=1&wait=1" ? res(200, { ok: true }) : res(500, "no"));
  assert.equal(await downloadPath("/sessions/2/export", "run.db", "session export"), null);
  assert.deepEqual(fetches.map(([u]) => u), ["/sessions/2/export?check=1&wait=1"]);
  assert.deepEqual(navigations, ["/sessions/2/export?wait=1"]);
});
