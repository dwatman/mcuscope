// settings.js: a port row's baud must be validated, not dropped.
//
// collectPorts omitted the baud whenever the field was empty or non-positive, and the daemon
// then applied its own default (115200) to a PUT that simply had no baud in it. So clearing
// the box on a 921600 port silently rewrote it to 115200 with a green "saved" - the opposite
// of what the user did. Every other numeric field in this dialog (server port, retention, size
// cap, sessions) names its refusal in the section's error slot and saves nothing; this is that
// treatment for baud.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const CONFIG = {
  path: "/tmp/mcuscope.toml", exists: true, restart_required: false, token_set: false,
  server: { host: "127.0.0.1", port: 8765 },
  storage: { db_path: "/tmp/db", retention_days: 7, max_db_bytes: 0, min_sessions: 1,
             auto_session: false },
  ports: [{ alias: "board", device: "socket://127.0.0.1:9900", baud: 921600, autoconnect: true }],
  update: { check: false },
};

const puts = [];
globalThis.fetch = async (url, opt = {}) => {
  const method = opt.method || "GET";
  if (method === "PUT") puts.push({ url: String(url), body: JSON.parse(opt.body) });
  if (String(url).includes("/devices")) {
    return { ok: true, status: 200, json: async () => ({ devices: [] }) };
  }
  return { ok: true, status: 200, json: async () => CONFIG };
};

const { initSettings } = await import(webuiUrl("settings.js"));

const baudInput = () => env.byId("cfgPortsBody").querySelectorAll("tr")[0]._fields.baudInput;

test("open the dialog on a saved 921600 port", async () => {
  initSettings();
  env.byId("settingsBtn").emit("click", {});
  await tick(0);
  await tick(0);
  assert.equal(baudInput().value, 921600, "the fixture did not render the saved port row");
});

for (const [label, value] of [["cleared", ""], ["zero", "0"],
                              ["negative", "-1"], ["not a number", "abc"]]) {
  test(`a ${label} baud is refused by name and saves nothing`, async () => {
    puts.length = 0;
    baudInput().value = value;
    env.byId("cfgPortsSave").emit("click", {});
    await tick(0);
    assert.equal(puts.length, 0,
      `a ${label} baud was saved; the daemon defaults the missing field to 115200`);
    assert.match(env.byId("cfgPortsErr").textContent, /baud/,
      "the refusal must be named in the section's error slot, like every other numeric field");
    assert.match(env.byId("cfgPortsErr").textContent, /board/, "and must name the row");
  });
}

test("a baud above the daemon's bound is refused here, not by a 422", async () => {
  puts.length = 0;
  baudInput().value = "200000000";   // MAX_BAUD is 100000000
  env.byId("cfgPortsSave").emit("click", {});
  await tick(0);
  assert.equal(puts.length, 0, "the dialog must not send a value the daemon answers 422 for");
  assert.match(env.byId("cfgPortsErr").textContent, /1-100000000/,
    "the refusal names the bound, in the dialog's own wording");
});

test("the bound itself still saves", async () => {
  puts.length = 0;
  baudInput().value = "100000000";
  env.byId("cfgPortsSave").emit("click", {});
  await tick(0);
  await tick(0);
  const put = puts.find((p) => p.url.includes("/config/ports"));
  assert.ok(put, "MAX_BAUD is inclusive on both sides");
  assert.equal(put.body.ports[0].baud, 100000000);
});

test("a valid baud still saves, and carries the typed value", async () => {
  puts.length = 0;
  baudInput().value = "460800";
  env.byId("cfgPortsSave").emit("click", {});
  await tick(0);
  await tick(0);
  const put = puts.find((p) => p.url.includes("/config/ports"));
  assert.ok(put, "a valid row must still save");
  assert.equal(put.body.ports[0].baud, 460800);
  assert.equal(put.body.ports[0].alias, "board");
});
