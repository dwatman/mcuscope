// Sweep-stage ruling: a status refresh asked for after an action waits for a poll started after
// it. Sharing a poll already in flight showed the state from before the action (a detached
// chip stayed up to the next 5 s poll); background polls still share.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();
const ok = (body) => ({ ok: true, status: 200, json: async () => body });
let attached = true;
let holdStatus = false;
const heldStatus = [];
let statusCalls = 0;

function statusBody(ports) {
  return { version: "0.5.0", uptime_s: 1, db_size_bytes: 0, db_content_bytes: 0, ports,
           write_errors: 0, session: null };
}
const PORT = { alias: "sim", device: "socket://127.0.0.1:9900", baud: 115200, connected: true,
               eol: "lf", target: null };

globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  if (u === "/status") {
    statusCalls++;
    const body = statusBody(attached ? [PORT] : []);   // read when the daemon receives it
    if (holdStatus) return new Promise((res) => heldStatus.push(() => res(ok(body))));
    return ok(body);
  }
  if ((opt.method || "GET") === "DELETE" && u === "/ports/sim") { attached = false; return ok({ ok: true }); }
  return ok({ ok: true });
};

const { initStatusbar, refreshStatus } = await import(webuiUrl("statusbar.js"));
initStatusbar();
async function settle() { for (let i = 0; i < 6; i++) await tick(0); }

test("a detach while a poll is in flight ends with the chip gone, not the pre-detach poll's", async () => {
  attached = true; holdStatus = false;
  await refreshStatus();
  const x = env.byId("ports").querySelectorAll(".x")[0];
  assert.ok(x, "no chip to detach");
  holdStatus = true;
  const early = refreshStatus();          // the interval's poll, reading before the detach
  await settle();
  x.emit("click", {});                    // DELETE lands, then refreshStatus(true)
  await settle();
  holdStatus = false;
  heldStatus.shift()();                   // the pre-detach answer: sim still attached
  await early;
  await settle();
  assert.equal(env.byId("ports").querySelectorAll(".x").length, 0,
    "the chip of the detached port is still up: the refresh reused a poll from before it");
});

test("background callers still share the poll in flight; fresh callers share one follow-up", async () => {
  attached = true; holdStatus = true; heldStatus.length = 0;
  const before = statusCalls;
  const p1 = refreshStatus();
  const p2 = refreshStatus();
  const f1 = refreshStatus(true);
  const f2 = refreshStatus(true);
  await settle();
  assert.equal(statusCalls - before, 1, "a background caller started a second poll");
  holdStatus = false;
  heldStatus.shift()();
  await Promise.all([p1, p2, f1, f2]);
  assert.equal(statusCalls - before, 2, "fresh callers must share one follow-up poll");
});
