// statusbar.js: the attach dialog must open once, with each device once, even against a
// daemon that is slow or never answers.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

let devicesMode = "ok";          // "ok" | "hold" | "stall"
const heldDevices = [];
const ok = (body) => ({ ok: true, status: 200, json: async () => body });

globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  if (u === "/devices") {
    const body = { devices: [{ device: "/dev/ttyACM0", description: "STLINK" }] };
    if (devicesMode === "hold") return new Promise((res) => heldDevices.push(() => res(ok(body))));
    if (devicesMode === "stall") {
      return new Promise((_, rej) => {
        if (opt.signal) opt.signal.addEventListener("abort", () => rej(opt.signal.reason));
      });
    }
    return ok(body);
  }
  return ok({ ok: true });
};

const { initStatusbar } = await import(webuiUrl("statusbar.js"));
initStatusbar();

async function settle() { for (let i = 0; i < 6; i++) await tick(0); }

// ---- E-5 and E-6: the attach dialog -----------------------------------------------------

const dlg = env.byId("attachDlg");
let shows = 0;
dlg.showModal = function () { shows++; this.attrs.set("open", ""); };
const devOptions = () => env.byId("devSel").children.map((o) => o.value);

test("E-5: two + Attach clicks before /devices answers list each device once and open once", async () => {
  shows = 0;
  dlg.removeAttribute("open");
  devicesMode = "hold";
  env.byId("attachBtn").emit("click", {});
  env.byId("attachBtn").emit("click", {});
  await settle();
  assert.equal(heldDevices.length, 2);
  heldDevices.splice(0).forEach((release) => release());   // oldest first
  await settle();
  devicesMode = "ok";
  assert.deepEqual(devOptions(), ["/dev/ttyACM0", "socket://127.0.0.1:9900", "custom"]);
  assert.equal(shows, 1, "a superseded open still called showModal");
});

test("E-5: the newer fill answering first is not overwritten by the older one", async () => {
  shows = 0;
  dlg.removeAttribute("open");
  devicesMode = "hold";
  env.byId("attachBtn").emit("click", {});
  env.byId("attachBtn").emit("click", {});
  await settle();
  const [older, newer] = heldDevices.splice(0);
  newer();
  await settle();
  older();
  await settle();
  devicesMode = "ok";
  assert.deepEqual(devOptions(), ["/dev/ttyACM0", "socket://127.0.0.1:9900", "custom"]);
  assert.equal(shows, 1);
});

test("E-6: against a daemon that never answers, Attach opens at once, held, and says why at the deadline", async () => {
  const realTimeout = AbortSignal.timeout;
  const armed = [];
  AbortSignal.timeout = (ms) => { const ac = new AbortController(); armed.push({ ms, ac }); return ac.signal; };
  shows = 0;
  dlg.removeAttribute("open");
  devicesMode = "stall";
  env.byId("attachBtn").emit("click", {});
  await settle();
  assert.equal(shows, 1, "the dialog waited for /devices before opening");
  assert.deepEqual(env.byId("devSel").children.map((o) => o.textContent), ["loading devices..."]);
  assert.equal(env.byId("dlgAttach").disabled, true, "Attach is live before the device list landed");
  assert.equal(armed.length, 1, "no deadline armed on /devices");
  assert.ok(armed[0].ms > 0 && armed[0].ms < 5000);
  armed[0].ac.abort(new DOMException("The operation was aborted due to timeout", "TimeoutError"));
  await settle();
  AbortSignal.timeout = realTimeout;
  devicesMode = "ok";
  assert.equal(shows, 1, "the attach dialog opened twice");
  assert.equal(env.byId("dlgAttach").disabled, false, "Attach stayed held after the list landed");
  assert.equal(env.byId("dlgErr").textContent, "could not list devices: no reply from daemon");
  assert.deepEqual(devOptions(), ["socket://127.0.0.1:9900", "custom"],
    "custom entry is still offered, which is what the offline dialog is for");
});
