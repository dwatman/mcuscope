// statusbar.js: the formatters and the update badge, driven through a stubbed /status.
//
// The chip row is what tells a user the daemon is alive, so "healthy while dead" (REVIEW
// registry class 12) lives here: an unreachable daemon must say so rather than keep showing
// the last good reading.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

let status = {};
let fail = false;
let hold = null;      // set to a promise to keep a poll in flight
let fetchCalls = 0;
let lastOpt = null;
let posted = [];      // [path, method] of every non-GET request
let devices = [];     // GET /devices answer
const BY_ID = "/dev/serial/by-id/usb-STMicroelectronics_STLINK-V3PWR_0031-if01";
globalThis.fetch = async (path, opt) => {
  fetchCalls += 1;
  lastOpt = opt;
  if (opt && opt.method && opt.method !== "GET") posted.push([path, opt.method, opt.body]);
  if (fail) throw new Error("connection refused");
  if (hold) await hold;
  if (String(path).endsWith("/devices")) return { ok: true, status: 200, json: async () => ({ devices }) };
  return { ok: true, status: 200, json: async () => status };
};

const SB = await import(webuiUrl("statusbar.js"));
const { fmtBytes, refreshStatus, tickUptime, flashDaemonError, initStatusbar } = SB;

const text = (id) => env.byId(id).textContent;

function baseStatus(over = {}) {
  return { version: "0.1.0", uptime_s: 0, db_size_bytes: 0, ports: [], write_errors: 0,
           session: null, ...over };
}

test("fmtBytes scales and rounds the way the settings dialog labels a cap", () => {
  assert.equal(fmtBytes(0), "0 B");
  assert.equal(fmtBytes(1023), "1023 B");
  assert.equal(fmtBytes(1024), "1.0 kB");
  assert.equal(fmtBytes(1536), "1.5 kB");
  assert.equal(fmtBytes(10 * 1024), "10 kB", "past 10 the fraction is dropped");
  assert.equal(fmtBytes(1024 ** 2), "1.0 MB");
  assert.equal(fmtBytes(1024 ** 3), "1.0 GB");
  assert.equal(fmtBytes(1024 ** 4), "1.0 TB");
  assert.equal(fmtBytes(1024 ** 5), "1024 TB", "TB is the last unit, so it keeps growing");
  assert.equal(fmtBytes(-1), "", "a nonsense size renders as nothing, not as '-1 B'");
  assert.equal(fmtBytes(NaN), "");
  assert.equal(fmtBytes(Infinity), "");
  assert.equal(fmtBytes(undefined), "");
});

test("fmtUptime steps through its units", async () => {
  const shown = async (uptime) => {
    status = baseStatus({ uptime_s: uptime });
    await refreshStatus();
    tickUptime();
    return text("daemonUptime");
  };
  assert.equal(await shown(0), "up 0s");
  assert.equal(await shown(9.7), "up 9s", "seconds floor, they do not round up");
  assert.equal(await shown(59), "up 59s");
  assert.equal(await shown(60), "up 1m0s");
  assert.equal(await shown(3599), "up 59m59s");
  assert.equal(await shown(3600), "up 1h0m");
  assert.equal(await shown(86399), "up 23h59m");
  assert.equal(await shown(86400), "up 1d0h");
  assert.equal(await shown(90061), "up 1d1h");
  assert.equal(await shown(-5), "up 0s", "a clock that went backwards must not read negative");
});

test("the version, host and db size render from /status", async () => {
  status = baseStatus({ version: "1.2.3", db_size_bytes: 5 * 1024 * 1024 });
  await refreshStatus();
  assert.equal(text("daemonVer"), "mcuscoped 1.2.3");
  assert.equal(text("daemonHost"), "127.0.0.1:8558");
  assert.equal(text("daemonDb"), "db 5.0 MB");

  status = baseStatus({ db_size_bytes: 5 * 1024 * 1024, db_max_bytes: 100 * 1024 * 1024,
                        lines_trimmed: 12 });
  await refreshStatus();
  assert.equal(text("daemonDb"), "db 5.0 MB / 100 MB");
  assert.equal(env.byId("daemonDb").classList.contains("drop"), true);
  assert.match(env.byId("daemonDb").title, /12 of the oldest lines/);
});

test("an unreachable daemon says so instead of holding the last good reading", async () => {
  status = baseStatus({ version: "1.2.3", uptime_s: 500, db_size_bytes: 5 * 1024 * 1024,
                        ports: [{ alias: "mcu0", device: "/dev/ttyACM0", baud: 115200,
                                  connected: true }] });
  await refreshStatus();
  assert.equal(text("daemonVer"), "mcuscoped 1.2.3");
  assert.equal(text("daemonDb"), "db 5.0 MB");
  assert.equal(env.byId("ports").children.length, 1);

  fail = true;
  await refreshStatus();
  fail = false;
  assert.equal(text("daemonVer"), "daemon unreachable");
  assert.equal(text("daemonUptime"), "");
  assert.equal(env.byId("daemonDot").className, "dot crit");
  tickUptime();
  assert.equal(text("daemonUptime"), "", "a dead daemon's clock must not keep ticking");
  // The per-port health surface, which this test is named for and used to skip entirely: a
  // green "connected" chip beside "daemon unreachable" is the class 12 shape.
  assert.equal(env.byId("ports").children.length, 0,
    "the port chips held their last good reading while the daemon was unreachable");
  assert.equal(text("daemonDb"), "",
    "the db size is as unknown as the rest of /status, not 5.0 MB");
});

test("a port chip shows alias, short port name and drops; description, by-id and baud are the hover", async () => {
  status = baseStatus({
    ports: [{ alias: "mcu0", device: BY_ID, resolved_device: "/dev/ttyACM0", description: "STLINK-V3PWR",
              baud: 115200, connected: true, rx_dropped: 3 },
            { alias: "mcu1", device: "COM3", resolved_device: "COM3", description: null,
              baud: 9600, connected: false },
            { alias: "mcu2", device: BY_ID, resolved_device: null, description: null,
              baud: 9600, connected: false }],
  });
  await refreshStatus();
  const chips = env.byId("ports").children;
  assert.equal(chips.length, 3);
  assert.equal(chips[0].textContent, "mcu0/dev/ttyACM03 dropped×",
    "the by-id path pushed the header buttons onto a second line; the chip shows the port it landed on");
  assert.equal(chips[0].title, `STLINK-V3PWR\n${BY_ID}\n@115200`);
  assert.equal(chips[0].className, "chip");
  assert.equal(chips[0].children[0].className, "dot");
  assert.equal(chips[1].textContent, "mcu1COM3↻×", "a detached port offers a reconnect");
  assert.equal(chips[1].title, "@9600", "a name equal to the device string is not repeated");
  assert.equal(chips[1].className, "chip disc");
  assert.equal(chips[1].children[0].className, "dot off");
  assert.equal(chips[2].textContent, "mcu2↻×", "never connected: no port name to show");
  assert.equal(chips[2].title, `waiting for ${BY_ID}\n@9600`);
});

test("the attach dialog attaches the port as picked; the bind box swaps in the by-id path", async () => {
  devices = [{ device: "/dev/ttyACM0", by_id: BY_ID, description: "STLINK-V3PWR" },
             { device: "COM3", by_id: null, description: "" }];
  initStatusbar();
  env.byId("attachBtn").emit("click", {});
  await tick(0);
  const sel = env.byId("devSel");
  assert.deepEqual(sel.children.slice(0, 2).map((o) => o.value), ["/dev/ttyACM0", "COM3"],
    "option values are the port names, not silently the by-id paths");
  sel.value = "/dev/ttyACM0";
  sel.emit("change", {});
  assert.equal(env.byId("bindRow").style.display, "", "a device with a by-id path offers binding");
  env.byId("aliasInput").value = "mcu0";
  env.byId("baudSel").value = "115200";   // the stub <select> has no default selection
  posted = [];
  env.byId("dlgAttach").emit("click", {});
  await tick(0);
  assert.deepEqual(posted[0].slice(0, 2), ["/ports", "POST"], env.byId("dlgErr").textContent);
  assert.equal(JSON.parse(posted[0][2]).device, "/dev/ttyACM0", "unticked: the port name as picked");

  env.byId("attachBtn").emit("click", {});
  await tick(0);
  assert.equal(env.byId("bindById").checked, false, "the box does not remember the last attach");
  sel.value = "/dev/ttyACM0";
  env.byId("baudSel").value = "115200";
  env.byId("bindById").checked = true;
  env.byId("aliasInput").value = "mcu0";
  posted = [];
  env.byId("dlgAttach").emit("click", {});
  await tick(0);
  assert.equal(JSON.parse(posted[0][2]).device, BY_ID, "ticked: the by-id path");

  env.byId("attachBtn").emit("click", {});
  await tick(0);
  sel.value = "COM3";
  sel.emit("change", {});
  env.byId("baudSel").value = "115200";
  assert.equal(env.byId("bindRow").style.display, "none", "no by-id path (Windows): no box");
  env.byId("bindById").checked = true;   // stale tick from an earlier open cannot leak
  env.byId("aliasInput").value = "mcu1";
  posted = [];
  env.byId("dlgAttach").emit("click", {});
  await tick(0);
  assert.equal(JSON.parse(posted[0][2]).device, "COM3");
});

test("the dot is the connect switch: green disconnects (held, red), red reconnects", async () => {
  status = baseStatus({
    ports: [{ alias: "mcu0", device: "COM3", baud: 9600, connected: true }],
  });
  await refreshStatus();
  let dot = env.byId("ports").children[0].children[0];
  assert.equal(dot.tagName, "BUTTON");
  assert.match(dot.title, /^Disconnect mcu0/);
  posted = [];
  dot.click();
  await refreshStatus();
  assert.deepEqual(posted[0].slice(0, 2), ["/ports/mcu0/disconnect", "POST"]);

  status = baseStatus({
    ports: [{ alias: "mcu0", device: "COM3", baud: 9600, connected: false, held: true }],
  });
  await refreshStatus();
  const chip = env.byId("ports").children[0];
  dot = chip.children[0];
  assert.equal(dot.className, "dot crit", "held is red, not the grey of a lost device");
  assert.equal(chip.className, "chip disc");
  assert.equal(chip.textContent, "mcu0×",
    "held: no ↻ button, the dot is the reconnect control");
  assert.match(dot.title, /^Reconnect mcu0/);
  posted = [];
  dot.click();
  await refreshStatus();
  assert.deepEqual(posted[0].slice(0, 2), ["/ports/mcu0/reconnect", "POST"]);
});

test("a store-wide write error is surfaced on every port chip", async () => {
  // write_errors is store-wide (/status.write_errors), not per port, but a connected port
  // whose lines are not reaching the database is not healthy and a green dot said it was.
  status = baseStatus({
    ports: [{ alias: "mcu0", device: "/dev/ttyACM0", baud: 115200, connected: true }],
    write_errors: 2,
  });
  await refreshStatus();
  const chip = env.byId("ports").children[0];
  assert.match(chip.textContent, /2 write errors/);
  assert.equal(chip.children[0].className, "dot crit");

  status.write_errors = 1;
  await refreshStatus();
  assert.match(env.byId("ports").children[0].textContent, /1 write error(?!s)/);
});

test("a dead store writer is a broken state on every port chip", async () => {
  // /status carries writer_alive, but nothing a human looks at showed it: the chip stayed
  // green and rx kept climbing while not one line was being stored (REVIEW class 12).
  status = baseStatus({
    ports: [{ alias: "mcu0", device: "/dev/ttyACM0", baud: 115200, connected: true }],
    writer_alive: false,
  });
  await refreshStatus();
  let chip = env.byId("ports").children[0];
  assert.match(chip.textContent, /capture stopped/);
  assert.equal(chip.children[0].className, "dot crit");

  status.writer_alive = true;
  await refreshStatus();
  chip = env.byId("ports").children[0];
  assert.doesNotMatch(chip.textContent, /capture stopped/, "the chip must repaint when it recovers");
  assert.equal(chip.children[0].className, "dot");
});

test("the session chip distinguishes the daemon's automatic run from a named one", async () => {
  status = baseStatus({ session: { id: 1, name: "auto-2026", auto: true } });
  await refreshStatus();
  assert.equal(text("sessionBtn"), "● session");
  assert.match(env.byId("sessionBtn").title, /automatic run/);

  status = baseStatus({ session: { id: 2, name: "run-a", auto: false } });
  await refreshStatus();
  assert.equal(text("sessionBtn"), "■ run-a");
  assert.equal(env.byId("sessionBtn").classList.contains("primary"), true);
});

test("the update badge only shows a real release, and only over http(s)", async () => {
  status = baseStatus({ update: { available: false } });
  await refreshStatus();
  assert.equal(env.byId("updateBadge").hidden, true);

  status = baseStatus({ update: { available: true, latest: "9.9.9",
                                  url: "https://pypi.org/project/mcuscope/" } });
  await refreshStatus();
  assert.equal(env.byId("updateBadge").hidden, false);
  assert.equal(text("updateLink"), "update: 9.9.9");
  assert.equal(env.byId("updateLink").href, "https://pypi.org/project/mcuscope/");

  // Validate at the sink: this field names PyPI, but a "javascript:" value would execute
  // on click, and the guarantee must not live only in the daemon.
  for (const bad of ["javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,x", "", null]) {
    status = baseStatus({ update: { available: true, latest: "9.9.9", url: bad } });
    await refreshStatus();
    assert.equal(env.byId("updateLink").href, "https://pypi.org/project/mcuscope/",
      `a ${JSON.stringify(bad)} href reached the DOM`);
  }
});

test("dismissing the badge hides that version, and only that version", async () => {
  initStatusbar();
  env.store.clear();
  status = baseStatus({ update: { available: true, latest: "9.9.9" } });
  await refreshStatus();
  assert.equal(env.byId("updateBadge").hidden, false);

  env.byId("updateDismiss").emit("click");
  assert.equal(env.store.get("mcuscope.updateDismissed"), "9.9.9");
  assert.equal(env.byId("updateBadge").hidden, true);
  await refreshStatus();
  assert.equal(env.byId("updateBadge").hidden, true, "a dismissed badge must stay hidden");

  // A newer release is different news: it shows again, so one dismissal can never
  // silence the next release. This is what replaced the day/week/month snooze ladder.
  status = baseStatus({ update: { available: true, latest: "9.9.10" } });
  await refreshStatus();
  assert.equal(env.byId("updateBadge").hidden, false);
  env.store.clear();
});

test("unusable dismissal storage must not hide news", async () => {
  status = baseStatus({ update: { available: true, latest: "9.9.9" } });
  // A stored value naming some other version says nothing about this one, and a render
  // fault here used to paint "daemon unreachable" over a daemon that had just answered.
  for (const stored of ["9.9.8", "", "{not json", "null"]) {
    env.store.set("mcuscope.updateDismissed", stored);
    await refreshStatus();
    assert.equal(text("daemonVer"), "mcuscoped 0.1.0", `${stored} broke the bar`);
    assert.equal(env.byId("updateBadge").hidden, false, `${stored} hid the badge`);
  }
  env.store.clear();
});

test("a render fault is not an unreachable daemon", async () => {
  // The catch around the fetch also covered every render call, so one bad element or one
  // corrupt localStorage record painted "daemon unreachable" over a daemon that had just
  // answered - on every 5 s poll, for as long as the fault lasted (REVIEW class 12 inverted).
  status = baseStatus({ version: "1.2.3", ports: [] });
  await refreshStatus();
  assert.equal(text("daemonVer"), "mcuscoped 1.2.3");

  status = baseStatus({ version: "1.2.3", ports: 5 });   // renderPorts throws on ports.map
  await refreshStatus();
  assert.equal(text("daemonVer"), "mcuscoped 1.2.3",
    "a throw out of the rendering was reported as a dead daemon");
  assert.equal(env.byId("daemonDot").className, "dot ", "the health dot went critical on a bug");

  fail = true;                                            // ... and a real failure still says so
  await refreshStatus();
  fail = false;
  assert.equal(text("daemonVer"), "daemon unreachable");
});

test("the /status poll is one at a time, and carries a deadline", async () => {
  // A daemon that accepts the connection and then stalls answers nothing and closes nothing:
  // the 5 s interval piled overlapping fetches up for as long as it lasted.
  status = baseStatus({ version: "1.2.3" });
  await refreshStatus();
  let release;
  hold = new Promise((r) => { release = r; });
  fetchCalls = 0;

  const first = refreshStatus();
  const second = refreshStatus();
  assert.equal(fetchCalls, 1, "a second poll started while the first was still in flight");
  assert.equal(first, second, "concurrent callers must share the poll in flight");
  assert.ok(lastOpt && lastOpt.signal, "the poll carries no AbortSignal, so a stall never ends");
  assert.equal(lastOpt.signal.aborted, false, "the deadline must not fire on a prompt answer");

  release();
  hold = null;
  await first;
  await refreshStatus();
  assert.equal(fetchCalls, 2, "the guard was never cleared: no poll can run again");
});

test("flashDaemonError puts the reason on the chip", () => {
  flashDaemonError("detach mcu0 failed: no such port");
  const el = env.byId("daemon");
  assert.equal(el.classList.contains("flash-err"), true);
  assert.equal(el.title, "detach mcu0 failed: no such port");
});

// The attach dialog's baud carried its lower bound only, so a value above the daemon's
// MAX_BAUD reached POST /ports and came back as a raw 422 after a round trip. Same omission
// as settings.js collectPorts had, and the same treatment.
test("the attach dialog refuses a baud above the daemon's bound", async () => {
  initStatusbar();
  env.byId("attachBtn").emit("click", {});
  env.byId("aliasInput").value = "mcu0";
  env.byId("devSel").value = "custom";
  env.byId("devCustom").value = "socket://127.0.0.1:9900";
  env.byId("baudSel").value = "custom";
  env.byId("baudCustom").value = "200000000";   // MAX_BAUD is 100000000
  const before = fetchCalls;
  env.byId("dlgAttach").emit("click", {});
  await Promise.resolve();
  assert.equal(fetchCalls, before, "no POST /ports for a value the daemon answers 422 for");
  assert.match(env.byId("dlgErr").textContent, /1-100000000/,
    "the refusal names the bound, in the dialog's own wording");
});
