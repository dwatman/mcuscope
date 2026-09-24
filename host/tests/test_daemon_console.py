"""Windows console close (`daemon.py`): the daemon installs the CTRL_CLOSE hold even when a
console existed at startup, and its graceful stop fits inside the hold. kernel32 and the
installer are faked here; the real close needs Windows."""

from __future__ import annotations

import _thread
import ctypes
import sys
import time
from pathlib import Path

import pytest

from mcuscope import _stdio
from mcuscope import daemon as daemon_mod


@pytest.fixture
def run_daemon(tmp_path: Path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(_stdio, "install_console_ctrl_handler",
                        lambda **kw: calls.append(f"install {kw}") or True)
    monkeypatch.setattr(daemon_mod, "_release_pid_on_terminating_signal", lambda _p: None)
    monkeypatch.setattr(daemon_mod, "_port_conflict", lambda _h, _p: None)
    monkeypatch.setattr(daemon_mod, "_serve", lambda *a, **kw: calls.append("serve"))
    cfg = tmp_path / "console.toml"
    cfg.write_text(f'[storage]\ndb_path = "{(tmp_path / "c.db").as_posix()}"\n',
                   encoding="utf-8", newline="\n")

    def run() -> list[str]:
        assert daemon_mod.main(["-c", str(cfg), "--port", "18558"]) == 0
        return calls

    return run


def test_a_console_present_at_startup_gets_the_close_hold(run_daemon, monkeypatch) -> None:
    monkeypatch.setattr(_stdio, "_ctrl_handler_ref", None)
    monkeypatch.setattr(_stdio, "have_console", lambda: True)
    assert run_daemon() == ["install {'keep_ctrl_c_ignored': True}", "serve"]


def test_a_consoleless_daemon_keeps_its_inherited_ctrl_state(run_daemon, monkeypatch) -> None:
    monkeypatch.setattr(_stdio, "_ctrl_handler_ref", None)
    monkeypatch.setattr(_stdio, "have_console", lambda: False)
    assert run_daemon() == ["serve"]


@pytest.fixture
def handler(monkeypatch):
    """The installed ctrl handler, on a fake kernel32, with its side effects recorded."""
    events: list[str] = []

    class K32:
        def SetConsoleCtrlHandler(self, handler, add) -> int:  # noqa: N802 (Win32 name)
            return 1

    class WinDLL:
        kernel32 = K32()

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", WinDLL(), raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", lambda *types: (lambda fn: fn), raising=False)
    monkeypatch.setattr(_stdio, "_ctrl_handler_ref", None)
    monkeypatch.setattr(_stdio, "console_close_hook", lambda: events.append("hook"))
    monkeypatch.setattr(_thread, "interrupt_main", lambda: events.append("sigint"))
    monkeypatch.setattr(time, "sleep", lambda s: events.append(f"hold {s}"))
    assert _stdio.install_console_ctrl_handler(keep_ctrl_c_ignored=True)
    return _stdio._ctrl_handler_ref, events


def test_a_console_close_runs_the_hook_before_the_sigint_and_holds(handler) -> None:
    on_event, events = handler
    assert on_event(2) is True
    assert events == ["hook", "sigint", f"hold {_stdio.CONSOLE_CLOSE_HOLD_S}"]


def test_ctrl_c_and_break_leave_the_graceful_wait_alone(handler) -> None:
    on_event, events = handler
    assert on_event(0) is True and on_event(1) is True
    assert events == ["sigint", "sigint"]


def test_the_daemons_hook_shortens_the_graceful_wait_to_fit_the_hold(monkeypatch) -> None:
    seen: list[float] = []

    class FakeServer:
        started = True

        def __init__(self, config) -> None:
            self.config = config

        def run(self) -> None:
            seen.append(self.config.timeout_graceful_shutdown)
            _stdio.console_close_hook()
            seen.append(self.config.timeout_graceful_shutdown)

    monkeypatch.setattr(_stdio, "console_close_hook", None)
    monkeypatch.setattr(daemon_mod, "Server", FakeServer)
    daemon_mod._serve(object(), log_level="warning",
                      timeout_graceful_shutdown=daemon_mod.GRACEFUL_SHUTDOWN_S)
    assert seen == [daemon_mod.GRACEFUL_SHUTDOWN_S, daemon_mod.CONSOLE_CLOSE_GRACEFUL_S]
    # The finaliser (port stop, session close, store flush) needs the rest of the hold.
    assert daemon_mod.CONSOLE_CLOSE_GRACEFUL_S <= _stdio.CONSOLE_CLOSE_HOLD_S - 1.5
