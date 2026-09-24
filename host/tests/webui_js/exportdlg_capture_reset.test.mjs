// exportdlg.js (registry class 73): an export dialog left open across a capture reset holds bounds
// (the panel's id_to watermark and shown window, the session list) that name the old capture;
// Export refuses them rather than send them against the new one.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const ok = (body) => ({ ok: true, status: 200, headers: { get: () => null }, json: async () => body });
const exports = [];       // every request or navigation to an export path
let holdSessions = null;  // resolvers for held /sessions fills
globalThis.fetch = async (url) => {
  const u = String(url);
  if (u.startsWith("/lines/export")) { exports.push(u); return ok({}); }
  if (u.startsWith("/sessions")) {
    const answer = ok({ sessions: [{ id: 3, name: "old-run", lines: 9, started_ts: 1, ended_ts: null }] });
    return holdSessions ? new Promise((r) => holdSessions.push(() => r(answer))) : answer;
  }
  return ok({ lines: [], channels: [] });
};
const create = env.document.createElement;
env.document.createElement = (tag) => {
  const el = create(tag);
  if (String(tag).toLowerCase() === "a") el.click = () => exports.push("navigate " + el.href);
  return el;
};

const { connectWs } = await import(webuiUrl("api.js"));
const { initExportDialog, openExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();

async function settle() { for (let i = 0; i < 8; i++) await tick(0); }
connectWs();
const sock = env.sockets.at(-1);
const token = async (capture) => { sock.onmessage({ data: JSON.stringify([{ capture }]) }); await settle(); };
await token("cap-A");   // adopted, not a reset

const REFUSAL = "the capture was reset while this dialog was open; close it and export again";
function openPausedPanelExport() {
  env.byId("exportDlg").removeAttribute("open");
  env.byId("expErr").textContent = "";
  openExportDialog({
    kind: "lines", watermark: 900, shown: { fromTs: 1000, toTs: 1001 },
    options: [{ name: "format", type: "select", label: "Format", choices: ["text"], value: "text" }],
    build: (p) => "/lines/export?" + p.toString(),
  });
}
const pressExport = async () => { env.byId("expGo").emit("click", {}); await settle(); };

test("a capture reset while Export waits on the session list sends nothing", async () => {
  exports.length = 0;
  holdSessions = [];
  openPausedPanelExport();
  await pressExport();
  await token("cap-B");
  holdSessions.forEach((go) => go());
  holdSessions = null;
  await settle();
  assert.deepEqual(exports, [], "the old capture's id_to went out against the new one");
  assert.equal(env.byId("expErr").textContent, REFUSAL);
});

test("a capture reset between opening the dialog and Export sends nothing", async () => {
  exports.length = 0;
  openPausedPanelExport();
  await settle();
  await token("cap-C");
  await pressExport();
  assert.deepEqual(exports, []);
  assert.equal(env.byId("expErr").textContent, REFUSAL);
});

test("a dialog opened after the reset exports with its own bounds", async () => {
  exports.length = 0;
  openPausedPanelExport();
  await settle();
  await pressExport();
  assert.equal(env.byId("expErr").textContent, "");
  assert.ok(exports.some((e) => e.startsWith("navigate /lines/export?") && e.includes("id_to=900")),
            `no export went out: ${JSON.stringify(exports)}`);
});
