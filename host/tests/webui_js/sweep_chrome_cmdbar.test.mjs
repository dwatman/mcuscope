// Class 73 sweep, cmdbar.js: a marker's answer does not wipe a label typed while it was out, and
// does not write the result strip over a newer command or marker.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const ok = (body) => ({ ok: true, status: 200, json: async () => body });
const held = [];
let holdRules = [];
function holdNext(pred) { holdRules.push({ pred, n: 1 }); }
async function release(pred, answer) {
  const i = held.findIndex((h) => pred(h.u, h.body));
  assert.ok(i >= 0, "setup: no such request is held");
  held.splice(i, 1)[0].go(answer);
  await settle();
}

globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  const body = opt.body ? JSON.parse(opt.body) : {};
  const rule = holdRules.find((r) => r.n > 0 && r.pred(u, body));
  if (rule) {
    rule.n--;
    const given = await new Promise((go) => held.push({ u, body, go }));
    if (given) return given;
  }
  if (u === "/cmd") return ok({ status: "ok", data: "pong " + body.cmd, latency_ms: 1 });
  return ok({ line_id: 1 });
};

const { state } = await import(webuiUrl("state.js"));
const { setKnownPorts } = await import(webuiUrl("terminal.js"));
const { initCmdBar, setCmdMode } = await import(webuiUrl("cmdbar.js"));
initCmdBar();
state.portTarget = Object.assign(Object.create(null), { p1: "board" });
setKnownPorts(["p1"]);
setCmdMode("cmd", true);

async function settle() { for (let i = 0; i < 8; i++) await tick(0); }
const strip = () => env.byId("cmdResult").textContent;
const isMarker = (label) => (u, b) => u === "/marker" && (label === undefined || b.text === label);
const isCmd = (u) => u === "/cmd";

async function marker(text) {
  env.byId("markerInput").value = text;
  env.byId("markerInput").emit("keydown", { key: "Enter", preventDefault() {} });
  await settle();
}
async function command(text) {
  env.byId("cmdInput").value = text;
  env.byId("cmdInput").emit("keydown", { key: "Enter", preventDefault() {} });
  await settle();
}

test("a marker's answer keeps the next label typed while it was out", async () => {
  holdNext(isMarker());
  await marker("first");
  env.byId("markerInput").value = "second label";
  await release(isMarker());
  assert.equal(env.byId("markerInput").value, "second label");
});

test("a marker's ack landing after a newer command leaves the command's result", async () => {
  holdNext(isMarker());
  await marker("late mark");
  await command("ping1");
  await release(isMarker());
  assert.match(strip(), /pong ping1/);
  assert.doesNotMatch(strip(), /late mark/);
});

test("a marker's refusal landing after a newer command leaves the command's result", async () => {
  holdNext(isMarker());
  await marker("late mark");
  await command("ping2");
  await release(isMarker(), { ok: false, status: 422, json: async () => ({ error: "text: stale refusal" }) });
  assert.match(strip(), /pong ping2/);
  assert.doesNotMatch(strip(), /stale refusal/);
});

test("a command pending under a marker still shows its own verdict", async () => {
  holdNext(isCmd);
  await command("slow");
  await marker("while waiting");
  assert.match(strip(), /while waiting/);
  await release(isCmd);
  assert.match(strip(), /pong slow/);
});

test("an older marker's ack does not overwrite a newer marker's", async () => {
  holdNext(isMarker("older"));
  await marker("older");
  await marker("newer");
  await release(isMarker("older"));
  assert.match(strip(), /newer/);
  assert.doesNotMatch(strip(), /older/);
});
