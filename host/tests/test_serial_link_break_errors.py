"""A transport failure during `POST /break` answers 400 naming the port, not a portless 500."""

from __future__ import annotations

import ctypes
import sys
import types

import pytest
import serial

from tests.support import Stack, stack_client


def _failing_break(exc: BaseException):
    def send_break(seconds: float) -> bool:
        raise exc
    return send_break


def _break_with(stack: Stack, monkeypatch, exc: BaseException):
    assert stack.wait_connected(True)
    monkeypatch.setattr(stack.sim.links[-1], "send_break", _failing_break(exc))
    with stack_client(stack) as c:
        return c.post("/break", json={"ms": 5})


def _termios_error():
    import termios
    return termios.error(5, "Input/output error")


@pytest.mark.parametrize("make_exc", [
    pytest.param(lambda: serial.SerialException("device reports readiness to read"),
                 id="serial"),
    pytest.param(lambda: OSError(5, "Input/output error"), id="oserror"),
    # termios.error is not an OSError: tcsendbreak on a vanished tty raises it.
    pytest.param(_termios_error, id="termios", marks=pytest.mark.skipif(
        sys.platform == "win32", reason="no termios on Windows")),
])
def test_a_failing_break_is_a_400_naming_the_port(stack: Stack, monkeypatch, make_exc) -> None:
    r = _break_with(stack, monkeypatch, make_exc())
    assert r.status_code == 400, r.text
    assert f"port {stack.alias} break failed: " in r.json()["error"], r.text



# -- a vanished USB adapter on Windows: pyserial ignores the Win32 results -----------------
# Faked at the pyserial and ctypes boundary, so the checked break runs on every OS.

_HANDLE = 0x4D0
_ERR_SET, _ERR_CLEAR = 5, 6


@pytest.fixture
def win32(monkeypatch):
    """A `SerialLink` on a fake pyserial Win32 port, with kernel32, ctypes' error helpers
    and the sleep faked. `events` records every call in order."""
    from mcuscope import link as link_mod

    state = types.SimpleNamespace(events=[], set_ok=1, clear_ok=1, last_error=0,
                                  sleep_raises=None, dlls=[])

    class WinDLL:
        def __init__(self, name: str, use_last_error: bool = False) -> None:
            assert name == "kernel32"
            self.use_last_error = use_last_error
            self.SetCommBreak = self._fn("set", "set_ok", _ERR_SET)
            self.ClearCommBreak = self._fn("clear", "clear_ok", _ERR_CLEAR)
            state.dlls.append(self)

        def _fn(self, name: str, ok_attr: str, err: int):
            def fn(handle):
                state.events.append((name, handle))
                ok = getattr(state, ok_attr)
                if self.use_last_error:        # ctypes saves the error only when asked to
                    state.last_error = 0 if ok else err
                return ok
            return fn

    def sleep(seconds: float) -> None:
        state.events.append(("sleep", seconds))
        if state.sleep_raises:
            raise state.sleep_raises

    class Serial:
        _port_handle = _HANDLE

        def send_break(self, duration: float = 0.25) -> None:
            state.events.append(("pyserial send_break", duration))

    mod = types.ModuleType("serial.serialwin32")
    mod.Serial = Serial
    monkeypatch.setitem(sys.modules, "serial.serialwin32", mod)
    monkeypatch.setattr(serial, "serialwin32", mod, raising=False)
    monkeypatch.setattr(link_mod, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(link_mod, "time", types.SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(ctypes, "WinDLL", WinDLL, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state.last_error, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(f"[WinError {code}] fake"),
                        raising=False)
    state.link = link_mod.SerialLink(Serial(), "COM99")
    return state


def test_a_win32_break_that_lands_holds_the_line_then_clears(win32) -> None:
    from ctypes import wintypes

    assert win32.link.send_break(0.25) is True
    assert win32.events == [("set", _HANDLE), ("sleep", 0.25), ("clear", _HANDLE)]
    k32 = win32.dlls[-1]
    assert k32.SetCommBreak.argtypes == k32.ClearCommBreak.argtypes == [wintypes.HANDLE]


@pytest.mark.parametrize("set_ok, clear_ok, message", [
    # Measured on a pulled adapter: both fail with error 5.
    (0, 0, f"SetCommBreak failed ([WinError {_ERR_SET}] fake)"),
    # The set's error, read before the clear in the finally overwrites it.
    (0, 1, f"SetCommBreak failed ([WinError {_ERR_SET}] fake)"),
    (1, 0, f"ClearCommBreak failed ([WinError {_ERR_CLEAR}] fake)"),
])
def test_a_win32_break_on_a_vanished_handle_raises(win32, set_ok, clear_ok, message) -> None:
    win32.set_ok, win32.clear_ok = set_ok, clear_ok
    with pytest.raises(serial.SerialException) as exc:
        win32.link.send_break(0.25)
    assert str(exc.value) == message
    held = [("sleep", 0.25)] if set_ok else []
    assert win32.events == [("set", _HANDLE), *held, ("clear", _HANDLE)], \
        "the line must be released even after a failed set"


def test_a_win32_break_interrupted_mid_hold_still_clears(win32) -> None:
    win32.sleep_raises = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        win32.link.send_break(0.25)
    assert win32.events == [("set", _HANDLE), ("sleep", 0.25), ("clear", _HANDLE)]


@pytest.mark.skipif(sys.platform != "win32", reason="the real pyserial Win32 class and kernel32")
def test_the_real_win32_port_and_kernel32_fit_the_checked_break(tmp_path) -> None:
    import msvcrt

    from serial import serialwin32

    from mcuscope import link as link_mod

    ser = serialwin32.Serial()          # unopened: no device is touched
    assert link_mod._is_win32_serial(ser)
    # The private attribute _win32_break reads; a pyserial rename would make /break a 500.
    assert ser._port_handle is None
    # A closed port (None) and a handle that is no COM port both fail the set, and the
    # error read is the set's own, not a stale 0.
    with open(tmp_path / "not-a-port", "wb") as fh:
        for handle in (None, msvcrt.get_osfhandle(fh.fileno())):
            with pytest.raises(serial.SerialException,
                               match=r"^SetCommBreak failed \(\[WinError [1-9]"):
                link_mod._win32_break(types.SimpleNamespace(_port_handle=handle), 0)
