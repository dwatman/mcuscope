"""A transport failure during `POST /break` answers 400 naming the port, not a portless 500."""

from __future__ import annotations

import sys

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
