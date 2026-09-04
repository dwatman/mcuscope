"""`link.py` at its edges: which transports can really send a break.

A break is a line state, not bytes, so nothing downstream can tell a break that went out
from one that was swallowed. These pin the two answers `SerialLink.send_break` must give.
"""

from __future__ import annotations

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
