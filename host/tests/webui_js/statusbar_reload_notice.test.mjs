// statusbar.js: a page left open across a daemon upgrade offers a reload once /status reports
// a version other than the one the page first saw. This is the unstamped page: index.html still
// holds the placeholder, as when it was not served by a daemon that fills it in.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
const META = 'meta[name="mcuscope-version"]';
const query = env.document.querySelector;
env.document.querySelector = (sel) => (sel === META ? { content: "__MCUSCOPE_VERSION__" } : query(sel));
let status = null;
let fail = false;
globalThis.fetch = async () => {
  if (fail) throw new Error("connection refused");
  return { ok: true, status: 200, json: async () => status };
};
let reloads = 0;
globalThis.location.reload = () => { reloads += 1; };

const { refreshStatus, initStatusbar } = await import(webuiUrl("statusbar.js"));
initStatusbar();
const badge = env.byId("reloadBadge");

const poll = (version) => {
  status = { version, uptime_s: 0, db_size_bytes: 0, ports: [], write_errors: 0, session: null };
  return refreshStatus();
};

test("the first version seen is the page's; a different one shows the notice", async () => {
  badge.hidden = false;   // so the first poll has to hide it
  await poll("0.4.0");
  assert.equal(badge.hidden, true, "the version that served the page is not news");
  await poll("0.4.0");
  assert.equal(badge.hidden, true);
  fail = true;
  await refreshStatus();
  fail = false;
  assert.equal(badge.hidden, true, "an unreachable daemon is not an upgrade");
  await poll("0.5.0");
  assert.equal(badge.hidden, false, "the daemon was upgraded under the open page");
  assert.equal(env.byId("brandVer").textContent, "0.5.0");
  await poll("0.4.0");
  assert.equal(badge.hidden, true, "back on the page's own version, nothing to reload");
});

test("the notice reloads the page", async () => {
  await poll("0.5.0");
  badge.emit("click", {});
  assert.equal(reloads, 1);
});
