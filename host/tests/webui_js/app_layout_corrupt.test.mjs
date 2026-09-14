// A stored layout that cannot be read boots with the defaults, and is not half-applied.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };
env.store.set("mcuscope.layout", '{"sideW": "wide", "hidden": "yes", "canCap": 400, "expanded": 1');
env.byId("workspace").clientWidth = 1600;
await import(webuiUrl("app.js"));

test("a corrupt layout boots at the defaults", () => {
  assert.equal(env.byId("workspace").style["--side-w"], "360px");
  assert.equal(env.byId("workspace").classList.contains("collapsed"), false);
  assert.equal(env.byId("sidebar").style["--can-h"], undefined, "the stylesheet's 45 percent stands");
  assert.equal(env.byId("popoutBtn").textContent, "↔ expand");
});
