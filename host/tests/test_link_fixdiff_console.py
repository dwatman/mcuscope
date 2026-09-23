"""Windows: the daemon installs the CTRL_CLOSE hold even when a console existed at startup,
so closing the console window gets a graceful stop. The real close needs Windows; here the
installer is replaced and only the daemon's decision to call it is checked."""

from __future__ import annotations

import ctypes
import sys
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


# -- the installer itself, on a fake kernel32 --------------------------------------------


@pytest.fixture
def fake_k32(monkeypatch):
    """`sys.platform` win32 with a kernel32 that records SetConsoleCtrlHandler calls."""
    calls: list[tuple[object, bool]] = []

    class K32:
        def SetConsoleCtrlHandler(self, handler, add) -> int:  # noqa: N802 (Win32 name)
            calls.append((handler, add))
            return 1

    class WinDLL:
        kernel32 = K32()

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", WinDLL(), raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", lambda *types: (lambda fn: fn), raising=False)
    monkeypatch.setattr(_stdio, "_ctrl_handler_ref", None)
    return calls


def test_a_second_install_keeps_the_first_thunk_and_registers_once(fake_k32) -> None:
    assert _stdio.install_console_ctrl_handler() is True
    first = _stdio._ctrl_handler_ref
    assert _stdio.install_console_ctrl_handler(keep_ctrl_c_ignored=True) is True
    assert _stdio._ctrl_handler_ref is first
    assert [add for handler, add in fake_k32 if handler is not None] == [True]


def test_keeping_ctrl_c_ignored_never_clears_the_inherited_flag(fake_k32) -> None:
    assert _stdio.install_console_ctrl_handler(keep_ctrl_c_ignored=True) is True
    assert (None, False) not in fake_k32
    assert fake_k32 == [(_stdio._ctrl_handler_ref, True)]


def test_a_plain_install_clears_the_inherited_flag(fake_k32) -> None:
    """Positive control for the one above: the late-attach path still clears it."""
    assert _stdio.install_console_ctrl_handler() is True
    assert fake_k32 == [(None, False), (_stdio._ctrl_handler_ref, True)]
