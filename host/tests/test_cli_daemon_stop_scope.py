"""`mcu daemon stop` signals only a pid a local record names; `daemon start`'s stderr file.

SPEC 4: the pid /status reports can belong to another machine (a remote --url, a tunnelled
loopback port), so with no local record the stop is POST /shutdown alone.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import types
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import typer

from mcuscope import cli, cli_daemonctl
from mcuscope.cli_client import Settings
from mcuscope.pidfile import pid_running, read_pid_record
from tests.support import dead_pid
from tests.test_cli import _PIDDIR_ENV_SKIP, _run_mcu_data_home, _write_pid_record


class _FakeDaemon:
    """/status naming `pid` while `alive()`; /shutdown refused (403) or accepted."""

    def __init__(self, pid: int, alive: Callable[[], bool], accept: bool) -> None:
        state = {"alive": alive}
        self.state = state

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def _send(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path == "/status" and state["alive"]():
                    self._send(200, {"version": "9.9.9", "uptime_s": 1, "ports": [],
                                     "pid": pid})
                else:
                    self._send(404, {"error": "gone"})

            def do_POST(self) -> None:
                if not accept:
                    self._send(403, {"error": "shutdown is a local operation"})
                    return
                state["alive"] = lambda: False
                self._send(200, {"ok": True})

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def victim():
    """A local process whose pid the fake daemon reports; reaped as soon as it dies."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    reaper = threading.Thread(target=proc.wait, daemon=True)
    reaper.start()
    yield proc
    proc.kill()
    reaper.join(5)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli_daemonctl, "DAEMON_STOP_GRACE_S", 1.0)
    return tmp_path


def _stop(url: str) -> int:
    return cli.main(["--url", url, "daemon", "stop"])


def test_no_record_and_a_refused_shutdown_signals_nothing(victim, data_dir, capsys) -> None:
    fake = _FakeDaemon(victim.pid, lambda: True, accept=False)
    try:
        rc = _stop(fake.url)
    finally:
        fake.close()
    err = capsys.readouterr().err
    assert rc == 1
    assert "no local pid record names it, so no process was signalled" in err
    assert victim.poll() is None, "a process this machine does not know was signalled"


def test_no_record_and_an_accepted_shutdown_is_judged_by_status(victim, data_dir,
                                                               capsys) -> None:
    fake = _FakeDaemon(victim.pid, lambda: True, accept=True)
    try:
        rc = _stop(fake.url)
    finally:
        fake.close()
    out = capsys.readouterr().out
    assert rc == 0
    assert "no local pid record: asked it to shut down, signalled nothing" in out
    assert victim.poll() is None


def test_a_record_naming_the_pid_still_lets_the_signal_fallback_work(victim, data_dir,
                                                                    capsys) -> None:
    """Positive control: the same refused shutdown, with a local record, stops the pid."""
    fake = _FakeDaemon(victim.pid, lambda: victim.poll() is None, accept=False)
    try:
        record = cli_daemonctl._pid_file(Settings(url=fake.url, json_out=False, port=None))
        with open(record, "w", encoding="utf-8", newline="") as fh:
            fh.write(str(victim.pid))
        rc = _stop(fake.url)
    finally:
        fake.close()
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert f"stopped mcuscoped (pid {victim.pid})" in out
    assert victim.wait(5) is not None
    assert not os.path.exists(record)


@pytest.mark.parametrize("recorded, named", [
    (None, "http://127.0.0.1:1"),   # no record: judged on /status alone
    (4242, "pid 4242"),             # a record /status corroborates: judged on the pid
])
def test_a_daemon_still_answering_after_the_stop_is_reported(monkeypatch, capsys, recorded,
                                                             named) -> None:
    """HEALTH-15 D01: the stop "worked" but something still serves the URL."""
    monkeypatch.setattr(cli_daemonctl, "_request_shutdown", lambda s: True)
    # Gone only by the pid this path should wait on, so the check after it is what fails.
    monkeypatch.setattr(cli_daemonctl, "_wait_daemon_gone", lambda s, pid, t: pid == recorded)
    monkeypatch.setattr(cli_daemonctl, "_status_body", lambda s, timeout=2.0: {"version": "9"})
    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
    with pytest.raises(typer.Exit) as ei:
        cli_daemonctl._stop_running_daemon(s, {"pid": 4242}, recorded=recorded)
    out, err = capsys.readouterr()
    assert ei.value.exit_code == 1
    assert f"still answering at http://127.0.0.1:1 after stopping {named}" in err
    assert "stopped" not in out


def _write_record(url: str, pid: int) -> str:
    record = cli_daemonctl._pid_file(Settings(url=url, json_out=False, port=None))
    with open(record, "w", encoding="utf-8", newline="") as fh:
        fh.write(str(pid))
    return record


def test_a_stale_record_naming_a_live_unrelated_pid_is_not_signalled(victim, data_dir,
                                                                    capsys) -> None:
    """A crashed daemon's record names a recycled pid; another daemon serves the URL."""
    fake = _FakeDaemon(4000000, lambda: True, accept=True)
    try:
        record = _write_record(fake.url, victim.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert (f"its pid record named pid {victim.pid}, not the serving process: asked it to "
            "shut down, signalled nothing") in out
    assert victim.poll() is None, "an unrelated process named by a stale record was signalled"
    assert not os.path.exists(record)


def test_a_stale_record_and_a_refused_shutdown_signals_nothing(victim, data_dir,
                                                              capsys) -> None:
    fake = _FakeDaemon(4000000, lambda: True, accept=False)
    try:
        _write_record(fake.url, victim.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    err = capsys.readouterr().err
    assert rc == 1
    assert (f"names pid {victim.pid}, which is not the process serving it, so no process "
            "was signalled") in err
    assert victim.poll() is None


def test_a_record_naming_a_dead_pid_waits_for_status_to_go_quiet(data_dir, capsys) -> None:
    """The stop is judged on /status, not on a stale record's pid being gone at once."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    until = [float("inf")]
    fake = _FakeDaemon(4000000, lambda: time.monotonic() < until[0], accept=False)

    def slow_shutdown(handler) -> None:     # accepted; /status answers 0.4 s longer
        until[0] = time.monotonic() + 0.4
        handler._send(200, {"ok": True})

    fake.httpd.RequestHandlerClass.do_POST = slow_shutdown
    try:
        _write_record(fake.url, dead.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert f"its pid record named pid {dead.pid}" in out


def test_the_launcher_parent_corroborates_a_record_only_on_windows(monkeypatch) -> None:
    body = {"pid": 5, "ppid": 7}
    monkeypatch.setattr(sys, "platform", "linux")
    assert cli_daemonctl._serving_pids(body) == {5}
    monkeypatch.setattr(sys, "platform", "win32")
    assert cli_daemonctl._serving_pids(body) == {5, 7}
    assert cli_daemonctl._serving_pids({"version": "0.1.0"}) == set()


# -- daemon start: the shared stderr file, and --timeout below half a second ---------------


class _Proc:
    def __init__(self, pid: int, exited: int | None) -> None:
        self.pid, self._exited = pid, exited

    def poll(self):
        return self._exited

    def terminate(self) -> None:
        self._exited = -15

    def wait(self, timeout=None):
        return self._exited

    def kill(self) -> None:
        self._exited = -9


def test_a_start_appends_to_the_stderr_file_and_shows_only_its_own_lines(
    data_dir, monkeypatch, capsys,
) -> None:
    """Two starts racing for one host:port share the file; neither may wipe the other's."""
    url = "http://127.0.0.1:1"
    err_path = cli_daemonctl._stderr_log_path(
        cli_daemonctl._pid_file(Settings(url=url, json_out=False, port=None)))
    with open(err_path, "w", encoding="utf-8", newline="") as fh:
        fh.write("the serving daemon's warning\n")

    def spawn(args, **kw):
        os.write(kw["stderr"].fileno(), b"this start's own error\n")
        return _Proc(999999, exited=3)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    rc = cli.main(["--url", url, "daemon", "start", "--timeout", "5"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "this start's own error" in err
    assert "the serving daemon's warning" not in err
    with open(err_path, encoding="utf-8") as fh:
        assert fh.read() == "the serving daemon's warning\nthis start's own error\n"


def test_start_timeout_below_half_a_second_is_honoured(data_dir, monkeypatch, capsys) -> None:
    """HEALTH-15 C11: `--timeout 0.05` waits 0.05 s of readiness, not a hidden 0.5 s floor.

    The clock is the test's, advanced only by the loop's own sleep, so the number of
    readiness probes is the measurement.
    """
    clock = [100.0]
    probes = [0]

    def probe(s, timeout=2.0):
        probes[0] += 1
        return None, None, None

    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cli.time, "sleep", lambda sec: clock.__setitem__(0, clock[0] + sec))
    monkeypatch.setattr(cli, "_status_or_refusal", probe)
    monkeypatch.setattr(cli, "_status_body", lambda s, timeout=2.0: None)
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: _Proc(999998, exited=None))
    rc = cli.main(["--url", "http://127.0.0.1:1", "daemon", "start", "--timeout", "0.05"])
    assert rc == 1
    assert probes[0] == 1, f"{probes[0]} readiness probes for a 0.05 s wait"
    assert "did not come up" in capsys.readouterr().err


_STATUS = {"version": "9.9.9", "uptime_s": 0, "ports": []}
_OWN_LINE = "mcuscoped: a line this start's child wrote"
_WINNER_LINE = "mcuscoped: a line the serving daemon wrote before this start"
_LOCK_REFUSAL = "mcuscoped: capture database is already in use by another mcuscoped"
OWN = object()   # an answer echoing the start id this start handed its child
_LOST = ({**_STATUS, "pid": 4242}, None, None)
_REFUSED = (None, (401, "token required"), None)
_REFUSED_OWN = (None, (401, "token required"), OWN)


class _LosingChild(_Proc):
    """A start's child. `exited`: it already failed on the capture lock. `deaf` names the
    stop calls it survives, `refuse` those the OS refuses; `interrupt_wait` makes a wait
    raise Ctrl-C."""

    def __init__(self, exited=None, deaf=(), refuse=(), interrupt_wait=False) -> None:
        super().__init__(999997, exited)
        self.deaf, self.refuse, self.interrupt_wait = deaf, refuse, interrupt_wait
        self.calls: list = []

    def _stop(self, name: str, code: int) -> None:
        self.calls.append(name)
        if name in self.refuse:
            raise PermissionError(13, "Access is denied")
        if name not in self.deaf:
            self._exited = code

    def terminate(self) -> None:
        self._stop("terminate", -15)

    def kill(self) -> None:
        self._stop("kill", -9)

    def wait(self, timeout=None):
        self.calls.append(("wait", timeout))
        if self.interrupt_wait:
            raise KeyboardInterrupt
        if self._exited is None:
            raise subprocess.TimeoutExpired("mcuscoped", timeout)
        return self._exited


def _start_with(monkeypatch, child: _LosingChild, answer, timeout: str = "5", glob=(),
                before_answer=None):
    """`daemon start` spawning `child`, whose readiness probe answers `answer` (body,
    refusal, start id; OWN echoes the id this start handed the child) or raises it. The
    stderr file already holds a line from before this start. Returns the exit code, the pid
    file and what the record named at each probe."""
    url = "http://127.0.0.1:1"
    pid_path = cli_daemonctl._pid_file(Settings(url=url, json_out=False, port=None))
    with open(cli_daemonctl._stderr_log_path(pid_path), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(_WINNER_LINE + "\n")
    records: list = []
    ids: list[str] = []

    def probe(s, timeout=2.0):
        records.append(read_pid_record(pid_path))
        if before_answer is not None:
            before_answer(pid_path)
        if isinstance(answer, BaseException):
            raise answer
        body, refusal, start_id = answer
        return body, refusal, ids[-1] if start_id is OWN else start_id

    def spawn(args, **kw):
        ids.append(kw["env"]["MCUSCOPED_START_ID"])
        child.env = kw["env"]
        lines = [_OWN_LINE] + ([_LOCK_REFUSAL] if child.poll() is not None else [])
        os.write(kw["stderr"].fileno(), "".join(f"{ln}\n" for ln in lines).encode())
        return child

    monkeypatch.setattr(cli, "_status_body", lambda s, timeout=2.0: None)
    monkeypatch.setattr(cli, "_status_or_refusal", probe)
    monkeypatch.setattr(cli, "_open_append", lambda path: open(path, "ab"))  # noqa: SIM115
    monkeypatch.setattr(subprocess, "Popen", spawn)
    rc = cli.main([*glob, "--url", url, "daemon", "start", "--timeout", timeout])
    return rc, pid_path, records


@pytest.mark.parametrize("glob, token", [(["--token", "s3cret"], "s3cret"), ([], None)])
def test_the_child_gets_a_fresh_start_id_and_the_token_through_its_environment(
        data_dir, monkeypatch, glob, token) -> None:
    monkeypatch.delenv("MCUSCOPED_TOKEN", raising=False)
    monkeypatch.delenv("MCUSCOPE_TOKEN", raising=False)
    ids = []
    for _ in range(2):
        child = _LosingChild()
        rc, _, _ = _start_with(monkeypatch, child, ({**_STATUS, "pid": 4}, None, OWN),
                               glob=glob)
        assert rc == 0
        assert child.env.get("MCUSCOPED_TOKEN") == token
        ids.append(child.env["MCUSCOPED_START_ID"])
    assert all(len(i) == 32 and set(i) <= set("0123456789abcdef") for i in ids), ids
    assert ids[0] != ids[1], "two starts handed their children the same id"


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_an_answer_with_this_starts_id_is_its_daemon_whatever_the_pids(
        data_dir, monkeypatch, capsys, platform) -> None:
    """Neither `pid` nor `ppid` names the spawned process (a launcher chain two deep): the
    id alone says the answer is this start's, and nothing is waited for or signalled."""
    monkeypatch.setattr(sys, "platform", platform)
    child = _LosingChild()
    rc, pid_path, _ = _start_with(monkeypatch, child,
                                  ({**_STATUS, "pid": 4242, "ppid": 1}, None, OWN))
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert "started mcuscoped (pid 4242; launcher 999997)" in out
    assert child.calls == [], "a start answered by its own child waited or signalled"
    assert "now names" not in err, "a record already naming the child was rewritten"
    assert read_pid_record(pid_path) == child.pid


@pytest.mark.parametrize("answer", [
    ({**_STATUS, "pid": 999997}, None, None),                # no id, even naming our pid
    ({**_STATUS, "pid": 999997, "ppid": 1}, None, "ab" * 16),   # another start's id
])
def test_an_answer_without_this_starts_id_is_another_daemon(data_dir, monkeypatch, capsys,
                                                            answer) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    child = _LosingChild()
    rc, pid_path, _ = _start_with(monkeypatch, child, answer)
    err = capsys.readouterr().err
    assert rc == 1
    assert "another daemon is already serving at http://127.0.0.1:1 (pid 999997); stopped " \
        "this start's own process (pid 999997)" in err
    assert not os.path.exists(pid_path)


def _record(pid: int | None):
    """A `before_answer` that leaves the record naming `pid` (None: no record)."""
    def write(pid_path: str) -> None:
        if pid is None:
            os.remove(pid_path)
            return
        with open(pid_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(str(pid))
    return write


@pytest.mark.parametrize("alive", [True, False])
def test_the_winner_takes_the_record_a_racing_loser_wrote_last(victim, data_dir,
                                                               monkeypatch, capsys,
                                                               alive) -> None:
    """Both starts read "no record" and both wrote, the loser's landing last; its reap then
    removed it, and off loopback `daemon stop` could not reach the unrecorded winner. The
    loser's child is stopped at once, so its record most likely names a dead pid."""
    loser = victim.pid if alive else dead_pid()
    child = _LosingChild()
    rc, pid_path, _ = _start_with(monkeypatch, child, ({**_STATUS, "pid": 999997}, None, OWN),
                                  before_answer=_record(loser))
    err = capsys.readouterr().err
    assert rc == 0, err
    assert f"note: {pid_path} named pid {loser}; it now names this start's pid 999997" in err
    assert read_pid_record(pid_path) == child.pid
    cli_daemonctl._remove_pid_record(pid_path, loser)   # the loser's reap, after
    assert read_pid_record(pid_path) == child.pid, "the loser's reap removed the winner's"


def test_a_loser_reap_straddling_the_winners_rewrite_puts_it_back(data_dir,
                                                                  monkeypatch) -> None:
    """The reap read its own child's record, then the winner's rewrite landed before the
    remove: the winner's record must survive."""
    from mcuscope import pidfile

    pid_path = cli_daemonctl._pid_file(Settings(url="http://127.0.0.1:1", json_out=False,
                                                port=None))
    loser, winner = dead_pid(), 999997
    _record(loser)(pid_path)
    real, reads = pidfile.read_pid_record, []

    def read(p):
        value = real(p)
        reads.append(value)
        if len(reads) == 1:
            cli_daemonctl._replace_pid_record(pid_path, winner)   # the rewrite lands
        return value

    monkeypatch.setattr(pidfile, "read_pid_record", read)
    cli_daemonctl._remove_pid_record(pid_path, loser)
    assert reads[:2] == [loser, winner], "positive control: the straddle happened"
    assert real(pid_path) == winner, "the reap deleted the winner's rewritten record"


def test_the_clis_failed_put_back_is_a_warning(data_dir, monkeypatch, capsys) -> None:
    from mcuscope import pidfile

    def refused(src, dst):
        raise PermissionError(13, "Access is denied")

    pid_path = cli_daemonctl._pid_file(Settings(url="http://127.0.0.1:1", json_out=False,
                                                port=None))
    loser = dead_pid()
    _record(loser)(pid_path)
    real, reads = pidfile.read_pid_record, []

    def read(p):
        value = real(p)
        reads.append(value)
        if len(reads) == 1:
            cli_daemonctl._replace_pid_record(pid_path, 999997)
        return value

    monkeypatch.setattr(pidfile, "read_pid_record", read)
    monkeypatch.setattr(pidfile, "_link_new", refused)
    cli_daemonctl._remove_pid_record(pid_path, loser)
    err = capsys.readouterr().err
    assert f"warning: could not put back {pid_path}, which names pid 999997: " in err
    assert "mcuscoped:" not in err


def test_a_losing_start_write_straddling_the_winners_rewrite_never_replaces_it(
        data_dir, monkeypatch) -> None:
    """A later loser read "no record", then the winner's rewrite landed before its write."""
    from mcuscope import pidfile

    pid_path = cli_daemonctl._pid_file(Settings(url="http://127.0.0.1:1", json_out=False,
                                                port=None))
    real, reads = pidfile.read_pid_record, []

    def read(p):
        value = real(p)
        reads.append(value)
        if len(reads) == 1:
            cli_daemonctl._replace_pid_record(pid_path, 999997)
        return value

    monkeypatch.setattr(pidfile, "read_pid_record", read)
    assert cli_daemonctl._write_pid_record(pid_path, 888888) is False
    assert reads == [None], "positive control: the loser read no record"
    assert real(pid_path) == 999997


def test_a_record_the_child_already_claimed_is_not_warned_about(data_dir, monkeypatch,
                                                                capsys) -> None:
    """On POSIX the child's own claim can land before this start's write, naming the same
    pid: that is this start's record, not "another process"."""
    pid_path = cli_daemonctl._pid_file(Settings(url="http://127.0.0.1:1", json_out=False,
                                                port=None))
    from mcuscope import pidfile

    monkeypatch.setattr(pidfile, "pid_running", lambda pid: True)   # the child is up
    _record(999997)(pid_path)
    child = _LosingChild()
    rc, _, records = _start_with(monkeypatch, child, ({**_STATUS, "pid": 999997}, None, OWN))
    err = capsys.readouterr().err
    assert rc == 0, err
    assert records == [999997]
    assert "names another process" not in err and "now names" not in err


def test_a_start_takes_a_stale_record(data_dir) -> None:
    pid_path = cli_daemonctl._pid_file(Settings(url="http://127.0.0.1:1", json_out=False,
                                                port=None))
    _record(dead_pid())(pid_path)
    assert cli_daemonctl._write_pid_record(pid_path, 888888) is True
    assert read_pid_record(pid_path) == 888888


def test_a_start_over_a_live_record_warns_once_and_then_takes_it(victim, data_dir,
                                                                 monkeypatch, capsys) -> None:
    """The warning must not say "left it in place" and then the note "now names"."""
    pid_path = cli_daemonctl._pid_file(Settings(url="http://127.0.0.1:1", json_out=False,
                                                port=None))
    _record(victim.pid)(pid_path)
    child = _LosingChild()
    rc, _, records = _start_with(monkeypatch, child, ({**_STATUS, "pid": 999997}, None, OWN))
    err = capsys.readouterr().err
    assert rc == 0, err
    assert records == [victim.pid], "positive control: the first write was refused"
    assert f"warning: {pid_path} names another process; replaced only if this start's " \
        "daemon answers" in err
    assert "left it in place" not in err
    assert f"note: {pid_path} named pid {victim.pid}; it now names this start's pid 999997" \
        in err
    assert read_pid_record(pid_path) == child.pid


def test_a_refusal_carrying_this_starts_id_takes_the_record_too(victim, data_dir,
                                                                monkeypatch, capsys) -> None:
    """A CLI without the token wins a LAN race: the rewrite must not depend on a body."""
    child = _LosingChild()
    rc, pid_path, _ = _start_with(monkeypatch, child, _REFUSED_OWN,
                                  before_answer=_record(victim.pid))
    err = capsys.readouterr().err
    assert rc == 0, err
    assert "requires a token (HTTP 401: token required)" in err
    assert f"note: {pid_path} named pid {victim.pid}; it now names this start's pid 999997" \
        in err
    assert read_pid_record(pid_path) == child.pid


def test_stop_keeps_the_record_of_a_live_pid_with_no_usable_status(victim, data_dir,
                                                                   monkeypatch,
                                                                   capsys) -> None:
    """A daemon still starting: removing its record is how one becomes unstoppable."""
    url = "http://127.0.0.1:1"
    pid_path = cli_daemonctl._pid_file(Settings(url=url, json_out=False, port=None))
    _record(victim.pid)(pid_path)
    monkeypatch.setattr(cli, "_status_body", lambda s, timeout=2.0: None)
    assert cli.main(["--url", url, "daemon", "stop"]) == 1
    assert f"no usable /status from {url}, but pid {victim.pid} is still running; left its " \
        f"record {pid_path} in place" in capsys.readouterr().err
    assert read_pid_record(pid_path) == victim.pid
    with pytest.raises(subprocess.TimeoutExpired):   # poll() is None while the reaper waits
        victim.wait(0.5)


def test_stop_leaves_a_stale_record_that_changed_before_its_removal(victim, data_dir,
                                                                    monkeypatch,
                                                                    capsys) -> None:
    from mcuscope import pidfile

    url = "http://127.0.0.1:1"
    pid_path = cli_daemonctl._pid_file(Settings(url=url, json_out=False, port=None))
    stale = dead_pid()
    _record(stale)(pid_path)
    real = pidfile.remove_record_if

    def a_start_writes_first(path, pid, **kw):
        _record(victim.pid)(path)
        return real(path, pid, **kw)

    monkeypatch.setattr(cli, "_status_body", lambda s, timeout=2.0: None)
    monkeypatch.setattr(pidfile, "remove_record_if", a_start_writes_first)
    assert cli.main(["--url", url, "daemon", "stop"]) == 1
    err = capsys.readouterr().err
    assert f"the stale pid file {pid_path} (was pid {stale}) changed or could not be moved, " \
        "so it was left as it is" in err
    assert "removed stale pid file" not in err
    assert read_pid_record(pid_path) == victim.pid


def test_the_winner_records_itself_after_the_losers_reap_removed_the_record(
        data_dir, monkeypatch, capsys) -> None:
    child = _LosingChild()
    rc, pid_path, _ = _start_with(monkeypatch, child, ({**_STATUS, "pid": 999997}, None, OWN),
                                  before_answer=_record(None))
    err = capsys.readouterr().err
    assert rc == 0, err
    assert read_pid_record(pid_path) == child.pid
    assert "now names" not in err, "a missing record is not worth a note"


def test_a_record_naming_the_serving_daemon_itself_is_left(data_dir, monkeypatch,
                                                           capsys) -> None:
    """Behind a launcher the daemon's own claim names the interpreter, which `stop`
    matches as `pid`; the launcher's pid would be no better."""
    child = _LosingChild()
    rc, pid_path, _ = _start_with(monkeypatch, child, ({**_STATUS, "pid": 4242}, None, OWN),
                                  before_answer=_record(4242))
    err = capsys.readouterr().err
    assert rc == 0, err
    assert read_pid_record(pid_path) == 4242
    assert "now names" not in err


def test_a_losing_start_never_takes_the_record(victim, data_dir, monkeypatch,
                                               capsys) -> None:
    child = _LosingChild()
    rc, pid_path, _ = _start_with(monkeypatch, child, _LOST,
                                  before_answer=_record(victim.pid))
    assert rc == 1
    assert read_pid_record(pid_path) == victim.pid, "the winner's record was taken"
    assert "now names" not in capsys.readouterr().err


def test_a_record_rewrite_that_fails_is_a_warning_not_a_failed_start(
        victim, data_dir, monkeypatch, capsys) -> None:
    calls = []

    def rewrite_fails(pid_path, pid):
        calls.append(pid)
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(cli_daemonctl, "_replace_pid_record", rewrite_fails)
    child = _LosingChild()
    rc, pid_path, _ = _start_with(monkeypatch, child, ({**_STATUS, "pid": 999997}, None, OWN),
                                  before_answer=_record(victim.pid))
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert calls == [999997], "positive control: the rewrite was attempted"
    assert f"warning: could not write the pid file {pid_path}: [Errno 13] Access is " \
        "denied" in err
    assert "started mcuscoped (pid 999997)" in out


class _CloseInterrupted:
    """The stderr handle, whose close after the spawn is where Ctrl-C lands."""

    def __init__(self, path: str) -> None:
        self.fh = open(path, "ab")  # noqa: SIM115

    def fileno(self) -> int:
        return self.fh.fileno()

    def close(self) -> None:
        self.fh.close()
        raise KeyboardInterrupt


def test_ctrl_c_right_after_the_spawn_names_the_child(data_dir, monkeypatch, capsys) -> None:
    child = _LosingChild()
    monkeypatch.setattr(cli, "_status_body", lambda s, timeout=2.0: None)
    monkeypatch.setattr(cli, "_open_append", _CloseInterrupted)
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: child)
    rc = cli.main(["--url", "http://127.0.0.1:1", "daemon", "start", "--timeout", "5"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "this start's own process (pid 999997) is still running" in err


def test_ctrl_c_inside_the_spawn_is_an_interrupt_not_a_traceback(data_dir, monkeypatch,
                                                                  capsys) -> None:
    def spawn(args, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_status_body", lambda s, timeout=2.0: None)
    monkeypatch.setattr(cli, "_open_append", lambda path: open(path, "ab"))  # noqa: SIM115
    monkeypatch.setattr(subprocess, "Popen", spawn)
    rc = cli.main(["--url", "http://127.0.0.1:1", "daemon", "start", "--timeout", "5"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "interrupted" in err
    assert "is still running" not in err and "Traceback" not in err


def test_a_lost_race_child_that_already_failed_shows_only_its_own_lines(
        data_dir, monkeypatch, capsys) -> None:
    child = _LosingChild(exited=1)
    rc, pid_path, records = _start_with(monkeypatch, child, _LOST)
    err = capsys.readouterr().err
    assert rc == 1
    assert "another daemon is already serving at http://127.0.0.1:1 (pid 4242)" in err
    assert _LOCK_REFUSAL in err, "the child's own refusal"
    assert _WINNER_LINE not in err, "a line from before this start was shown as its own"
    assert "stopped this start's own process" not in err
    assert child.calls == [], "a child that had already exited was signalled"
    assert records == [child.pid], "positive control: the record named the child"
    assert not os.path.exists(pid_path), "the record naming the loser's child was kept"


def test_a_lost_race_stops_a_child_that_is_still_starting_at_once(data_dir, monkeypatch,
                                                                  capsys) -> None:
    """Waiting for it first left it 10 s to take the lock and the port once the winner
    stopped, and then report it as never having served."""
    child = _LosingChild()
    rc, pid_path, records = _start_with(monkeypatch, child, _LOST)
    err = capsys.readouterr().err
    assert rc == 1
    assert child.calls == ["terminate", ("wait", cli_daemonctl.DAEMON_STOP_GRACE_S)]
    assert "(pid 4242); stopped this start's own process (pid 999997)" in err
    assert records == [child.pid]
    assert not os.path.exists(pid_path)


def test_a_lost_race_child_deaf_to_terminate_is_killed(data_dir, monkeypatch,
                                                       capsys) -> None:
    child = _LosingChild(deaf={"terminate"})
    rc, pid_path, _ = _start_with(monkeypatch, child, _LOST)
    err = capsys.readouterr().err
    grace = ("wait", cli_daemonctl.DAEMON_STOP_GRACE_S)
    assert rc == 1
    assert child.calls == ["terminate", grace, "kill", grace]
    assert child.poll() == -9
    assert "stopped this start's own process (pid 999997)" in err
    assert not os.path.exists(pid_path)


@pytest.mark.parametrize("deaf, refuse", [({"terminate", "kill"}, ()),
                                          ((), {"terminate", "kill"})])
def test_a_lost_race_child_that_cannot_be_stopped_keeps_its_record(
        data_dir, monkeypatch, capsys, deaf, refuse) -> None:
    """Deaf to both stops, or the OS refusing both: its record is the only handle left, and
    its stderr lines are the only clue why."""
    child = _LosingChild(deaf=deaf, refuse=refuse)
    rc, pid_path, _ = _start_with(monkeypatch, child, _LOST)
    err = capsys.readouterr().err
    assert rc == 1
    assert "this start's own process (pid 999997) is still running and could not be " \
        f"stopped (pid file {pid_path})" in err
    assert _OWN_LINE in err and _WINNER_LINE not in err
    assert read_pid_record(pid_path) == child.pid, "the only handle on a live child was lost"


def test_a_lost_race_terminate_refused_by_the_os_still_escalates(data_dir, monkeypatch,
                                                                 capsys) -> None:
    child = _LosingChild(refuse={"terminate"})
    rc, pid_path, _ = _start_with(monkeypatch, child, _LOST)
    err = capsys.readouterr().err
    assert rc == 1
    assert child.calls[::2] == ["terminate", "kill"], child.calls
    assert "stopped this start's own process (pid 999997)" in err
    assert not os.path.exists(pid_path)


def test_ctrl_c_while_stopping_a_losing_child_names_it(data_dir, monkeypatch,
                                                       capsys) -> None:
    child = _LosingChild(deaf={"terminate"}, interrupt_wait=True)
    rc, pid_path, _ = _start_with(monkeypatch, child, _LOST)
    err = capsys.readouterr().err
    assert rc == 1
    assert child.calls[0] == "terminate", "the stop was not sent before the wait"
    assert "this start's own process (pid 999997) is still running" in err
    assert "interrupted" in err
    assert read_pid_record(pid_path) == child.pid


def test_ctrl_c_in_the_readiness_wait_names_the_child(data_dir, monkeypatch,
                                                      capsys) -> None:
    child = _LosingChild()
    rc, pid_path, _ = _start_with(monkeypatch, child, KeyboardInterrupt())
    err = capsys.readouterr().err
    assert rc == 1
    assert "this start's own process (pid 999997) is still running" in err
    assert child.calls == [], "Ctrl-C signalled the child"
    assert read_pid_record(pid_path) == child.pid


def test_ctrl_c_while_writing_the_pid_record_names_the_child(data_dir, monkeypatch,
                                                              capsys) -> None:
    def interrupted(pid_path, pid):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_write_pid_record", interrupted)
    child = _LosingChild()
    rc, _, records = _start_with(monkeypatch, child, _LOST)
    err = capsys.readouterr().err
    assert rc == 1
    assert "this start's own process (pid 999997) is still running" in err
    assert records == [], "the readiness wait was reached"


def test_ctrl_c_after_the_child_exited_removes_its_record(data_dir, monkeypatch,
                                                          capsys) -> None:
    child = _LosingChild(exited=1)
    rc, pid_path, records = _start_with(monkeypatch, child, KeyboardInterrupt())
    err = capsys.readouterr().err
    assert rc == 1
    assert "interrupted" in err
    assert "is still running" not in err
    assert records == [child.pid], "positive control: the record named the child"
    assert not os.path.exists(pid_path), "a record naming a dead child was left"


def test_a_refusal_without_this_starts_id_is_another_daemon(data_dir, monkeypatch,
                                                            capsys) -> None:
    """A 401 carries no pid, but it carries the id: two starts with different tokens got
    the winner's refusal and reported the loser's child as started."""
    child = _LosingChild()
    rc, pid_path, records = _start_with(monkeypatch, child, _REFUSED)
    out, err = capsys.readouterr()
    assert rc == 1
    assert "another daemon is already serving at http://127.0.0.1:1; stopped this start's " \
        "own process (pid 999997)" in err
    assert child.calls[0] == "terminate", "the refusal was waited on instead"
    assert "started mcuscoped" not in out
    assert records == [child.pid]
    assert not os.path.exists(pid_path)


def test_a_refusal_carrying_this_starts_id_is_its_daemon(data_dir, monkeypatch,
                                                         capsys) -> None:
    child = _LosingChild()
    rc, pid_path, _ = _start_with(monkeypatch, child, _REFUSED_OWN)
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert child.calls == [], "a start refused by its own daemon waited or signalled"
    assert "started mcuscoped (pid 999997)" in out
    assert "requires a token (HTTP 401: token required)" in err
    assert read_pid_record(pid_path) == child.pid


def test_a_child_that_exited_just_after_answering_is_not_reported_started(
        data_dir, monkeypatch, capsys) -> None:
    child = _LosingChild(exited=1)
    rc, pid_path, records = _start_with(monkeypatch, child, ({**_STATUS, "pid": 4}, None, OWN))
    out, err = capsys.readouterr()
    assert rc == 1
    assert "mcuscoped exited with status 1 just after answering at http://127.0.0.1:1" in err
    assert _LOCK_REFUSAL in err and _WINNER_LINE not in err
    assert "started mcuscoped" not in out
    assert records == [child.pid]
    assert not os.path.exists(pid_path)


def test_a_start_that_never_answered_is_stopped_like_a_losing_one(data_dir, monkeypatch,
                                                                  capsys) -> None:
    """One stop helper: the same grace and kill escalation as the reap."""
    child = _LosingChild(deaf={"terminate"})
    rc, pid_path, _ = _start_with(monkeypatch, child, (None, None, None), timeout="0")
    err = capsys.readouterr().err
    grace = ("wait", cli_daemonctl.DAEMON_STOP_GRACE_S)
    assert rc == 1
    assert "did not come up at http://127.0.0.1:1 within 0s; stopped it" in err
    assert child.calls == ["terminate", grace, "kill", grace]
    assert not os.path.exists(pid_path)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is TerminateProcess on Windows")
def test_a_real_child_ignoring_sigterm_is_killed_by_the_reap(data_dir) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, "
         "signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    try:
        assert proc.stdout.readline().strip() == b"ready"
        note = cli_daemonctl._reap_losing_start(proc, str(data_dir / "x.pid"), None, 0)
        assert note == f"; stopped this start's own process (pid {proc.pid})"
        assert proc.returncode == -9
    finally:
        proc.kill()
        proc.wait()
        proc.stdout.close()


@pytest.mark.skipif(sys.platform != "win32", reason="the venv launcher is Windows-only")
@pytest.mark.skipif(sys.prefix == sys.base_prefix, reason="needs a venv's launcher")
def test_a_lost_race_stop_reaches_the_interpreter_behind_the_venv_launcher(
        data_dir) -> None:
    """Terminating the launcher must end the interpreter it runs (its kill-on-close job),
    or the loser's real daemon lives on with its record removed. CI fails on this test
    skipping (ci.yml), so a runner without a launcher is loud, not silent."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import os, time; print(os.getpid(), flush=True); "
         "time.sleep(60)"],
        stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        | subprocess.CREATE_NEW_PROCESS_GROUP)  # type: ignore[attr-defined]
    pid = 0
    try:
        pid = int(proc.stdout.readline())
        if pid == proc.pid:
            pytest.skip("no launcher: the venv's python.exe is the interpreter itself")
        assert pid_running(pid), "positive control"
        pid_path = str(data_dir / "x.pid")
        with open(pid_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(str(proc.pid))
        note = cli_daemonctl._reap_losing_start(proc, pid_path, None, 0)
        assert note == f"; stopped this start's own process (pid {proc.pid})"
        deadline = time.monotonic() + 5
        while pid_running(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not pid_running(pid), "the interpreter outlived its launcher's stop"
        assert not os.path.exists(pid_path)
    finally:
        proc.kill()
        proc.wait()
        proc.stdout.close()
        if pid and pid != proc.pid and pid_running(pid):
            os.kill(pid, 9)   # TerminateProcess on Windows


DAEMON_PID = 4000000


def _ppid_daemon(ppid_of, alive, accept: bool) -> _FakeDaemon:
    """A fake whose nth /status (from 1) reports pid DAEMON_PID and ppid `ppid_of(n)`."""
    fake = _FakeDaemon(DAEMON_PID, alive, accept=accept)
    orig_get = fake.httpd.RequestHandlerClass.do_GET
    n = [0]

    def do_get(handler) -> None:
        if handler.path == "/status" and fake.state["alive"]():
            n[0] += 1
            handler._send(200, {"version": "9.9.9", "uptime_s": 1, "ports": [],
                                "pid": DAEMON_PID, "ppid": ppid_of(n[0])})
        else:
            orig_get(handler)

    fake.httpd.RequestHandlerClass.do_GET = do_get
    return fake


@pytest.fixture
def win32(monkeypatch):
    monkeypatch.setattr(cli_daemonctl, "sys", types.SimpleNamespace(platform="win32"))


def test_the_parent_of_a_daemon_that_shut_down_is_not_signalled(win32, victim, data_dir,
                                                                capsys) -> None:
    """The recorded pid is the daemon's parent (a cmd.exe it was run from), which never
    exits; the daemon accepts /shutdown and goes."""
    fake = _ppid_daemon(lambda n: victim.pid, lambda: True, accept=True)
    try:
        record = _write_record(fake.url, victim.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert f"stopped mcuscoped (pid {victim.pid})" in out
    assert victim.poll() is None, "the daemon's parent was signalled after it had gone"
    assert not os.path.exists(record)


def test_a_parent_status_still_names_after_the_grace_is_signalled(win32, victim, data_dir,
                                                                  capsys) -> None:
    """Positive control: the daemon refuses /shutdown and keeps naming the parent, which is
    then signalled (and the daemon, a shim's child, goes with it)."""
    fake = _ppid_daemon(lambda n: victim.pid, lambda: victim.poll() is None, accept=False)
    try:
        _write_record(fake.url, victim.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert victim.wait(5) is not None
    assert f"stopped mcuscoped (pid {victim.pid})" in out


def test_a_parent_status_stops_naming_before_the_signal_is_spared(win32, victim, data_dir,
                                                                  monkeypatch, capsys) -> None:
    """Corroborated when the stop began, not at the moment of signalling."""
    monkeypatch.setattr(cli_daemonctl, "_wait_daemon_gone", lambda s, pid, t: False)
    fake = _ppid_daemon(lambda n: victim.pid if n == 1 else 1, lambda: True, accept=False)
    try:
        _write_record(fake.url, victim.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    err = capsys.readouterr().err
    assert rc == 1
    assert f"names pid {victim.pid}, which is not the process serving it" in err
    assert "no process was signalled" in err
    assert victim.poll() is None


def test_a_daemon_gone_by_the_moment_of_signalling_spares_its_parent(win32, victim,
                                                                     data_dir, monkeypatch,
                                                                     capsys) -> None:
    """The grace ran out, but /status is quiet by the re-check: stopped, nothing signalled."""
    monkeypatch.setattr(cli_daemonctl, "_wait_daemon_gone", lambda s, pid, t: False)
    probes: list[int] = []
    fake = _ppid_daemon(lambda n: victim.pid,
                        lambda: probes.append(0) or len(probes) == 1, accept=False)
    try:
        _write_record(fake.url, victim.pid)
        rc = _stop(fake.url)
    finally:
        fake.close()
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert f"stopped mcuscoped (pid {victim.pid})" in out
    assert victim.poll() is None


def test_restart_starts_only_once_the_launcher_has_exited(win32, data_dir, monkeypatch,
                                                          capsys) -> None:
    """/status goes quiet before the old daemon releases its capture lock; the launcher
    exits only after its child has, so the new start waits for it."""
    shim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    reaper = threading.Thread(target=shim.wait, daemon=True)
    reaper.start()
    seen: list[bool] = []
    monkeypatch.setattr(cli, "_start_daemon",
                        lambda ctx, config, sim, wait_s, open_ui:
                        seen.append(pid_running(shim.pid)))
    monkeypatch.setattr(cli_daemonctl, "DAEMON_STOP_GRACE_S", 10.0)
    fake = _ppid_daemon(lambda n: shim.pid, lambda: True, accept=True)
    try:
        _write_record(fake.url, shim.pid)
        rc = cli.main(["--url", fake.url, "daemon", "restart"])
    finally:
        fake.close()
        shim.kill()
        reaper.join(5)
    assert rc == 0, capsys.readouterr().err
    assert seen == [False], "the new daemon was started while the old launcher still ran"


# -- F7 / class 7: the stale-record stop path says what it did ---------------------------


@_PIDDIR_ENV_SKIP
def test_daemon_stop_reports_removing_a_stale_pid_record(tmp_path) -> None:
    """No daemon answering and the recorded pid gone: remove the record and say so."""
    from tests.test_cli import _child_data_dir

    dead = dead_pid()
    _write_pid_record(str(tmp_path), "127.0.0.1", 1, dead)
    # The child resolves its own data dir; write the record where it will look.
    data_dir = _child_data_dir(str(tmp_path))
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "mcuscoped-127.0.0.1-1.pid")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(str(dead))
    r = _run_mcu_data_home(str(tmp_path), "daemon", "stop")
    assert r.returncode == 1
    assert "removed stale pid file" in r.stderr and str(dead) in r.stderr
    assert not os.path.exists(path)
