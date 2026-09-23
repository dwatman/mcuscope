"""`mcu daemon start` waits out the one-time index build of an older capture.

The daemon answers only once `Store.start()` has built the indexes the capture lacks, which
can outlast `--timeout`; stopping it then only restarts the build on the next start. The
clock is the test's, advanced only by the readiness loop's own sleep.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from mcuscope import cli, cli_daemonctl, store
from mcuscope.cli_client import Settings

URL = "http://127.0.0.1:1"
BUILD = ("capture /x/cap.db: building index idx_lines_port_id, idx_plot_line once "
         "(about 2.5 s per million lines)\n")
BUILT = "capture /x/cap.db: built index idx_lines_port_id, idx_plot_line in 41.0 s\n"
BODY = {"version": "9.9.9", "uptime_s": 0, "ports": [], "pid": 4242}


def test_the_cli_copies_of_the_notices_match_the_store() -> None:
    assert cli_daemonctl.INDEX_BUILD_NOTICE == store.INDEX_BUILD_NOTICE
    assert cli_daemonctl.INDEX_BUILT_NOTICE == store.INDEX_BUILT_NOTICE


class _Proc:
    def __init__(self) -> None:
        self.pid, self.terminated = 4242, False

    def poll(self):
        return -15 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None):
        return self.poll()

    def kill(self) -> None:
        self.terminated = True


@pytest.fixture
def start(tmp_path, monkeypatch):
    """Run `daemon start --timeout 5` whose child writes `first` to its stderr file, and
    whose readiness probe calls `probe(n, clock)` (None, or a /status body)."""
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path))
    err_path = cli_daemonctl._stderr_log_path(
        cli_daemonctl._pid_file(Settings(url=URL, json_out=False, port=None)))
    clock = [100.0]
    proc = _Proc()

    def run(first: str, probe, *extra: str) -> int:
        probes = [0]

        def spawn(args, **kw):
            os.write(kw["stderr"].fileno(), first.encode())
            return proc

        def status(s, timeout=2.0):
            probes[0] += 1
            return probe(probes[0], clock[0] - 100.0), None

        monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(cli.time, "sleep", lambda sec: clock.__setitem__(0, clock[0] + sec))
        monkeypatch.setattr(cli, "_status_body", lambda s, timeout=2.0: None)
        monkeypatch.setattr(cli, "_status_or_refusal", status)
        monkeypatch.setattr(subprocess, "Popen", spawn)
        return cli.main([*extra, "--url", URL, "daemon", "start", "--timeout", "5"])

    def append(text: str) -> None:
        with open(err_path, "a", encoding="utf-8", newline="") as fh:
            fh.write(text)

    run.proc, run.append, run.clock = proc, append, clock
    return run


def test_a_start_past_its_timeout_during_an_index_build_waits_for_it(start, capsys) -> None:
    # Another warning follows the notice: the build is judged on the lines, not the last.
    rc = start(BUILD + "journal mode is delete\n",
               lambda n, t: BODY if t > 60 else None, "--json")
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert not start.proc.terminated
    assert err.count("mcuscoped is building index idx_lines_port_id, idx_plot_line on an "
                     "older capture (one time); waiting. Ctrl-C leaves it building (pid 4242)"
                     ) == 1, err
    assert json.loads(out)["pid"] == 4242   # the notice is stderr only


def test_a_start_past_its_timeout_with_no_build_is_stopped(start, capsys) -> None:
    """Positive control for the wait: the same silence without the notice fails at 5 s."""
    rc = start("journal mode is delete\n", lambda n, t: BODY if t > 60 else None)
    err = capsys.readouterr().err
    assert rc == 1
    assert start.proc.terminated
    assert "did not come up" in err and "building index" not in err
    assert 5.0 <= start.clock[0] - 100.0 < 6.0


def test_a_finished_build_gives_the_daemon_one_fresh_timeout(start, capsys) -> None:
    """A daemon that wedges after its build still fails, --timeout after the build ends."""
    appended = []

    def probe(n: int, t: float) -> None:
        if t >= 40 and not appended:
            appended.append(start.append(BUILT))
        return None

    rc = start(BUILD, probe)
    err = capsys.readouterr().err
    assert rc == 1
    assert start.proc.terminated
    assert "did not come up" in err and "building index" in err
    assert 45.0 <= start.clock[0] - 100.0 < 46.0, start.clock[0]


def test_a_build_that_finished_inside_the_timeout_extends_it_once(start, capsys) -> None:
    rc = start(BUILD + BUILT, lambda n, t: BODY if t > 8 else None)
    err = capsys.readouterr().err
    assert rc == 0, err
    assert not start.proc.terminated and "building index" not in err
