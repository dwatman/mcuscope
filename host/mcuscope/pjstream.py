"""UDP fan-out of decoded plot points to PlotJuggler (SPEC 3.7).

One datagram per decoded plot line, JSON, matching PlotJuggler's stock "UDP Server"
data source with the JSON parser and `ts` as the message timestamp field. Delivery is
fire-and-forget: this is a viewer path, the SQLite capture is the record, and nothing
here may fail a capture write.

Thread contract: `send` runs on the event loop for every decoded plot line; `configure`
runs in a worker thread (its `getaddrinfo` blocks). They share exactly one attribute,
`_target`, an immutable `(socket, sockaddr)` pair swapped in a single store and read in
a single load, so `send` can never see a socket paired with the wrong address.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import re
import socket
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_DEST = "127.0.0.1:9870"   # PlotJuggler's UDP Server default port

# Hostname or IPv4 literal. Deliberately colon-free so a bare IPv6 literal gets the
# "needs brackets" error below instead of silently donating its last group as the port.
_HOST_RE = re.compile(r"[A-Za-z0-9._-]+")
_V6_RE = re.compile(r"[0-9A-Fa-f:.]+")   # inside brackets; getaddrinfo does the rest


def parse_dest(dest: str) -> tuple[str, int]:
    """Split a `host:port` (or `[v6addr]:port`) destination, raising ValueError.

    The port must be plain ASCII digits: bare int() also accepts `+9870`, `9_870` and
    every Unicode decimal script, which would persist a dest no reader can trust.
    """
    text = dest.strip()
    host, sep, port_s = text.rpartition(":")
    if not sep or not host:
        raise ValueError(f"destination must be host:port, not {dest!r}")
    if not (port_s.isascii() and port_s.isdigit()):
        raise ValueError(f"destination port must be a number, not {port_s!r}")
    port = int(port_s)
    if not 1 <= port <= 65535:
        raise ValueError(f"destination port must be 1..65535, not {port}")
    if host.startswith("[") and host.endswith("]"):
        inner = host[1:-1]
        if not _V6_RE.fullmatch(inner or ""):
            raise ValueError(f"destination host {host!r} is not an IPv6 literal")
        return inner, port
    if ":" in host:
        # Where the address ends and a port begins is ambiguous in a bare literal,
        # so suggest the form rather than guessing a split.
        raise ValueError(f"IPv6 literal must be bracketed, like [2001:db8::1]:9870, not {dest!r}")
    if not _HOST_RE.fullmatch(host):
        raise ValueError(f"destination host {host!r} is not a hostname or address")
    return host, port


def _resolve(dest: str) -> tuple[int, tuple[Any, ...]]:
    """dest -> (address family, sockaddr), first getaddrinfo result.

    Refuses a non-unicast result: multicast, the unspecified address or the limited
    broadcast widens the audience beyond the named recipient, which is the case the
    config-write bar on this destination exists to exclude (SPEC 3.7). A directed
    broadcast (x.y.z.255) is indistinguishable from a host without the netmask and
    passes; the socket has no SO_BROADCAST, so its sendto fails inertly.
    """
    host, port = parse_dest(dest)
    family, _, _, _, addr = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0]
    ip = ipaddress.ip_address(addr[0].partition("%")[0])   # strip any v6 scope id
    if ip.is_multicast or ip.is_unspecified or ip == ipaddress.IPv4Address("255.255.255.255"):
        raise ValueError(f"destination {addr[0]} is not a unicast address")
    return family, addr


class PlotJugglerStreamer:
    """Daemon-wide `(enabled, dest)` pair plus the socket that serves it.

    Construction stores the dest and nothing else (no I/O, so it is loop-safe);
    `configure(True)` resolves and builds the target off-loop. A raise from
    `configure` leaves the previous state whole: every mutation sits below the last
    statement that can fail. Callers that may run concurrently must serialize
    `configure` themselves (the daemon uses its config write lock).
    """

    def __init__(self, dest: str = DEFAULT_DEST) -> None:
        self.dest = dest.strip()
        # (socket, sockaddr) while enabled, else None. Swapped whole, never mutated.
        self._target: tuple[socket.socket, tuple[Any, ...]] | None = None
        # The previously swapped-out target, closed on the next swap. A replaced
        # socket cannot be closed immediately: an in-flight send on the loop may
        # still hold it, and a close could hand its fd to an unrelated socket
        # mid-sendto. By the next configure/close that send has long returned.
        self._retired: tuple[socket.socket, tuple[Any, ...]] | None = None

    @property
    def enabled(self) -> bool:
        return self._target is not None

    def configure(self, enabled: bool, dest: str | None = None) -> None:
        """Set the runtime state; raises ValueError/OSError on a bad destination.

        The destination is resolved when the stream is enabled or retargeted (not per
        datagram); a dest set while disabled is grammar-checked only, so it costs no
        lookup until it is used.
        """
        new_dest = self.dest
        if dest:
            parse_dest(dest)   # validate even when disabled: refuse bad state early
            new_dest = dest.strip()
        target = None
        if enabled:
            if self._target is not None and new_dest == self.dest:
                target = self._target
            else:
                family, addr = _resolve(new_dest)
                sock = socket.socket(family, socket.SOCK_DGRAM)
                # Non-blocking: a full send buffer must drop the datagram, not stall
                # the event loop the capture runs on. BlockingIOError is an OSError,
                # already swallowed in send().
                sock.setblocking(False)
                target = (sock, addr)
                log.info("plotjuggler: streaming to %s (%s)", new_dest, addr[0])
        # Nothing above this line mutated state, so a raise kept the old state whole.
        self.dest = new_dest
        old, self._target = self._target, target
        retired, self._retired = self._retired, (old if old is not target else None)
        if retired is not None:
            retired[0].close()

    def close(self) -> None:
        """Loop-side shutdown; same thread as send, so the explicit close is safe."""
        target, self._target = self._target, None
        retired, self._retired = self._retired, None
        for pair in (target, retired):
            if pair is not None:
                pair[0].close()

    def send(self, alias: str, ts: float, points: list[dict[str, Any]]) -> None:
        """One datagram for one decoded plot line; every failure is swallowed."""
        target = self._target   # single read: configure swaps this whole, never parts
        if target is None or not points:
            return
        # A typed f4 sample or an overscaled value can be inf/nan, which json.dumps
        # would emit as tokens JSON forbids, killing the whole datagram in the
        # receiver's parser. Drop the value, keep the line (registry class 6).
        chans = {pt["name"]: pt["value"] for pt in points if math.isfinite(pt["value"])}
        if not chans:
            return
        if alias in ("ts", "tick"):
            alias += "_"   # keep the reserved timestamp keys ahead of any port name
        msg = {"ts": ts, "tick": points[0]["tick_ms"] / 1000.0, alias: chans}
        try:
            target[0].sendto(json.dumps(msg, separators=(",", ":")).encode(), target[1])
        except OSError:
            pass
