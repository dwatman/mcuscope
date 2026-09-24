// statusbar.js: a page whose index.html the daemon stamped with its version compares /status
// against that stamp, not the first version it sees. A tab rebuilt by Back from the cached old
// page first polls the upgraded daemon, and must still offer the reload.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
const META = 'meta[name="mcuscope-version"]';
const query = env.document.querySelector;
env.document.querySelector = (sel) => (sel === META ? { content: "0.5.0" } : query(sel));
let status = null;
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => status });

const { refreshStatus, initStatusbar } = await import(webuiUrl("statusbar.js"));
initStatusbar();
const badge = env.byId("reloadBadge");

const poll = (version) => {
  status = { version, uptime_s: 0, db_size_bytes: 0, ports: [], write_errors: 0, session: null };
  return refreshStatus();
};

test("the first poll already reading a newer daemon shows the notice", async () => {
  await poll("0.6.0");
  assert.equal(badge.hidden, false, "the cached 0.5.0 page adopted the new daemon's version");
  await poll("0.6.0");
  assert.equal(badge.hidden, false);
  await poll("0.5.0");
  assert.equal(badge.hidden, true, "the daemon that built this page is not news");
});

test("index.html carries the placeholder the daemon stamps", () => {
  const html = readFileSync(new URL("../../mcuscope/webui/index.html", import.meta.url), "utf8");
  assert.match(html, /<meta name="mcuscope-version" content="__MCUSCOPE_VERSION__">/);
});
