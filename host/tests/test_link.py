"""`link.py` at its edges: which transports can really send a break, and a write the
transport cut short.

A break is a line state, not bytes, so nothing downstream can tell a break that went out
from one that was swallowed. These pin the two answers `SerialLink.send_break` must give.
"""

from __future__ import annotations

import os

import pytest
import serial

from mcuscope.link import SerialLink, SourceLink
from tests.support import Scripted


class _FakeSer:
    """A pyserial handle that has send_break, like every SerialBase subclass does."""

    def __init__(self) -> None:
        self.breaks: list[float] = []

    def send_break(self, seconds: float) -> None:
        self.breaks.append(seconds)


def test_send_break_over_socket_is_refused_not_swallowed() -> None:
    """socket:// inherits send_break but its _update_break_state only logs."""
    ser = _FakeSer()
    link = SerialLink(ser, "socket://127.0.0.1:1")
    assert link.send_break(0.01) is False
    assert ser.breaks == [], "the break was handed to a transport that cannot send it"


def test_send_break_over_an_uppercase_socket_url_is_refused_too() -> None:
    """serial_for_url matches the scheme case-insensitively, so the refusal must too."""
    ser = _FakeSer()
    link = SerialLink(ser, "SOCKET://127.0.0.1:1")
    assert link.send_break(0.01) is False
    assert ser.breaks == []


def test_send_break_over_a_native_port_is_sent() -> None:
    ser = _FakeSer()
    link = SerialLink(ser, "/dev/ttyFAKE")
    assert link.send_break(0.01) is True
    assert ser.breaks == [0.01]


def test_source_link_break_on_a_closed_link_raises() -> None:
    """The same refusal a write gets: the handle is gone, so say so."""
    link = SourceLink(Scripted(idle_after=True))
    assert link.send_break(0.005) is True
    link.close()
    with pytest.raises(serial.SerialException):
        link.send_break(0.005)


def test_source_link_reports_the_break_to_its_hook() -> None:
    seen: list[float] = []
    link = SourceLink(Scripted(idle_after=True), on_break=seen.append)
    link.send_break(0.005)
    assert seen == [0.005]


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="POSIX pty")
def test_a_write_cut_short_by_cancel_write_raises() -> None:
    """pyserial returns the short count rather than raising, and the reader's cancel_write
    on a disconnect is what cuts a write short."""
    master, slave = os.openpty()
    ser = serial.Serial(os.ttyname(slave), write_timeout=2)
    try:
        link = SerialLink(ser, ser.port)
        link.write(b"ok")                   # a whole write returns normally
        ser.cancel_write()                  # queued: the next write aborts at once
        with pytest.raises(serial.SerialException,
                           match=r"^write cut short \(\d+ of 100 bytes reported written\)$"):
            link.write(b"x" * 100)
    finally:
        ser.close()
        os.close(master)
        os.close(slave)


def test_a_partial_write_raises() -> None:
    """An aborted Win32 write returns the bytes that did go out, so the short count is partial."""
    class _Partial:
        def write(self, data: bytes) -> int:
            return len(data) - 1

    with pytest.raises(serial.SerialException,
                       match=r"^write cut short \(9 of 10 bytes reported written\)$"):
        SerialLink(_Partial(), "COM9").write(b"x" * 10)
