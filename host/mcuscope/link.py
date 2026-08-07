"""The transport a port talks over: opening one, reading bursts from it, closing it.

`serial_link` answered "what kind of device is this?" in four separate places - which
drain strategy to use, whether presence can be tested at all, which URL schemes are
allowed, and whether the handle implements `cancel_read`/`cancel_write` - each an `if`
on the device string. It also obtained its transport by calling `serial.serial_for_url`
inline in the retry loop, which nothing could substitute. The consequence was that the
only way to reach the reader's *success* path was a real socket, so the burst-timestamp
/ drain / post cycle - the hottest and most-commented code in the module - had no
in-process test, while every reader-thread test drove a device that can never open.

The seam here is real rather than hypothetical: `in_waiting` is a true byte count on a
native port and a 0/1 readability poll on `socket://`, a difference the reader already
had to know about. Those are two adapters. `SourceLink` is the third, and the reason the
read loop is testable without a socket - by the simulator or by a script, which are two
sources behind one Link rather than two Links implementing the same contract twice.
"""

from __future__ import annotations

import abc
import contextlib
import time
from dataclasses import dataclass
from typing import Any

import serial

READ_TIMEOUT = 0.2      # seconds; lets the reader thread notice the stop event
WRITE_TIMEOUT = 2.0     # seconds; a blocked write raises SerialTimeoutException instead
READ_CHUNK = 8192       # max bytes drained from the port in one burst

# serial_for_url dispatches on the URL scheme, and pyserial ships handlers that can write
# files (`spy://...?file=`) or open arbitrary sockets. Only bare device paths and these
# schemes are ever legitimate here. `sim://` never reaches serial_for_url at all: it is
# served by SourceLink, and is allowed here so that a sim device validates and reads as a
# remote transport (nothing to stat, so the backoff stays in charge) like the loopback
# socket it replaces.
ALLOWED_URL_SCHEMES = frozenset({"socket", "rfc2217", "sim"})


def validate_device(device: str | None) -> None:
    """Reject device strings that could turn serial_for_url into a file-write/SSRF gadget.

    `None` (the serial_number path) is fine; it resolves to a real device later. Bare paths
    are allowed (pyserial simply fails to open a non-tty, which is not a vulnerability). URL
    forms are restricted to the allowlisted schemes, and query options (`?file=` and friends)
    are refused outright.
    """
    if device is None:
        return
    if not device or "\n" in device or "\r" in device:
        raise ValueError("invalid device string")
    if "?" in device:
        raise ValueError("device query options are not allowed")
    if "://" in device:
        scheme = device.split("://", 1)[0].lower()
        if scheme not in ALLOWED_URL_SCHEMES:
            raise ValueError(f"device scheme not allowed: {scheme}://")


def is_url_device(device: str | None) -> bool:
    """True if `device` names a URL transport rather than a filesystem/COM device.

    A URL transport has nothing to test for presence: there is no node to stat, and
    connecting is the only way to find out. The presence-gated backoff uses this to know
    when it must fall back to plain exponential retry.
    """
    return device is None or "://" in device


class Link(abc.ABC):
    """One open transport. Everything the reader thread needs and nothing else."""

    device: str

    @abc.abstractmethod
    def read(self, n: int) -> bytes:
        """Block for up to READ_TIMEOUT for at most `n` bytes. Empty means timeout."""

    @abc.abstractmethod
    def drain(self, buf: bytearray) -> None:
        """Append everything already received to `buf`, without blocking.

        Called once per burst, after `read(1)` has anchored the timestamp.
        """

    @abc.abstractmethod
    def write(self, data: bytes) -> None: ...

    @abc.abstractmethod
    def close(self) -> None: ...

    def cancel_read(self) -> bool:
        """Unblock a read in progress. False if this transport cannot.

        Returning a bool rather than raising is what replaces `hasattr(ser, ...)` at the
        call site: pyserial's URL handlers genuinely do not implement it, and a suppressed
        AttributeError made the call look effective when it never was.
        """
        return False

    def cancel_write(self) -> bool:
        """Unblock a write in progress. False if this transport cannot.

        Same contract as `cancel_read`, for the same reason: the URL handlers do not
        implement it, and silence about that is what made a suppressed AttributeError
        look like a cancelled write.
        """
        return False


class SerialLink(Link):
    """A pyserial handle, with the drain strategy its transport needs."""

    def __init__(self, ser: serial.SerialBase, device: str) -> None:
        self._ser = ser
        self.device = device
        self._socket_drain = device.startswith("socket://")

    def read(self, n: int) -> bytes:
        return self._ser.read(n)

    def drain(self, buf: bytearray) -> None:
        """Append everything already received, without blocking.

        The two branches exist because `in_waiting` does not mean the same thing on every
        transport:

        - Native serial ports (and `rfc2217://`) report a true byte count, so one sized
          read empties the driver buffer in a single syscall.
        - The `socket://` handler implements `in_waiting` as a readability poll answering
          0 or 1, so `read(in_waiting)` degenerates into one select+recv per byte -
          measured at 0.2 MB/s, with one `call_soon_threadsafe` hop per two bytes. Setting
          the timeout to 0 turns `read(n)` into a single non-blocking read of whatever is
          buffered instead (measured: 600 MB/s). The flip is free there because that
          handler's `_reconfigure_port` ignores every setting; it is deliberately NOT used
          for `rfc2217://`, where changing a port setting renegotiates over the network.
        """
        ser = self._ser
        if self._socket_drain:
            try:
                ser.timeout = 0
                while len(buf) < READ_CHUNK:
                    chunk = ser.read(READ_CHUNK - len(buf))
                    if not chunk:
                        break
                    buf += chunk
            finally:
                ser.timeout = READ_TIMEOUT
            return
        waiting = ser.in_waiting
        if waiting:
            buf += ser.read(min(waiting, READ_CHUNK))

    def write(self, data: bytes) -> None:
        self._ser.write(data)

    def cancel_read(self) -> bool:
        if not hasattr(self._ser, "cancel_read"):
            return False
        with contextlib.suppress(Exception):
            self._ser.cancel_read()
        return True

    def cancel_write(self) -> bool:
        if not hasattr(self._ser, "cancel_write"):
            return False
        with contextlib.suppress(Exception):
            self._ser.cancel_write()
        return True

    def close(self) -> None:
        self._ser.close()


def open_link(device: str, baud: int) -> Link:
    """Open `device` and wrap it. Raises whatever pyserial raises."""
    ser = serial.serial_for_url(
        device, baudrate=baud, timeout=READ_TIMEOUT, write_timeout=WRITE_TIMEOUT
    )
    return SerialLink(ser, device)


# -- a link whose bytes come from somewhere other than a port ----------------------------


class SourceLink(Link):
    """A Link fed by a `source` object instead of a transport.

    The source answers two questions and nothing else:

        feed(data: bytes) -> bytes    what the far end replies to bytes we sent
        poll() -> bytes               what the far end emits unprompted, right now

    `poll` returning `b""` is "nothing yet", which becomes a read timeout. Either method
    may raise to model the link failing, and `poll` may return a `BurstThenError` to fail
    *during the drain* with complete lines already buffered.

    The point of the source seam is that the read/drain contract - which byte answers the
    read, what the drain appends, where an error surfaces - is implemented once here. The
    simulator behind a link and a scripted burst are two sources, not two Links; writing
    that contract a second time is how the two ended up disagreeing about EOF.
    """

    def __init__(
        self,
        source: Any,
        device: str = "sim://",
        idle: float = 0.01,
        cancellable: bool = False,
    ) -> None:
        self.device = device
        self._source = source
        self._idle = idle
        # False models a URL transport, whose pyserial handlers do not implement cancel_read.
        # Handing a test the native port's capability where production has the socket's is
        # how a reader-teardown test passes on a path production can never take.
        self._cancellable = cancellable
        self._inbox = bytearray()             # replies from feed(), delivered before poll()
        self._rest = bytearray()              # this burst's tail, waiting for the drain
        self._drain_error: Exception | None = None
        self.closed = False
        self.written = bytearray()
        self.cancelled_reads = 0
        self.cancelled_writes = 0

    def read(self, n: int) -> bytes:
        self._rest.clear()
        self._drain_error = None
        if self._inbox:
            data: bytes = bytes(self._inbox)
            self._inbox.clear()
        else:
            item = self._source.poll()
            if isinstance(item, BurstThenError):
                data, self._drain_error = item.data, item.error
            else:
                data = item
        if not data:
            # A read timeout. The sleep is what keeps the reader thread off a spin: a socket
            # blocks in recv for READ_TIMEOUT, an in-process source answers instantly.
            time.sleep(self._idle)
            return b""
        head, rest = data[:n], data[n:]
        self._rest += rest
        return head

    def drain(self, buf: bytearray) -> None:
        buf += self._rest[: max(0, READ_CHUNK - len(buf))]
        self._rest.clear()
        if self._drain_error is not None:
            error, self._drain_error = self._drain_error, None
            raise error

    def write(self, data: bytes) -> None:
        if self.closed:
            raise serial.SerialException("write to a closed link")
        self.written += data
        self._inbox += self._source.feed(data)

    def cancel_read(self) -> bool:
        self.cancelled_reads += 1
        return self._cancellable

    def cancel_write(self) -> bool:
        self.cancelled_writes += 1
        return self._cancellable

    def close(self) -> None:
        self.closed = True


@dataclass
class BurstThenError:
    """A burst that arrives and then the link fails *during the drain*.

    The case that costs a real response if it is handled wrongly: at EOF a `socket://`
    port reports readable and pyserial raises from inside the drain with complete lines
    already sitting in the buffer, so a reader that posts only on success throws that
    burst away (SPEC 3.2 requires the logging half).
    """

    data: bytes
    error: Exception
