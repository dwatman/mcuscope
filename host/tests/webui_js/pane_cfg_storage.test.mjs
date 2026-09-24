// pane.js paneCfgFromStorage: a well-typed pane config is kept as it is. The per-field fallback
// through termState is pinned in terminal_createpane.

import test from "node:test";
import assert from "node:assert/strict";
import { paneCfgFromStorage, ALL_CHANS } from "../../mcuscope/webui/pane.js";

test("paneCfgFromStorage keeps a well-typed config as it is", () => {
  assert.deepEqual(paneCfgFromStorage({ port: "sim", channels: ["cmd", "resp"], regex: "ERR" }),
    { port: "sim", channels: ["cmd", "resp"], regex: "ERR" });
  assert.deepEqual(paneCfgFromStorage("x"), { port: "all", channels: ALL_CHANS, regex: "" });
  assert.deepEqual(paneCfgFromStorage({ port: "", channels: [1, "sys"], regex: 5 }),
    { port: "all", channels: ["sys"], regex: "" });
});
