"""UDP fan-out of decoded plot points to PlotJuggler (SPEC 3.7).

One datagram per decoded plot line, JSON, matching PlotJuggler's stock "UDP Server"
data source with the JSON parser and `ts` as the message timestamp field. Delivery is
fire-and-forget: this is a viewer path, the SQLite capture is the record, and nothing
here may fail a capture write.
"""

from __future__ import annotations

import json
import logging
import socket
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_DEST = "127.0.0.1:9870"   # PlotJuggler's UDP Server default port


def parse_dest(dest: str) -> tuple[str, int]:
    """Split a `host:port` destination, raising ValueError with the reason.

    rsplit, not split: a bracketless IPv6 literal is not supported, but the error
    should name the port as the problem rather than silently taking `db8` as one.
    """
    host, sep, port_s = dest.strip().rpartition(":")
    if not sep or not host:
        raise ValueError(f"destination must be host:port, not {dest!r}")
    try:
        port = int(port_s)
    except ValueError:
        raise ValueError(f"destination port must be a number, not {port_s!r}") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"destination port must be 1..65535, not {port}")
    return host, port


class PlotJugglerStreamer:
    """Daemon-wide `(enabled, dest)` pair plus the socket that serves it.

    `configure` resolves the destination once (so a hostname costs one lookup and a
    bad one fails the caller, not the capture path); `send` is called on the event
    loop for every decoded plot line and must stay cheap and exception-free.
    """

    def __init__(self, enabled: bool = False, dest: str = DEFAULT_DEST) -> None:
        self.enabled = False
        self.dest = dest
        self._sock: socket.socket | None = None
        self._addr: tuple[Any, ...] | None = None
        if enabled:
            try:
                self.configure(True, dest)
            except (ValueError, OSError) as exc:
                # A dead DNS name at startup disables the stream, it does not kill the
                # daemon: the capture does not depend on the viewer.
                log.warning("plotjuggler: cannot enable for %r: %s", dest, exc)

    def configure(self, enabled: bool, dest: str | None = None) -> None:
        """Set the runtime state; raises ValueError/OSError on a bad destination.

        A raise leaves the previous state in force. The destination resolves lazily on
        enable, so a dest changed while disabled costs nothing until it is used - and a
        dest change always drops the old resolution, so a later bare enable cannot keep
        sending to the address the reported dest no longer names.
        """
        new_dest = self.dest
        if dest:
            parse_dest(dest)   # validate even when disabled: refuse bad state early
            new_dest = dest.strip()
        addr = self._addr if new_dest == self.dest else None
        if enabled and addr is None:
            host, port = parse_dest(new_dest)
            # AF from getaddrinfo so an IPv6 host works; first result wins.
            family, _, _, _, addr = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0]
            if self._sock is not None and self._sock.family != family:
                self._sock.close()
                self._sock = None
            if self._sock is None:
                self._sock = socket.socket(family, socket.SOCK_DGRAM)
        # Nothing above this line mutated state, so a raise kept the old state whole.
        self.dest = new_dest
        self._addr = addr
        self.enabled = enabled

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self.enabled = False

    def send(self, alias: str, ts: float, points: list[dict[str, Any]]) -> None:
        """One datagram for one decoded plot line; every failure is swallowed."""
        if not self.enabled or self._sock is None or self._addr is None or not points:
            return
        msg: dict[str, Any] = {"ts": ts, "tick": points[0]["tick_ms"] / 1000.0,
                               alias: {pt["name"]: pt["value"] for pt in points}}
        try:
            self._sock.sendto(json.dumps(msg, separators=(",", ":")).encode(), self._addr)
        except OSError:
            pass
