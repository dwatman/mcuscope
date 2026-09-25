// state.js downloadPath: a session .db export sent as a navigation asks the daemon to queue for
// a build slot (`wait=1`), because a navigation cannot show the pool's 503. Every fetched export
// (the bundle, and the .db on the token path) keeps the 503 and reports its text.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();

const BUSY = "too many session exports in progress; try again shortly";
const fetched = [];
globalThis.fetch = async (url) => {
  const u = String(url);
  fetched.push(u);
  if (u.includes("check=1")) {   // the navigation's preflight (SPEC 3.4): a slot is free
    return { ok: true, status: 200, headers: { get: () => null }, json: async () => ({ ok: true }) };
  }
  if (u.startsWith("/sessions/2/")) {
    return { ok: false, status: 503, headers: { get: () => null }, json: async () => ({ error: BUSY }) };
  }
  return { ok: true, status: 200, headers: { get: () => null }, json: async () => ({}),
           blob: async () => new Blob(["x"]) };
};

const navigations = [];
const create = env.document.createElement;
env.document.createElement = (tag) => {
  const el = create(tag);
  if (String(tag).toLowerCase() === "a") {
    el.click = () => { if (!String(el.href).startsWith("blob:")) navigations.push(el.href); };
  }
  return el;
};

const { downloadPath, setToken } = await import(webuiUrl("state.js"));

test("a session .db navigation queues for a build slot; other navigations do not", async () => {
  setToken(null);
  navigations.length = 0;
  assert.equal(await downloadPath("/sessions/2/export", "r.db", "session export"), null);
  assert.equal(await downloadPath("/sessions/2/export?x=1", "r.db", "session export"), null);
  assert.equal(await downloadPath("/lines/export?format=jsonl", "l.jsonl", "export"), null);
  assert.deepEqual(navigations,
    ["/sessions/2/export?wait=1", "/sessions/2/export?x=1&wait=1", "/lines/export?format=jsonl"]);
});

test("the bundle, a fetch, reports the pool's 503 and never asks to wait", async () => {
  setToken(null);
  fetched.length = 0; navigations.length = 0;
  assert.equal(await downloadPath("/sessions/2/bundle", "b.zip", "bundle export"),
               `bundle export failed: ${BUSY}`);
  assert.deepEqual(fetched, ["/sessions/2/bundle"]);
  assert.deepEqual(navigations, []);
});

test("a session .db on the token path is a fetch: the 503 is reported, no wait asked", async () => {
  setToken("t");
  try {
    fetched.length = 0; navigations.length = 0;
    assert.equal(await downloadPath("/sessions/2/export", "r.db", "session export"),
                 `session export failed: ${BUSY}`);
    assert.deepEqual(fetched, ["/sessions/2/export"]);
    assert.deepEqual(navigations, []);
  } finally {
    setToken(null);
  }
});
