// settings.js: the PlotJuggler section drives the RUNTIME state and must not lie.
//
// The controls PUT /plotjuggler on change; on a refusal (400) the daemon kept its old
// state, so the checkbox must fall back to what the daemon says while the dest field
// keeps the user's typing (they are mid-correction). "Save as default" applies first
// and only then writes the config, so a state the daemon refused is never saved.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const CONFIG = {
  path: "/tmp/mcuscope.toml", exists: true, restart_required: false, token_set: false,
  server: { host: "127.0.0.1", port: 8765 },
  storage: { db_path: "/tmp/db", retention_days: 7, max_db_bytes: 0, min_sessions: 1,
             auto_session: false },
  ports: [],
  update: { check: false },
  plotjuggler: { enabled: false, dest: "127.0.0.1:9870" },
};

// Daemon-side runtime state the stub maintains; failNextPut simulates a 400.
const daemon = { enabled: false, dest: "127.0.0.1:9870" };
let failNextPut = null;   // error string, consumed by the next PUT /plotjuggler
const puts = [];

globalThis.fetch = async (url, opt = {}) => {
  const method = opt.method || "GET";
  const u = String(url);
  if (u.includes("/plotjuggler")) {
    if (method === "PUT" && !u.includes("/config/")) {
      const body = JSON.parse(opt.body);
      puts.push({ url: u, body });
      if (failNextPut) {
        const error = failNextPut; failNextPut = null;
        return { ok: false, status: 400, json: async () => ({ error }) };
      }
      daemon.enabled = body.enabled;
      if (body.dest) daemon.dest = body.dest;
      return { ok: true, status: 200, json: async () => ({ ...daemon }) };
    }
    if (method === "PUT") {   // /config/plotjuggler
      puts.push({ url: u, body: JSON.parse(opt.body) });
      return { ok: true, status: 200, json: async () => ({ ok: true, restart_required: false }) };
    }
    return { ok: true, status: 200, json: async () => ({ ...daemon }) };
  }
  if (method === "PUT") puts.push({ url: u, body: JSON.parse(opt.body) });
  if (u.includes("/devices")) {
    return { ok: true, status: 200, json: async () => ({ devices: [] }) };
  }
  return { ok: true, status: 200, json: async () => CONFIG };
};

const { initSettings } = await import(webuiUrl("settings.js"));

const box = () => env.byId("cfgPjEnabled");
const dest = () => env.byId("cfgPjDest");
const errSlot = () => env.byId("cfgPjErr");

test("opening the dialog renders the daemon's runtime state", async () => {
  initSettings();
  env.byId("settingsBtn").emit("click", {});
  await tick(0);
  await tick(0);
  assert.equal(box().checked, false);
  assert.equal(dest().value, "127.0.0.1:9870");
});

test("a change applies live and echoes the daemon's answer", async () => {
  puts.length = 0;
  box().checked = true;
  dest().value = "10.0.0.5:9870";
  dest().emit("change", {});
  await tick(0);
  const put = puts.find((p) => !p.url.includes("/config/"));
  assert.ok(put, "the change must PUT /plotjuggler");
  assert.deepEqual(put.body, { enabled: true, dest: "10.0.0.5:9870" });
  assert.equal(box().checked, true);
  assert.equal(errSlot().textContent, "");
});

test("a refused change re-syncs the checkbox and keeps the typed dest", async () => {
  puts.length = 0;
  daemon.enabled = false;           // the daemon is off and stays off
  failNextPut = "destination must be host:port, not 'myhost'";
  box().checked = true;             // the user ticks the box over a bad dest
  dest().value = "myhost";
  box().emit("change", {});
  await tick(0);
  await tick(0);
  assert.match(errSlot().textContent, /host:port/, "the refusal is named in the error slot");
  assert.equal(box().checked, false, "the checkbox must not show a stream the daemon refused");
  assert.equal(dest().value, "myhost", "the user's typing survives for correction");
  assert.equal(puts.filter((p) => p.url.includes("/config/")).length, 0);
});

test("save as default applies, then writes the config with the applied values", async () => {
  puts.length = 0;
  box().checked = true;
  dest().value = "10.0.0.6:9870";
  env.byId("cfgPjSave").emit("click", {});
  await tick(0);
  await tick(0);
  const runtime = puts.find((p) => !p.url.includes("/config/"));
  const saved = puts.find((p) => p.url.includes("/config/plotjuggler"));
  assert.ok(runtime && saved, "save issues both PUTs");
  assert.deepEqual(saved.body, { enabled: true, dest: "10.0.0.6:9870" });
});

test("save as default saves nothing when the apply is refused", async () => {
  puts.length = 0;
  failNextPut = "destination port must be 1..65535, not 0";
  dest().value = "10.0.0.7:0";
  env.byId("cfgPjSave").emit("click", {});
  await tick(0);
  await tick(0);
  assert.equal(puts.filter((p) => p.url.includes("/config/")).length, 0,
    "a state the daemon refused to run must never become the saved default");
  assert.match(errSlot().textContent, /1\.\.65535/);
});
