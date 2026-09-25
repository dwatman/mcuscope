// state.js preflight: a session `.db` export is checked with the export path itself plus
// `check=1&wait=1`, which the daemon answers with the refusal the navigation would get, without
// building the copy (SPEC 3.4). The reference stays as the path carries it: a separate query
// parameter built from it (the old `/sessions?name=`) had to be encoded, or `&` and `#` in a
// name asked about another session.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
const fetches = [];
let answer = { ok: true, status: 200, body: { ok: true } };

globalThis.fetch = async (url) => {
  fetches.push(String(url));
  return { ok: answer.ok, status: answer.status, headers: { get: () => null },
           json: async () => answer.body };
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

test("the check asks the export path itself, and the download follows it", async () => {
  fetches.length = 0;
  answer = { ok: true, status: 200, body: { ok: true } };
  const a = anchors();
  const path = "/sessions/run%26a%23b/export";
  assert.equal(await downloadPath(path, "x.db", "session export"), null);
  a.restore();
  assert.deepEqual(fetches, [path + "?check=1&wait=1"], "one check, of the session the download names");
  assert.equal(a.created.at(-1).href, path + "?wait=1", "the download itself still goes to the export path");
});

test("a session that is gone, or a full queue, is reported in the daemon's words and not navigated",
  async () => {
    for (const [status, error] of [[400, "no such session: 2"],
                                   [503, "too many session exports waiting for a slot; try again shortly"]]) {
      fetches.length = 0;
      answer = { ok: false, status, body: { error } };
      const a = anchors();
      assert.equal(await downloadPath("/sessions/2/export", "x.db", "session export"),
                   `session export failed: ${error}`);
      a.restore();
      assert.equal(a.created.length, 0, `${status}: navigated anyway`);
      assert.deepEqual(fetches, ["/sessions/2/export?check=1&wait=1"]);
    }
  });
