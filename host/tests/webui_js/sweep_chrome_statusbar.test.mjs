// Class 73 sweep, statusbar.js: an action's answer is not written into a dialog reopened while
// it was out, and a success does not clear a failure shown after the action started.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const ok = (body) => ({ ok: true, status: 200, json: async () => body });
const fail = (status, error) => ({ ok: false, status, json: async () => ({ error }) });

let status;
const calls = [];
const held = [];
let holdRules = [];
function reset() {
  status = { version: "0.5.0", uptime_s: 1, db_size_bytes: 0, write_errors: 0, session: null,
             ports: [{ alias: "p1", device: "/dev/ttyACM0", connected: true, held: false },
                     { alias: "p2", device: "/dev/ttyACM1", connected: false, held: false }] };
  calls.length = 0; held.length = 0; holdRules = [];
}
function holdNext(pred) { holdRules.push({ pred, n: 1 }); }
async function release(pred, answer) {
  const i = held.findIndex((h) => pred(h.u, h.method));
  assert.ok(i >= 0, "setup: no such request is held");
  held.splice(i, 1)[0].go(answer);
  await settle();
}

globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  const method = opt.method || "GET";
  calls.push([method, u]);
  const rule = holdRules.find((r) => r.n > 0 && r.pred(u, method));
  if (rule) {
    rule.n--;
    const given = await new Promise((go) => held.push({ u, method, go }));
    if (given) return given;
  }
  if (u === "/config") return ok({ ports: [], restart_required: false, revision: "r1" });
  if (u.startsWith("/devices")) return ok({ devices: [] });
  if (method !== "GET") return ok({ ok: true });
  return ok(status);
};

const { initStatusbar, refreshStatus, flashDaemonError } = await import(webuiUrl("statusbar.js"));
initStatusbar();

async function settle() { for (let i = 0; i < 12; i++) await tick(0); }
const click = async (el) => { el.emit("click", {}); await settle(); };
const isOpen = (id) => env.byId(id).hasAttribute("open");

// ---- attach dialog ----------------------------------------------------------------------

const isAttach = (u, m) => m === "POST" && u === "/ports";

async function submitAttachThenReopen(saveToConfig) {
  await click(env.byId("attachBtn"));
  env.byId("devSel").value = "socket://127.0.0.1:9900";
  env.byId("baudSel").value = "115200";
  env.byId("aliasInput").value = "sim";
  env.byId("saveToConfig").checked = saveToConfig;
  holdNext(isAttach);
  await click(env.byId("dlgAttach"));
  await click(env.byId("dlgCancel"));
  await click(env.byId("attachBtn"));
  assert.equal(env.byId("saveToConfig").checked, false, "setup: the reopen did not reset the box");
}

test("save to config, ticked at submit, is honoured when the dialog was reopened meanwhile", async () => {
  reset();
  await submitAttachThenReopen(true);
  await release(isAttach);
  assert.ok(calls.some(([m, u]) => m === "PUT" && u === "/config/ports"), "the attach was not saved to config");
});

test("an attach landing after a reopen leaves the reopened dialog open", async () => {
  reset();
  await submitAttachThenReopen(false);
  await release(isAttach);
  assert.equal(isOpen("attachDlg"), true);
});

test("an attach refused after a reopen writes its refusal nowhere", async () => {
  reset();
  await submitAttachThenReopen(false);
  await release(isAttach, fail(422, "alias sim is already attached"));
  assert.equal(env.byId("dlgErr").textContent, "");
});

// ---- session dialog ---------------------------------------------------------------------

test("a session start refused after a reopen writes its refusal nowhere", async () => {
  reset();
  await refreshStatus();
  const isStart = (u, m) => m === "POST" && u === "/sessions";
  await click(env.byId("sessionBtn"));
  holdNext(isStart);
  await click(env.byId("sesStart"));
  await click(env.byId("sesCancel"));
  await click(env.byId("sessionBtn"));
  await release(isStart, fail(422, "name: stale refusal"));
  assert.equal(env.byId("sesErr").textContent, "");
});

// ---- the failure strip ------------------------------------------------------------------

function chipButton(alias, title) {
  const chip = env.byId("ports").children.find((c) => c.children.some((k) => k.textContent === alias));
  return chip.children.find((k) => k.title.startsWith(title));
}

const OTHER = "other failed: shown while the action was out";
async function failureSurvives(start, pred) {
  holdNext(pred);
  await start();
  flashDaemonError(OTHER);
  await release(pred);
  assert.equal(env.byId("actionErrText").textContent, OTHER);
  assert.equal(env.byId("actionErr").hidden, false);
}

test("a detach succeeding late does not clear a failure shown after it started", async () => {
  reset();
  await refreshStatus();
  await failureSurvives(() => click(chipButton("p1", "Detach p1")), (u, m) => m === "DELETE");
});

test("a disconnect succeeding late does not clear a failure shown after it started", async () => {
  reset();
  await refreshStatus();
  await failureSurvives(() => click(chipButton("p1", "Disconnect p1")), (u) => u.endsWith("/disconnect"));
});

test("a reconnect succeeding late does not clear a failure shown after it started", async () => {
  reset();
  await refreshStatus();
  await failureSurvives(() => click(chipButton("p2", "Reconnect p2 now")), (u) => u.endsWith("/reconnect"));
});

test("a session stop succeeding late does not clear a failure shown after it started", async () => {
  reset();
  status.session = { id: 4, name: "run", auto: false };
  await refreshStatus();
  await failureSurvives(() => click(env.byId("sessionBtn")), (u) => u === "/sessions/stop");
});

test("a session start succeeding late does not clear a failure shown after it started", async () => {
  reset();
  status.session = null;
  await refreshStatus();
  await click(env.byId("sessionBtn"));
  await failureSurvives(() => click(env.byId("sesStart")), (u, m) => m === "POST" && u === "/sessions");
});

test("an action succeeding with no failure since it started still clears the strip", async () => {
  reset();
  await refreshStatus();
  flashDaemonError("an earlier failure");
  await click(chipButton("p1", "Detach p1"));
  assert.equal(env.byId("actionErr").hidden, true);
});
