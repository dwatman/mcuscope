// statusbar.js: the formatters and the update badge, driven through a stubbed /status.
//
// The chip row is what tells a user the daemon is alive, so "healthy while dead" (REVIEW
// registry class 12) lives here: an unreachable daemon must say so rather than keep showing
// the last good reading.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();

let status = {};
let fail = false;
globalThis.fetch = async () => {
  if (fail) throw new Error("connection refused");
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
  assert.equal(text("daemonHost"), "127.0.0.1:8765");
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

test("a port chip carries its alias, device, baud and drop count", async () => {
  status = baseStatus({
    ports: [{ alias: "mcu0", device: "/dev/ttyACM0", baud: 115200, connected: true, rx_dropped: 3 },
            { alias: "mcu1", device: "COM3", baud: 9600, connected: false }],
  });
  await refreshStatus();
  const chips = env.byId("ports").children;
  assert.equal(chips.length, 2);
  assert.equal(chips[0].textContent, "mcu0/dev/ttyACM0 @1152003 dropped×");
  assert.equal(chips[0].className, "chip");
  assert.equal(chips[0].children[0].className, "dot");
  assert.equal(chips[1].textContent, "mcu1COM3 @9600↻×", "a detached port offers a reconnect");
  assert.equal(chips[1].className, "chip disc");
  assert.equal(chips[1].children[0].className, "dot off");
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

test("dismissing the badge walks the snooze ladder", async () => {
  initStatusbar();
  env.store.clear();
  status = baseStatus({ update: { available: true, latest: "9.9.9" } });
  await refreshStatus();
  assert.equal(env.byId("updateBadge").hidden, false);

  const snooze = () => JSON.parse(env.store.get("mcuscope.updateSnooze"));
  const DAY = 86400e3;
  const rungs = [DAY, 7 * DAY, 30 * DAY];
  for (let step = 0; step < rungs.length; step++) {
    env.byId("updateDismiss").emit("click");
    const st = snooze();
    assert.equal(st.version, "9.9.9");
    assert.equal(st.step, step);
    assert.ok(Math.abs(st.until - (Date.now() + rungs[step])) < 5000, `rung ${step} is wrong`);
    assert.equal(env.byId("updateBadge").hidden, true);
    await refreshStatus();
    assert.equal(env.byId("updateBadge").hidden, true, "a snoozed badge must stay hidden");
  }
  env.byId("updateDismiss").emit("click");
  assert.equal(snooze().until, null, "the last rung hides it for good");
  await refreshStatus();
  assert.equal(env.byId("updateBadge").hidden, true);

  // ... but a newer release is different news, and starts the ladder over.
  status = baseStatus({ update: { available: true, latest: "9.9.10" } });
  await refreshStatus();
  assert.equal(env.byId("updateBadge").hidden, false);
});

test("an expired snooze lets the badge back", async () => {
  env.store.set("mcuscope.updateSnooze",
    JSON.stringify({ version: "9.9.9", step: 0, until: Date.now() - 1000 }));
  status = baseStatus({ update: { available: true, latest: "9.9.9" } });
  await refreshStatus();
  assert.equal(env.byId("updateBadge").hidden, false);

  env.store.set("mcuscope.updateSnooze", "{not json");
  await refreshStatus();
  assert.equal(env.byId("updateBadge").hidden, false, "unreadable storage must not hide news");
});

test("flashDaemonError puts the reason on the chip", () => {
  flashDaemonError("detach mcu0 failed: no such port");
  const el = env.byId("daemon");
  assert.equal(el.classList.contains("flash-err"), true);
  assert.equal(el.title, "detach mcu0 failed: no such port");
});
