// Fix-diff round 2, state.js preflight (FD2-5): a session `.db` export is checked against
// GET /sessions?name=<ref>, and the reference comes out of the export path, where the daemon
// resolves it as id-then-name. Anything but a bare number has to be encoded into the query.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
const fetches = [];
let sessions = [{ id: 3, name: "run&a#b" }];

globalThis.fetch = async (url) => {
  fetches.push(String(url));
  return { ok: true, status: 200, headers: { get: () => null },
           json: async () => ({ sessions }) };
};

const { downloadPath, setToken } = await import(webuiUrl("state.js"));
setToken(null);   // the token-less path: preflight, then a plain navigation

// The <a download> the navigation goes out on.
function anchors() {
  const created = [];
  const orig = env.document.createElement;
  env.document.createElement = (t) => {
    const el = orig(t);
    if (String(t).toLowerCase() === "a") created.push(el);
    return el;
  };
  return { created, restore: () => { env.document.createElement = orig; } };
}

test("FD2-5: a session name carrying & and # is one query parameter, not three", async () => {
  fetches.length = 0;
  sessions = [{ id: 3, name: "run&a#b" }];
  const a = anchors();
  const path = "/sessions/run&a#b/export";
  assert.equal(await downloadPath(path, "x.db", "session export"), null);
  a.restore();
  assert.deepEqual(fetches, ["/sessions?name=run%26a%23b"],
                   "the name ended the parameter early: the daemon was asked about another session");
  assert.equal(a.created.at(-1).href, path, "the download itself still goes to the export path");
});

test("FD2-5: a plain id is untouched, and a session that is gone is named as the user wrote it",
  async () => {
    fetches.length = 0;
    sessions = [{ id: 2, name: "run-b" }];
    const a = anchors();
    assert.equal(await downloadPath("/sessions/2/export", "x.db", "session export"), null);
    a.restore();
    assert.deepEqual(fetches, ["/sessions?name=2"], "positive control: nothing to encode here");
    sessions = [];   // deleted between the listing and the click
    assert.equal(await downloadPath("/sessions/run&a#b/export", "x.db", "session export"),
                 "session export failed: no such session: run&a#b");
  });
