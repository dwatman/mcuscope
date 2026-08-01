// state.js: the shared primitives every other module leans on.
//
// lineTick sets the sticky global anchor (state.anchorTick) that every terminal timestamp
// and every chart x axis is measured from, so a single out-of-range tick on one corrupt
// line used to shift the whole session until "clear all". pushBuffer, nearestX and the
// token/Content-Disposition helpers are the rest of the module's pure surface.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();

let fetchImpl = async () => { throw new Error("no fetch stub installed"); };
globalThis.fetch = (...a) => fetchImpl(...a);

const S = await import(webuiUrl("state.js"));
const { state, buffer, hooks, lineTick, pushBuffer, nearestX, portColor, rgbToHex,
        colorFor, saveColor, api, downloadPath, getToken, setToken, promptForToken,
        resetTokenPrompt, intField, BUFFER_MAX } = S;

const row = (over) => ({ id: 1, ts: 100, port: "p1", chan: "debug", raw: "hello", ...over });

// A !ps tick only counts once its stream has been declared by a !pd, exactly as plots.js and
// the daemon require. plots.js publishes the real answer through this hook; this file drives
// state.js alone, so it stands in for sid 0 on port p1 (state_plot_tick.test.mjs drives the
// two modules together and is what proves they agree).
hooks.hasPlotDef = (port, sid) => port === "p1" && sid === "0";

test("lineTick reads the tick out of the lines that carry one", () => {
  assert.equal(lineTick(row({ chan: "event", raw: "!can 1234 - 100 DEADBEEF" })), 1234);
  assert.equal(lineTick(row({ chan: "event", raw: "!p 4321 v=1" })), 4321);
  assert.equal(lineTick(row({ chan: "event", raw: "!ps 0 3E8 0064" })), 1000);   // hex tick
  assert.equal(lineTick(row({ chan: "marker", raw: "!m @77 boot done" })), 77);
});

test("lineTick returns null where there is no tick to read", () => {
  assert.equal(lineTick(row({ chan: "debug", raw: "!can 1234 - 100 -" })), null, "debug rows carry none");
  assert.equal(lineTick(row({ chan: "marker", raw: "!m host-side mark" })), null);
  assert.equal(lineTick(row({ chan: "marker", raw: "!m @12" })), null, "a tick with no text is not a marker");
  assert.equal(lineTick(row({ chan: "event", raw: "!can x - 100 -" })), null);
  assert.equal(lineTick(row({ chan: "event", raw: "!ps 0 zz 0064" })), null);
  assert.equal(lineTick(row({ chan: "event", raw: "!other 12" })), null);
  assert.equal(lineTick(row({ chan: "event", raw: "!ps 0 ABCD" })), null,
    "three tokens: plots.js and the daemon both keep this as a plain event");
  assert.equal(lineTick(row({ chan: "event", raw: "!ps 0 3E8 0064 extra" })), null);
  assert.equal(lineTick(row({ chan: "event", raw: "!ps 9 3E8 0064" })), null,
    "an undeclared stream has no tick to take");
});

test("a tick outside the SPEC 2.5 32-bit range is refused", () => {
  assert.equal(lineTick(row({ chan: "event", raw: "!can 99999999999999999999 - 100 -" })), null,
    "one corrupt line must not shift every timestamp and x axis for the session");
  assert.equal(lineTick(row({ chan: "event", raw: "!can 4294967296 - 100 -" })), null);
  assert.equal(lineTick(row({ chan: "event", raw: "!can 4294967295 - 100 -" })), 0xFFFFFFFF);
  assert.equal(lineTick(row({ chan: "event", raw: "!ps 0 100000000 0064" })), null);
  assert.equal(lineTick(row({ chan: "marker", raw: "!m @4294967296 late" })), null);
});

test("pushBuffer anchors relative time and tick on the first row that has one", () => {
  buffer.length = 0;
  state.anchorTs = null; state.anchorTick = null; state.maxId = 0;
  pushBuffer(row({ id: 5, ts: 42.5 }));
  assert.equal(state.anchorTs, 42.5);
  assert.equal(state.anchorTick, null, "a debug line carries no tick to anchor on");
  assert.equal(state.maxId, 5);
  pushBuffer(row({ id: 6, ts: 43, chan: "event", raw: "!can 900 - 100 -" }));
  assert.equal(state.anchorTick, 900);
  pushBuffer(row({ id: 7, ts: 44, chan: "event", raw: "!can 950 - 100 -" }));
  assert.equal(state.anchorTick, 900, "the anchor is sticky once set");
  assert.equal(state.maxId, 7);
  pushBuffer(row({ id: 3, ts: 45 }));
  assert.equal(state.maxId, 7, "maxId is a watermark, not the last id seen");
});

test("pushBuffer trims the shared buffer in blocks", () => {
  buffer.length = 0;
  for (let i = 1; i <= 6000; i++) pushBuffer(row({ id: i, ts: i }));
  assert.ok(buffer.length >= BUFFER_MAX, `buffer trimmed below the cap: ${buffer.length}`);
  assert.ok(buffer.length <= BUFFER_MAX + 512, `buffer overshot its slack: ${buffer.length}`);
  assert.equal(buffer.at(-1).id, 6000, "the live edge must survive the trim");
  assert.ok(buffer[0].id > 1, "the oldest rows must be evicted");
});

test("nearestX snaps to an actual sample", () => {
  const xs = [0, 1, 2, 10];
  assert.equal(nearestX(xs, -5), 0, "clamps left");
  assert.equal(nearestX(xs, 99), 10, "clamps right");
  assert.equal(nearestX(xs, 1), 1);
  assert.equal(nearestX(xs, 1.4), 1);
  assert.equal(nearestX(xs, 1.6), 2);
  assert.equal(nearestX(xs, 6), 2, "ties and midpoints resolve to the nearer sample");
  assert.equal(nearestX(xs, 6.1), 10);
  assert.equal(nearestX([], 1), null);
  assert.equal(nearestX(null, 1), null);
  assert.equal(nearestX([7], 1), 7);
});

test("portColor is stable per alias and rgbToHex is picker-safe", () => {
  const a = portColor("mcu0");
  assert.equal(portColor("mcu0"), a, "the same alias must keep its colour");
  assert.match(a, /^#[0-9a-f]{6}$/i);
  assert.match(portColor(""), /^#[0-9a-f]{6}$/i);
  assert.match(portColor(undefined), /^#[0-9a-f]{6}$/i);
  assert.equal(rgbToHex("#abcdef"), "#abcdef");
  assert.equal(rgbToHex("#abcdefff"), "#abcdef", "<input type=color> rejects an alpha suffix");
  assert.equal(rgbToHex("rgb(1,2,3)"), "#46c8d8", "a non-hex colour falls back to the default");
  assert.equal(rgbToHex(""), "#46c8d8");
  assert.equal(rgbToHex(null), "#46c8d8");
});

test("a saved colour overrides the palette slot", () => {
  const stock = colorFor("chanA", 0);
  saveColor("chanA", "#123456");
  assert.equal(colorFor("chanA", 0), "#123456");
  assert.equal(colorFor("chanB", 0), stock, "another channel keeps the palette slot");
  assert.equal(JSON.parse(env.store.get("mcuscope.colors")).chanA, "#123456");
});

test("filenameFromDisposition prefers the RFC 6266 form and falls back cleanly", async () => {
  const created = [];
  const origCreate = env.document.createElement;
  env.document.createElement = (t) => {
    const el = origCreate(t);
    if (String(t).toLowerCase() === "a") created.push(el);
    return el;
  };
  const download = async (disposition) => {
    fetchImpl = async () => ({
      ok: true, status: 200,
      headers: { get: () => disposition },
      blob: async () => new Blob(["x"]),
    });
    await downloadPath("/plot/export", "fallback.csv", "csv export");
    return created.at(-1).download;
  };

  assert.equal(await download('attachment; filename="plot.csv"'), "plot.csv");
  assert.equal(await download("attachment; filename=plot.csv"), "plot.csv");
  assert.equal(await download("attachment; filename*=UTF-8''pl%C3%B6t.csv"), "plöt.csv");
  assert.equal(await download("attachment; filename*=UTF-8''%E4%B8%AD.csv"), "中.csv");
  assert.equal(await download("attachment"), "fallback.csv");
  assert.equal(await download(null), "fallback.csv");
  assert.equal(await download("attachment; filename*=UTF-8''%zz"), "fallback.csv",
    "an undecodable percent-escape must not throw out of the download");
  env.document.createElement = origCreate;
});

test("a failed download is reported rather than swallowed", async () => {
  const errs = [];
  hooks.reportError = (m) => errs.push(m);
  fetchImpl = async () => ({
    ok: false, status: 400,
    json: async () => ({ error: "names is required" }),
    headers: { get: () => null },
  });
  await downloadPath("/plot/export", "plot.csv", "csv export");
  assert.deepEqual(errs, ["csv export failed: names is required"]);
});

test("a token is carried, persisted and re-prompted within its budget", async () => {
  resetTokenPrompt();
  setToken(null);
  assert.equal(getToken(), null);
  setToken("secret");
  assert.equal(getToken(), "secret");
  assert.equal(env.store.get("mcuscope.token"), "secret");

  const seen = [];
  fetchImpl = async (path, opt) => {
    seen.push((opt.headers || {}).Authorization);
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  await api("GET", "/status");
  assert.deepEqual(seen, ["Bearer secret"]);

  setToken(null);
  assert.equal(env.store.get("mcuscope.token"), undefined,
    "clearing the token must remove it from storage");
  assert.equal(getToken(), null);
});

test("promptForToken short-circuits when another path already supplied a token", () => {
  resetTokenPrompt();
  setToken("fresh");
  let prompts = 0;
  globalThis.prompt = () => { prompts += 1; return "typed"; };
  assert.equal(promptForToken(null), "fresh",
    "a concurrent 401 and WS 1008 for the same missing token must prompt once, not twice");
  assert.equal(prompts, 0);
});

test("promptForToken gives up once, and says so once", () => {
  resetTokenPrompt();
  setToken(null);
  let failed = 0;
  hooks.authFailed = () => { failed += 1; };
  globalThis.prompt = () => null;                      // the user cancels
  assert.equal(promptForToken(null), null);
  assert.equal(failed, 1);
  assert.equal(promptForToken(null), null, "a cancelled prompt does not come back");
  assert.equal(failed, 1, "authFailed must fire exactly once");

  resetTokenPrompt();
  let n = 0;
  globalThis.prompt = () => "wrong" + (++n);           // always a wrong token
  for (let i = 0; i < 3; i++) promptForToken(getToken());
  assert.equal(n, 3, "the prompt budget is 3 (initial plus 2 retries)");
  assert.equal(promptForToken(getToken()), null, "past the budget the prompt stops");
  assert.equal(n, 3);
  assert.equal(failed, 2);
});

test("authFetch retries a 401 with the newly entered token", async () => {
  resetTokenPrompt();
  setToken(null);
  globalThis.prompt = () => "good-token";
  const seen = [];
  fetchImpl = async (path, opt) => {
    seen.push((opt.headers || {}).Authorization);
    if (seen.length === 1) return { ok: false, status: 401, json: async () => ({ error: "unauthorized" }) };
    return { ok: true, status: 200, json: async () => ({ version: "1.2.3" }) };
  };
  const body = await api("GET", "/status");
  assert.deepEqual(body, { version: "1.2.3" });
  assert.deepEqual(seen, [undefined, "Bearer good-token"]);
});

test("api surfaces the daemon's error envelope", async () => {
  setToken(null);
  fetchImpl = async () => ({ ok: false, status: 400, json: async () => ({ error: "bad regex" }) });
  await assert.rejects(() => api("GET", "/lines?match=("), /bad regex/);
  fetchImpl = async () => ({ ok: false, status: 500, json: async () => { throw new Error("not json"); } });
  await assert.rejects(() => api("GET", "/status"), /HTTP 500/);
});

test("intField refuses what parseInt would silently truncate", () => {
  // parseInt takes the leading digits and stops, which is the wrong grammar for a bounded
  // field: "1e9" reads as 1, so `1e9` typed into the settings port box passed the 1-65535
  // check and saved port 1. <input type=number> accepts exponent notation, so this needs
  // nothing unusual pasted in.
  assert.equal(parseInt("1e9", 10), 1, "the behaviour being guarded against");
  assert.equal(intField("1e9"), 1e9);
  assert.ok(Number.isNaN(intField("12abc")));
  assert.ok(Number.isNaN(intField("9.9")), "a count field must not take a fraction");
  // Empty stays NaN rather than Number("") === 0, or a blank size cap would read as
  // "unlimited" instead of being rejected as missing.
  assert.ok(Number.isNaN(intField("")));
  assert.ok(Number.isNaN(intField("   ")));
  assert.ok(Number.isNaN(intField(null)));
  // And the ordinary values still land.
  assert.equal(intField(" 8765 "), 8765);
  assert.equal(intField("0"), 0);
  assert.equal(intField("-1"), -1);
});
