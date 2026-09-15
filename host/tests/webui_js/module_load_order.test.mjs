// Every webui module must load when it is the FIRST one imported.
//
// The webui has hand-wired import cycles (app.js:23 names plots<->digital and *->terminal),
// broken by a callback the importing side registers rather than a static import back. A cycle
// that is not broken loads only in the order app.js happens to reach it: the module evaluated
// second initialises fine, the one evaluated first reads a `let` of its partner still in the
// temporal dead zone and dies with "Cannot access 'x' before initialization". Every existing
// test file imports plots.js before digital.js, so none of them can see it.
//
// One fresh `node` process per module, because module evaluation happens once per process:
// importing a module that a previous test already pulled in as a dependency proves nothing.

import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { webuiDir, webuiUrl } from "./dom_stub.mjs";

const STUB = new URL("./dom_stub.mjs", import.meta.url).href;

const MODULES = readdirSync(webuiDir())
  .filter((f) => f.endsWith(".js"))
  .sort();

// A guard against the glob silently going empty (a moved directory, a renamed suffix): the
// checks below would then all pass by declaring nothing.
test("the module list is non-empty and plausible", () => {
  assert.ok(MODULES.length >= 10, `only ${MODULES.length} webui modules found: ${MODULES}`);
  for (const name of ["app.js", "plots.js", "digital.js", "terminal.js"]) {
    assert.ok(MODULES.includes(name), `${name} missing from ${MODULES}`);
  }
});

function loadFirst(name) {
  const script =
    `import { installDom, webuiUrl } from ${JSON.stringify(STUB)};` +
    "installDom();" +
    `await import(webuiUrl(${JSON.stringify(name)}));`;
  return spawnSync(process.execPath, ["--input-type=module", "-e", script], {
    encoding: "utf8",
  });
}

for (const name of MODULES) {
  test(`${name} loads when imported first`, () => {
    const proc = loadFirst(name);
    assert.equal(
      proc.status,
      0,
      `importing ${name} first failed (exit ${proc.status}):\n${proc.stderr}`,
    );
  });
}

// A positive control for the harness: a module that genuinely throws at load must be seen to
// fail. Without it every check above is satisfied by a spawn that silently never ran.
test("the load probe reports a module that throws at import time", () => {
  const script =
    `import { installDom } from ${JSON.stringify(STUB)};` +
    "installDom();" +
    `await import(${JSON.stringify(webuiUrl("no-such-module.js"))});`;
  const proc = spawnSync(process.execPath, ["--input-type=module", "-e", script], {
    encoding: "utf8",
  });
  assert.notEqual(proc.status, 0, "a missing module must not exit 0");
  assert.match(proc.stderr, /ERR_MODULE_NOT_FOUND|Cannot find module/);
});
