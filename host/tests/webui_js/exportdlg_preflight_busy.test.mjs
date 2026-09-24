// exportdlg.js, owner ruling E-3: a second Export while a token-less preflight is out sends
// nothing more.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const SESSIONS = [{ id: 7, name: "run-b", lines: 5, started_ts: 3000, ended_ts: null, auto: false }];
let route = null;         // (url, opt) => response or promise; the test's daemon
let fetches = [];         // [url, opt]

globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  fetches.push([u, opt]);
  if (u.startsWith("/sessions?limit=")) return res(200, { sessions: SESSIONS });
  return route(u, opt);
};

function res(status, body, { json = true } = {}) {
  return {
    ok: status >= 200 && status < 300, status, headers: { get: () => null },
    json: async () => { if (!json) throw new SyntaxError("Unexpected token"); return body; },
    text: async () => String(body),
    blob: async () => new Blob(["body"]),
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

const { setToken } = await import(webuiUrl("state.js"));
const { initExportDialog, openExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();

async function settle() { for (let i = 0; i < 8; i++) await tick(0); }
const PATH = "/plot/export?names=ftest&format=long&decode=1&changes=1&deadband=ftest%3A0.5";

async function openAndExport(path = PATH) {
  setToken(null);
  fetches = []; navigations.length = 0;
  env.byId("exportDlg").removeAttribute("open");
  openExportDialog({ kind: "plot", watermark: null, shown: null, options: [], build: () => path });
  await settle();
  env.byId("expGo").emit("click", {});
  await settle();
}
const exportFetches = () => fetches.filter(([u]) => !u.startsWith("/sessions?limit="));

test("E-3: a second Export while the preflight is out sends nothing more", async () => {
  const h = held();
  route = h.route;
  await openAndExport();
  env.byId("expGo").emit("click", {});
  await settle();
  assert.equal(exportFetches().length, 1);
  h.release(res(200, ""));
  await settle();
  assert.deepEqual(navigations, [PATH], "one download");
});
