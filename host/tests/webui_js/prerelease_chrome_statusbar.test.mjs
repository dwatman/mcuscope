// statusbar.js and cmdbar.js, pre-release round: a status poll must not move focus, the command
// bar must not keep a resolved port while the daemon is unreachable, and the attach dialog must
// open once, with each device once, even against a daemon that is slow or never answers.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

let status = null;
let statusDown = false;
let devicesMode = "ok";          // "ok" | "hold" | "stall"
const heldDevices = [];
const ok = (body) => ({ ok: true, status: 200, json: async () => body });

globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  if (u === "/status") {
    if (statusDown) throw new TypeError("Failed to fetch");
    return ok(status);
  }
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

const { initCmdBar } = await import(webuiUrl("cmdbar.js"));
const { initStatusbar, refreshStatus } = await import(webuiUrl("statusbar.js"));
initCmdBar();
initStatusbar();

async function settle() { for (let i = 0; i < 6; i++) await tick(0); }
function port(alias, over = {}) {
  return { alias, device: "socket://127.0.0.1:9900", baud: 115200, connected: true, eol: "lf",
           target: null, ...over };
}
function setStatus(ports) {
  status = { version: "0.4.0", uptime_s: 1, db_size_bytes: 0, db_content_bytes: 0, ports,
             write_errors: 0, session: null };
}
const autoLabel = () => env.byId("cmdPort").children.find((o) => o.value === "auto").textContent;
const prompt = () => env.byId("prompt").textContent;

// ---- E-4 --------------------------------------------------------------------------------

test("E-4: OK monitor arriving on a poll flips the mode without focusing the command input", async () => {
  const focused = [];
  env.byId("cmdInput").focus = () => focused.push("cmdInput");
  setStatus([port("sim")]);
  await refreshStatus();
  assert.equal(prompt(), "$", "a port that never answered OK monitor is raw");
  setStatus([port("sim", { target: "sim" })]);
  await refreshStatus();
  assert.equal(prompt(), ">", "the poll did flip the mode");
  assert.deepEqual(focused, [], "a background poll moved focus into the command input");
});

test("E-4: a second port making auto ambiguous changes mode without focus; a click still focuses", async () => {
  const focused = [];
  env.byId("cmdInput").focus = () => focused.push("cmdInput");
  setStatus([port("sim", { target: "sim" }), port("sbc", { target: null })]);
  await refreshStatus();
  assert.equal(autoLabel(), "(auto)");
  assert.equal(prompt(), "$", "an ambiguous auto falls back to raw");
  assert.deepEqual(focused, []);
  const { setCmdMode } = await import(webuiUrl("cmdbar.js"));
  setCmdMode("cmd", true);   // what the mode toggle's click handler calls
  assert.deepEqual(focused, ["cmdInput"], "the user's own pick still puts the caret in the input");
});

// ---- E-7 --------------------------------------------------------------------------------

test("E-7: offline, auto names no port and the input says why; back online it resolves again", async () => {
  setStatus([port("sim", { target: "sim" })]);
  await refreshStatus();
  assert.equal(autoLabel(), "(sim)");
  statusDown = true;
  await refreshStatus();
  assert.equal(env.byId("daemonVer").textContent, "daemon unreachable");
  assert.equal(autoLabel(), "(offline)", "the bar still claimed a resolved port");
  assert.equal(env.byId("cmdInput").placeholder, "daemon unreachable");
  assert.equal(env.byId("cmdInput").disabled, false,
    "one failed poll must not disable (and so blur) a line being typed");
  await refreshStatus();
  assert.equal(autoLabel(), "(offline)", "a second failed poll keeps it");
  statusDown = false;
  await refreshStatus();
  assert.equal(autoLabel(), "(sim)");
  assert.match(env.byId("cmdInput").placeholder, /type a command/);
});

test("E-7: offline with no port ever known still says offline, not 'attach a port'", async () => {
  setStatus([]);
  await refreshStatus();
  assert.equal(env.byId("cmdInput").placeholder, "attach a port to send commands");
  statusDown = true;
  await refreshStatus();
  statusDown = false;
  assert.equal(env.byId("cmdInput").placeholder, "daemon unreachable");
  assert.equal(autoLabel(), "(offline)");
});

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

test("E-6: against a daemon that never answers, Attach opens once the deadline fires and says why", async () => {
  const realTimeout = AbortSignal.timeout;
  const armed = [];
  AbortSignal.timeout = (ms) => { const ac = new AbortController(); armed.push({ ms, ac }); return ac.signal; };
  shows = 0;
  dlg.removeAttribute("open");
  devicesMode = "stall";
  env.byId("attachBtn").emit("click", {});
  await settle();
  assert.equal(shows, 0);
  assert.equal(armed.length, 1, "no deadline armed on /devices");
  assert.ok(armed[0].ms > 0 && armed[0].ms < 5000);
  armed[0].ac.abort(new DOMException("The operation was aborted due to timeout", "TimeoutError"));
  await settle();
  AbortSignal.timeout = realTimeout;
  devicesMode = "ok";
  assert.equal(shows, 1, "the attach dialog never opened against a stalled daemon");
  assert.equal(env.byId("dlgErr").textContent, "could not list devices: no reply from daemon");
  assert.deepEqual(devOptions(), ["socket://127.0.0.1:9900", "custom"],
    "custom entry is still offered, which is what the offline dialog is for");
});
