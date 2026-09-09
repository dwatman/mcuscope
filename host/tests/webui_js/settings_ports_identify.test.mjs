// settings.js: the port row's identify checkbox seeds from the saved value and is sent
// explicitly, so unticking it silences the connect-time ping for a console.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const CONFIG = {
  path: "/tmp/mcuscope.toml", exists: true, restart_required: false, token_set: false,
  server: { host: "127.0.0.1", port: 8558 },
  storage: { db_path: "/tmp/db", retention_days: 7, max_db_bytes: 0, min_sessions: 1,
             auto_session: false },
  ports: [{ alias: "mcu", device: "/dev/ttyACM0", baud: 115200, autoconnect: true, identify: true },
          { alias: "sbc", device: "/dev/ttyUSB0", baud: 115200, autoconnect: true, identify: false },
          { alias: "old", device: "/dev/ttyUSB1", baud: 115200, autoconnect: true }],
  update: { check: false },
};

const puts = [];
globalThis.fetch = async (url, opt = {}) => {
  if ((opt.method || "GET") === "PUT") puts.push({ url: String(url), body: JSON.parse(opt.body) });
  if (String(url).includes("/devices")) {
    return { ok: true, status: 200, json: async () => ({ devices: [] }) };
  }
  return { ok: true, status: 200, json: async () => CONFIG };
};

const { initSettings } = await import(webuiUrl("settings.js"));
const rows = () => env.byId("cfgPortsBody").querySelectorAll("tr");

test("the checkbox seeds from the saved value; a missing key reads as on", async () => {
  initSettings();
  env.byId("settingsBtn").emit("click", {});
  await tick(0);
  await tick(0);
  const [mcu, sbc, old] = rows().map((tr) => tr._fields.idInput.checked);
  assert.deepEqual([mcu, sbc, old], [true, false, true]);
});

test("a save sends identify per row, off where unticked", async () => {
  puts.length = 0;
  rows()[0]._fields.idInput.checked = false;
  env.byId("cfgPortsSave").emit("click", {});
  await tick(0);
  await tick(0);
  const put = puts.find((p) => p.url.includes("/config/ports"));
  assert.ok(put, "nothing was saved");
  const sent = Object.fromEntries(put.body.ports.map((p) => [p.alias, p.identify]));
  assert.deepEqual(sent, { mcu: false, sbc: false, old: true },
    "identify must be explicit in the body, not left to the daemon's keep-saved fallback");
});
