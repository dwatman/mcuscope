// state.js: the browser-side line ending must never widen what reaches the daemon.
//
// The daemon answers 422 for any `eol` outside none|lf|crlf, so a hand-edited (or
// older-build) localStorage value must be dropped on read, not carried into a request
// body. The other half is the "port default" case: it is spelled by OMITTING the field,
// so a browser that never touched the setting posts exactly the body it always did - an
// explicit null would be a different request from the one this feature promises.
//
// The <select> itself is manual-verify: the stub cannot lay one out.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();

const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

const { getEol, setEol, eolField } = await import(webuiUrl("state.js"));

test("the default is the port's, and that means no field at all", () => {
  assert.equal(getEol(), "");
  assert.deepEqual(eolField(), {},
    "port default must omit `eol`, not send null or an empty string");
  assert.equal(Object.hasOwn(eolField(), "eol"), false);
});

test("a chosen ending is carried on the body and persisted", () => {
  setEol("crlf");
  assert.deepEqual(eolField(), { eol: "crlf" });
  assert.equal(store.get("mcuscope.eol"), "crlf");
});

test("none is a real choice, not a falsy one", () => {
  setEol("none");
  assert.equal(getEol(), "none");
  assert.deepEqual(eolField(), { eol: "none" },
    "the bare-control-character setting was dropped as if it were the default");
});

test("going back to the port default clears the stored key", () => {
  setEol("crlf");
  setEol("");
  assert.equal(getEol(), "");
  assert.deepEqual(eolField(), {});
  assert.equal(store.has("mcuscope.eol"), false,
    "a stale key would resurrect the old ending on the next page load");
});

for (const bad of ["cr", "LF", "\r\n", "lf ", null, undefined, 1, {}]) {
  test(`a bad value (${JSON.stringify(bad)}) falls back to the port default`, () => {
    setEol("crlf");
    setEol(bad);
    assert.equal(getEol(), "", "an unknown ending would reach the daemon as a 422");
    assert.deepEqual(eolField(), {});
  });
}

test("a hand-edited storage value is rejected on load, not on send", async () => {
  // A fresh module instance, so the module-scope read runs against the poisoned value.
  store.set("mcuscope.eol", "CRLF");
  const again = await import(webuiUrl("state.js") + "?poisoned");
  assert.equal(again.getEol(), "", "the unvalidated value survived the load");
  assert.deepEqual(again.eolField(), {});
});

test("a storage that throws does not take the module down", async () => {
  const saved = globalThis.localStorage;
  globalThis.localStorage = {
    getItem() { throw new Error("SecurityError"); },
    setItem() { throw new Error("SecurityError"); },
    removeItem() { throw new Error("SecurityError"); },
  };
  try {
    const blocked = await import(webuiUrl("state.js") + "?blocked");
    assert.equal(blocked.getEol(), "");
    blocked.setEol("crlf");   // must not throw
    assert.deepEqual(blocked.eolField(), { eol: "crlf" },
      "the failed persist took the applied setting down with it");
  } finally {
    globalThis.localStorage = saved;
  }
});
