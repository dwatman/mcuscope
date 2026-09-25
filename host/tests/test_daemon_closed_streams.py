"""A closed stdout or stderr drops the message; it never owns the exit (class 35).

`mcuscoped >&-` or `2>&-`, or a reader that went away, made the first bare print raise:
a refusal crash-logged instead of returning 1, and a start died on its own banner.
"""

from __future__ import annotations

import io
import socket
import sys
import threading

import pytest

from mcuscope import _stdio
from mcuscope import daemon as daemon_mod
from mcuscope import sim as mcu_sim
from mcuscope.lockfile import CaptureLock
from tests.support import free_port


class _ClosedStream(io.TextIOBase):
    """A text stream whose reader is gone. No fileno, so the devnull dup2 cannot land on a
    real fd of the test process."""

    def __init__(self) -> None:
        self.attempts = 0

    def write(self, s: str) -> int:
        self.attempts += 1
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self) -> None:
        raise BrokenPipeError(32, "Broken pipe")

    def close(self) -> None:
        pass   # IOBase.__del__ closes, and closing flushes


@pytest.fixture
def daemon_argv(tmp_path, monkeypatch):
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    served: list[bool] = []
    monkeypatch.setattr(daemon_mod, "_serve", lambda *a, **kw: served.append(True))
    cfg = tmp_path / "empty.toml"
    cfg.touch()
    argv = ["-c", str(cfg), "--port", str(free_port())]
    return argv, served, str(tmp_path / "data" / "capture.db"), tmp_path / "data"


def test_a_closed_stderr_does_not_stop_an_overridden_lock_start(daemon_argv,
                                                                monkeypatch) -> None:
    argv, served, db, _data = daemon_argv
    err = _ClosedStream()
    held = CaptureLock(db)
    held.acquire()
    try:
        monkeypatch.setattr(sys, "stderr", err)
        rc = daemon_mod.main([*argv, "--ignore-capture-lock"])
    finally:
        held.release()
    assert (rc, served) == (0, [True])
    assert err.attempts >= 1, "positive control: the lock warning went to stderr"


def test_a_closed_stdout_does_not_stop_the_start_banner(daemon_argv, monkeypatch) -> None:
    argv, served, _db, _data = daemon_argv
    out = _ClosedStream()
    monkeypatch.setattr(sys, "stdout", out)
    # 0.0.0.0 adds the exposure warning, the other stdout line before the banner.
    rc = daemon_mod.main([*argv, "--host", "0.0.0.0", "--plotjuggler"])
    assert (rc, served) == (0, [True])
    # The exposure warning, the files notice, the web UI line and the PlotJuggler line.
    assert out.attempts == 4, out.attempts


def test_a_refusal_with_stderr_closed_exits_1_and_writes_no_crash_log(daemon_argv,
                                                                      monkeypatch) -> None:
    argv, served, db, data = daemon_argv
    err = _ClosedStream()
    held = CaptureLock(db)
    held.acquire()
    try:
        monkeypatch.setattr(sys, "stderr", err)
        rc = _stdio.console_entry(lambda: daemon_mod.main(argv), "mcuscoped")
    finally:
        held.release()
    assert rc == 1 and served == []
    assert err.attempts == 1, "positive control: the refusal was written to stderr"
    assert not list(data.glob("*-crash.log"))

    # Positive control for the absence: a crash under the same entry lands in that dir.
    def crash() -> int:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _stdio.console_entry(crash, "mcuscoped")
    assert list(data.glob("*-crash.log"))


def test_a_closed_stderr_does_not_end_the_simulator_listener(monkeypatch) -> None:
    """Each failed session prints a line; that print raising ended the accept loop, and the
    listener was left bound with nothing behind it."""
    err = _ClosedStream()
    monkeypatch.setattr(sys, "stderr", err)
    sessions: list[int] = []
    third = threading.Event()

    def session(args, conn, stop) -> None:
        sessions.append(len(sessions) + 1)
        if len(sessions) <= 2:
            raise RuntimeError(f"session {len(sessions)} failed")
        third.set()

    monkeypatch.setattr(mcu_sim, "_serve_socket_client", session)
    srv = mcu_sim.open_tcp_listener(0)
    port = srv.getsockname()[1]
    stop = threading.Event()
    args = mcu_sim.build_parser().parse_args([])
    thread = threading.Thread(target=mcu_sim.serve_listener, args=(args, srv, stop),
                              daemon=True)
    thread.start()
    try:
        for _ in range(3):
            socket.create_connection(("127.0.0.1", port), timeout=2).close()
        assert third.wait(5), f"the listener stopped accepting after {sessions}"
        assert err.attempts == 2, "positive control: both failures were reported to stderr"
    finally:
        stop.set()
        thread.join(5)
        srv.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe semantics and /proc")
@pytest.mark.parametrize("name", ["stdout", "stderr"])
def test_a_dead_pipe_is_repointed_at_devnull_without_leaking_an_fd(monkeypatch, name) -> None:
    """The dup2 is what stops the shutdown flush exiting 120 on the bytes still buffered."""
    import os

    say = _stdio._say if name == "stdout" else _stdio._note
    rfd, wfd = os.pipe()
    os.close(rfd)                       # the reader is gone
    stream = io.TextIOWrapper(io.FileIO(wfd, "w"), encoding="utf-8")
    monkeypatch.setattr(sys, name, stream)
    before = len(os.listdir("/proc/self/fd"))
    try:
        say("dropped")
        devnull = os.stat(os.devnull)
        assert os.fstat(wfd).st_rdev == devnull.st_rdev, "the fd still points at the pipe"
        assert len(os.listdir("/proc/self/fd")) == before, "the devnull fd leaked"
        say("after")                    # now lands in devnull
        stream.flush()
    finally:
        monkeypatch.undo()
        stream.close()


def test_the_daemon_and_simulator_print_nothing_bare() -> None:
    """Every message in these modules goes through `_stdio._note` or `_say`."""
    import ast
    import inspect

    for module in (daemon_mod, mcu_sim):
        tree = ast.parse(inspect.getsource(module))
        bare = [node.lineno for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print"]
        assert bare == [], f"{module.__name__} prints bare at lines {bare}"
