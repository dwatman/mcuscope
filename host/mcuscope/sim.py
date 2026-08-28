#!/usr/bin/env python3
"""MCU simulator: speaks the full monitor protocol (SPEC section 7).

The simulator lets the whole system run with zero hardware: `mcuscoped` attaches to
it exactly as it would a real serial port. It imports `mcuscope.protocol` so the sim
and the daemon share one encoding implementation.

Two transports (SPEC 7):

- Default TCP (cross-platform): listens on 127.0.0.1 and prints its device string
  `socket://127.0.0.1:<port>` on stdout. The daemon attaches with that as `device`.
- `--pty` (POSIX only): opens a pty pair and prints the slave path, for attaching
  exactly like a real `/dev/tty*` device.

Run standalone (installed: the `mcu-sim` console script; source checkout: the
`tools/mcu_sim.py` shim):

    mcu-sim [--tcp-port PORT] [--plot] [--drop-response N] ...
    mcu-sim --pty [--symlink PATH] ...     # POSIX only

It serves until interrupted. `mcuscoped --sim` runs it in-process instead, for a
zero-setup demo of the whole stack.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import math
import os
import select
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field

from . import protocol as p

PROJECT_NAME = "sim"

# SPEC 2.3/5.4: a command line carries at most 12 tokens including the seq.
MAX_COMMAND_TOKENS = p.MAX_COMMAND_TOKENS

# --- simulated peripheral state ------------------------------------------------------


@dataclass
class SimState:
    """All mutable state of the simulated MCU."""

    start_ns: int
    gpio: dict[str, bool] = field(default_factory=lambda: {"led": False, "en_5v": False})
    eeprom: bytearray = field(default_factory=lambda: bytearray(256))
    temp_raw: int = 0x0640  # ~1600, slowly drifting (fake temperature sensor at 0x48)
    can_rx: int = 0
    can_tx: int = 0
    can_err: int = 0
    can_counter: int = 0
    can_filter_id: int = 0
    can_filter_mask: int = 0
    can_filter_mode: str = "all"  # "all" | "none" | "one"
    can_filter_ext: bool = False  # the SPEC 2.4 `x` flag, passed through to the port layer
    alive_count: int = 0

    def tick_ms(self) -> int:
        return (time.monotonic_ns() - self.start_ns) // 1_000_000 & 0xFFFFFFFF


I2C_SCAN_ADDRS = (0x48, 0x50)
SPI_CS_NAMES = ("imu", "flash")
ADC_NAMES = ("vbat",)

# Extra periodic CAN traffic beyond the 0x100 heartbeat, so the decoded CAN view shows a
# realistic multi-id bus (mix of rates, an extended id, and a remote frame). Each tuple is
# (can_id, period_s, ext, rtr, dlc); data frames carry a rolling counter of dlc bytes.
# Most `--flood` lines emitted in one serve pass, so a scheduling stall cannot turn into a
# single enormous write. At the default 10 ms poll interval this bounds the rate at which
# a stalled sim catches up, not the configured rate itself.
FLOOD_MAX_BURST = 5000

# Most catch-up beats a periodic signal (heartbeat, CAN bus, `sim alive`, plot samples) may
# emit in one serve pass. These are live signals, not data to backfill: a longer stall
# re-anchors the schedule to now and the missed beats are dropped. Without it a stall of
# even a few minutes owed hundreds of thousands of lines from a single poll_events() pass,
# all stamped with the same tick, and Windows' monotonic clock advances through suspend, so
# any suspend/resume reached it. Same invariant FLOOD_MAX_BURST holds for `--flood`, at the
# count a periodic signal needs.
PERIODIC_MAX_BURST = 4

CAN_BUS = (
    (0x200, 0.5, False, False, 2),   # 2 Hz, 2-byte payload
    (0x18A, 1.0, True, False, 8),    # 1 Hz, extended id, 8-byte payload
    (0x321, 0.2, False, False, 1),   # 5 Hz, 1-byte payload
    (0x400, 2.0, False, True, 8),    # 0.5 Hz, remote request, dlc 8
)


def _due_beats(now: float, next_due: float, period: float) -> tuple[int, float]:
    """Beats of `period` owed at `now`, and the next-due time to carry forward.

    Bounded by PERIODIC_MAX_BURST; when the cap bites, the schedule re-anchors to `now`
    rather than continuing to owe the backlog, so one stall costs one bounded burst.
    """
    if now < next_due:
        return 0, next_due
    owed = int((now - next_due) / period) + 1
    if owed > PERIODIC_MAX_BURST:
        return PERIODIC_MAX_BURST, now + period
    return owed, next_due + owed * period


# --- command handling ----------------------------------------------------------------


class Simulator:
    """Parses commands and produces response/event lines, decoupled from I/O."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.state = SimState(start_ns=time.monotonic_ns())
        self.cmd_count = 0
        # scheduling (all in seconds, monotonic)
        now = time.monotonic()
        self.next_heartbeat = now + 0.1
        self.next_can = {cid: now + period for cid, period, *_ in CAN_BUS}
        self.can_bus_counter = 0
        self.next_alive = now + 2.0
        self.next_plot = now + 0.05
        self.next_plot_def = (now + 5.0) if args.plot_late_def else now
        self.last_plot_def_broadcast = 0.0
        self.pending_echoes: list[tuple[float, p.CanFrame]] = []
        # Event lines a command handler produced as a side effect (`mark`), drained by the
        # next poll_events() so they leave the same path as every other asynchronous line.
        self.async_lines: list[str] = []
        self.garbage_counter = 0
        # Set while an over-length inbound line is being discarded up to its LF.
        self.rx_overflow = False
        self.next_flood = now
        self.flood_seq = 0

    # -- top-level dispatch -----------------------------------------------------------

    def handle_line(self, raw: str) -> list[str]:
        """Handle one received line, returning zero or more lines to send back."""
        if p.classify(raw) is not p.LineClass.COMMAND:
            # The monitor silently ignores lines that do not start with '>' (SPEC 2.2).
            return []
        try:
            cmd = p.parse_command(raw)
        except p.ProtocolError:
            # SPEC 2.3: a seq that parses but carries no command name is answered
            # `ERR 1 badcmd`; silence is only for the case where there is no seq to echo,
            # and answering nothing left the daemon waiting out its timeout.
            parts = p.split_tokens(p.normalize_line(raw)[1:])
            if len(parts) != 1:
                return []
            try:
                seq = p.parse_seq_token(parts[0])
            except p.ProtocolError:
                return []
            return [p.format_response_err(seq, p.ERROR_CODES["badcmd"], "no command")]
        if len(cmd.tokens) + 1 > MAX_COMMAND_TOKENS:
            # SPEC 2.3/5.4: at most 12 tokens including the seq; a longer line is rejected
            # whole rather than truncated, as monitor.c's tokenize()/process_line() do.
            return [p.format_response_err(cmd.seq, p.ERROR_CODES["badarg"], "too many tokens")]
        self.cmd_count += 1
        if self.args.drop_response and self.cmd_count == self.args.drop_response:
            # Swallow the response to the Nth command to exercise the timeout path.
            return []
        out: list[str] = []
        resp = self.dispatch(cmd)
        out.append(resp)
        return out

    def dispatch(self, cmd: p.Command) -> str:
        seq = cmd.seq
        tokens = cmd.tokens
        name = tokens[0] if tokens else ""
        sub = tokens[1] if len(tokens) > 1 else ""
        rest = tokens[2:]
        try:
            if name == "ping":
                return p.format_response_ok(seq, f"monitor {p.PROTO_VERSION} {PROJECT_NAME}")
            if name == "info":
                up = self.state.tick_ms()
                return p.format_response_ok(seq, f"up={up} rst=por fw=sim-0.1")
            if name == "can":
                return self._can(seq, sub, rest)
            if name == "i2c":
                return self._i2c(seq, sub, rest)
            if name == "spi":
                return self._spi(seq, sub, rest)
            if name == "gpio":
                return self._gpio(seq, sub, rest)
            if name == "adc":
                return self._adc(seq, sub, rest)
            if name == "mark":
                return self._mark(seq, (sub, *rest))
            return p.format_response_err(seq, p.ERROR_CODES["badcmd"], f"unknown {name}")
        except p.ProtocolError as exc:
            return p.format_response_err(seq, p.ERROR_CODES["badarg"], str(exc).split(":")[0])

    # -- CAN --------------------------------------------------------------------------

    def _can(self, seq: int, sub: str, rest: tuple[str, ...]) -> str:
        st = self.state
        if sub == "tx":
            frame = p.parse_can_tx_args(rest)
            st.can_tx += 1
            # Echo the transmitted frame back with id+1 after 20 ms (SPEC 7). The id wraps
            # within its own range: at the top of the range (`can tx 7FF`) a bare +1 built
            # a frame format_can_event refuses, and that raise escaped poll_events and
            # killed the serving thread, so one command bricked the simulator for good.
            echo = p.CanFrame(
                can_id=(frame.can_id + 1) % (
                    (p.CAN_ID_MAX_EXT if frame.ext else p.CAN_ID_MAX_STD) + 1
                ),
                data=frame.data,
                ext=frame.ext,
                rtr=frame.rtr,
                dlc=frame.dlc,
            )
            self.pending_echoes.append((time.monotonic() + 0.02, echo))
            return p.format_response_ok(seq)
        if sub == "filter":
            return self._can_filter(seq, rest)
        if sub == "stat":
            state = "active"
            return p.format_response_ok(
                seq, f"rx={st.can_rx} tx={st.can_tx} err={st.can_err} state={state}"
            )
        return p.format_response_err(seq, p.ERROR_CODES["badcmd"], "bad can subcmd")

    def _can_filter(self, seq: int, rest: tuple[str, ...]) -> str:
        st = self.state
        if len(rest) == 1 and rest[0] == "all":
            st.can_filter_mode = "all"
            return p.format_response_ok(seq)
        if len(rest) == 1 and rest[0] == "none":
            st.can_filter_mode = "none"
            return p.format_response_ok(seq)
        # SPEC 2.4: `x` (extended) is accepted and passed to the port layer; `r` is refused,
        # because matching is defined over id/mask alone and answering OK to a filter that
        # cannot be honoured is worse than refusing it. Anything else is badarg - the earlier
        # `len(rest) >= 2` silently accepted and ignored any third token.
        if len(rest) in (2, 3) and (len(rest) == 2 or rest[2] == "x"):
            st.can_filter_id = p.parse_hex_int(rest[0])
            st.can_filter_mask = p.parse_hex_int(rest[1])
            st.can_filter_mode = "one"
            st.can_filter_ext = len(rest) == 3
            return p.format_response_ok(seq)
        return p.format_response_err(seq, p.ERROR_CODES["badarg"], "can filter args")

    def _can_passes_filter(self, can_id: int, ext: bool = False) -> bool:
        st = self.state
        if st.can_filter_mode == "all":
            return True
        if st.can_filter_mode == "none":
            return False
        # SPEC 2.4 hands the `x` flag to the port layer, and in the simulator the filter is
        # the port layer: an extended-only filter must not pass a standard-id frame.
        if st.can_filter_ext and not ext:
            return False
        return (can_id & st.can_filter_mask) == (st.can_filter_id & st.can_filter_mask)

    # -- I2C --------------------------------------------------------------------------

    def _i2c(self, seq: int, sub: str, rest: tuple[str, ...]) -> str:
        if sub == "scan":
            found = " ".join(f"{a:02X}" for a in I2C_SCAN_ADDRS)
            return p.format_response_ok(seq, found)
        if sub == "wr":
            if len(rest) != 2:
                return p.format_response_err(seq, p.ERROR_CODES["badarg"], "i2c wr args")
            addr = _i2c_addr(rest[0])
            data = p.hex_to_bytes(rest[1])
            return self._i2c_write(seq, addr, data)
        if sub == "rd":
            if len(rest) != 2:
                return p.format_response_err(seq, p.ERROR_CODES["badarg"], "i2c rd args")
            addr = _i2c_addr(rest[0])
            n = _parse_dec(rest[1], 1, 64)
            return self._i2c_read(seq, addr, None, n)
        if sub == "wrrd":
            if len(rest) != 3:
                return p.format_response_err(seq, p.ERROR_CODES["badarg"], "i2c wrrd args")
            addr = _i2c_addr(rest[0])
            wr = p.hex_to_bytes(rest[1])
            n = _parse_dec(rest[2], 1, 64)
            return self._i2c_read(seq, addr, wr, n)
        return p.format_response_err(seq, p.ERROR_CODES["badcmd"], "bad i2c subcmd")

    def _i2c_write(self, seq: int, addr: int, data: bytes) -> str:
        if addr == 0x50:
            # EEPROM: first byte is the offset, rest is written from there.
            if not data:
                return p.format_response_ok(seq)
            off = data[0]
            for i, b in enumerate(data[1:]):
                self.state.eeprom[(off + i) & 0xFF] = b
            return p.format_response_ok(seq)
        if addr == 0x48:
            return p.format_response_ok(seq)  # temp sensor: register pointer write, ignored
        return p.format_response_err(seq, p.ERROR_CODES["nack"], "no device")

    def _i2c_read(self, seq: int, addr: int, wr: bytes | None, n: int) -> str:
        if addr == 0x48:
            # Temperature sensor: reg 0x00 returns two big-endian bytes, slowly drifting.
            self.state.temp_raw = (self.state.temp_raw + 1) & 0xFFFF
            val = struct.pack(">H", self.state.temp_raw)
            data = (val * ((n // 2) + 1))[:n]
            return p.format_response_ok(seq, p.bytes_to_hex(data))
        if addr == 0x50:
            off = wr[0] if wr else 0
            data = bytes(self.state.eeprom[(off + i) & 0xFF] for i in range(n))
            return p.format_response_ok(seq, p.bytes_to_hex(data))
        return p.format_response_err(seq, p.ERROR_CODES["nack"], "no device")

    # -- SPI --------------------------------------------------------------------------

    def _spi(self, seq: int, sub: str, rest: tuple[str, ...]) -> str:
        if sub != "xfer" or len(rest) != 2:
            return p.format_response_err(seq, p.ERROR_CODES["badarg"], "spi xfer args")
        cs, data_hex = rest
        if cs not in SPI_CS_NAMES:
            return p.format_response_err(seq, p.ERROR_CODES["badarg"], f"unknown cs {cs}")
        tx = p.hex_to_bytes(data_hex)
        rx = bytes((~b) & 0xFF for b in tx)  # echo inverted (SPEC 7)
        return p.format_response_ok(seq, p.bytes_to_hex(rx))

    # -- GPIO -------------------------------------------------------------------------

    def _gpio(self, seq: int, sub: str, rest: tuple[str, ...]) -> str:
        st = self.state
        if sub == "set":
            if len(rest) != 2 or rest[0] not in st.gpio or rest[1] not in ("0", "1"):
                return p.format_response_err(seq, p.ERROR_CODES["badarg"], "gpio set args")
            st.gpio[rest[0]] = rest[1] == "1"
            return p.format_response_ok(seq)
        if sub == "get":
            if len(rest) != 1 or rest[0] not in st.gpio:
                return p.format_response_err(seq, p.ERROR_CODES["badarg"], "gpio get args")
            return p.format_response_ok(seq, "1" if st.gpio[rest[0]] else "0")
        return p.format_response_err(seq, p.ERROR_CODES["badcmd"], "bad gpio subcmd")

    # -- ADC --------------------------------------------------------------------------

    def _adc(self, seq: int, sub: str, rest: tuple[str, ...]) -> str:
        if sub != "read" or len(rest) != 1:
            return p.format_response_err(seq, p.ERROR_CODES["badarg"], "adc read args")
        if rest[0] not in ADC_NAMES:
            return p.format_response_err(seq, p.ERROR_CODES["badarg"], f"unknown adc {rest[0]}")
        # Slightly noisy value around 3300 mV.
        tick = self.state.tick_ms()
        mv = 3300 + int(20 * math.sin(tick / 500.0))
        raw = mv * 4095 // 3300
        return p.format_response_ok(seq, f"raw={raw} mv={mv}")

    # -- markers (SPEC 2.5) -----------------------------------------------------------

    def _mark(self, seq: int, tokens: tuple[str, ...]) -> str:
        """`mark <text>`: emit a firmware marker, the sim's stand-in for monitor_mark()."""
        text = " ".join(t for t in tokens if t)
        if not text:
            return p.format_response_err(seq, p.ERROR_CODES["badarg"], "empty marker")
        self.async_lines.append(p.format_marker(text, self.state.tick_ms()))
        return p.format_response_ok(seq)

    # -- periodic / asynchronous emissions --------------------------------------------

    def poll_events(self) -> list[str]:
        """Return any asynchronous lines due now (heartbeats, echoes, debug, plot)."""
        out: list[str] = []
        now = time.monotonic()
        st = self.state

        # Side-effect lines from the last command handled (see async_lines).
        if self.async_lines:
            out.extend(self.async_lines)
            self.async_lines = []

        # CAN heartbeat frame, id 0x100 at 10 Hz, counter payload.
        beats, self.next_heartbeat = _due_beats(now, self.next_heartbeat, 0.1)
        for _ in range(beats):
            st.can_counter = (st.can_counter + 1) & 0xFFFFFFFF
            frame = p.CanFrame(
                can_id=0x100,
                data=struct.pack(">I", st.can_counter),
                tick_ms=st.tick_ms(),
            )
            st.can_rx += 1
            if self._can_passes_filter(frame.can_id, frame.ext):
                out.append(p.format_can_event(frame))

        # Additional periodic CAN traffic (multi-id bus) for a realistic decoded view.
        for cid, period, ext, rtr, dlc in CAN_BUS:
            beats, self.next_can[cid] = _due_beats(now, self.next_can[cid], period)
            for _ in range(beats):
                st.can_rx += 1
                if rtr:
                    frame = p.CanFrame(can_id=cid, ext=ext, rtr=True, dlc=dlc, tick_ms=st.tick_ms())
                else:
                    self.can_bus_counter = (self.can_bus_counter + 1) & 0xFFFFFFFFFFFFFFFF
                    data = struct.pack(">Q", self.can_bus_counter)[-dlc:]
                    frame = p.CanFrame(can_id=cid, data=data, ext=ext, tick_ms=st.tick_ms())
                if self._can_passes_filter(cid, ext):
                    out.append(p.format_can_event(frame))

        # Delayed echoes of transmitted frames (id+1 after 20 ms).
        still_pending: list[tuple[float, p.CanFrame]] = []
        for due, frame in self.pending_echoes:
            if now >= due:
                frame.tick_ms = st.tick_ms()
                st.can_rx += 1
                if self._can_passes_filter(frame.can_id, frame.ext):
                    out.append(p.format_can_event(frame))
            else:
                still_pending.append((due, frame))
        self.pending_echoes = still_pending

        # Debug line every 2 s.
        beats, self.next_alive = _due_beats(now, self.next_alive, 2.0)
        for _ in range(beats):
            st.alive_count += 1
            out.append(f"sim alive n={st.alive_count}")

        if self.args.plot:
            out.extend(self._poll_plot(now))

        if getattr(self.args, "flood", 0):
            out.extend(self._poll_flood(now))

        if self.args.garbage:
            self.garbage_counter += 1
            if self.garbage_counter % 500 == 0:
                out.append(RawJunk("\x01\x02\x7f binary junk \x00 line"))

        return out

    def _poll_flood(self, now: float) -> list[str]:
        """Emit plain debug lines at the `--flood` rate, for load and back-pressure testing.

        Catches up on whatever is owed since the last pass rather than emitting one line
        per poll, so the requested rate is met regardless of how often the serve loop runs.
        The per-pass burst is bounded so a scheduling hiccup (or a long stall) cannot turn
        into one enormous write.
        """
        rate = self.args.flood
        if rate <= 0 or now < self.next_flood:
            return []
        owed = min(int((now - self.next_flood) * rate) + 1, FLOOD_MAX_BURST)
        self.next_flood += owed / rate
        out = []
        for _ in range(owed):
            self.flood_seq += 1
            out.append(f"flood line {self.flood_seq} payload=0123456789ABCDEF")
        return out

    def burst_debug(self) -> list[str]:
        """A short burst of debug lines, emitted right after any `gpio set`."""
        return [f"sim gpio-burst {i}" for i in range(3)]

    # -- plot streams (SPEC 2.5) ------------------------------------------------------

    def _poll_plot(self, now: float) -> list[str]:
        out: list[str] = []
        # Typed stream definitions: emit on first eligibility, then rebroadcast every 5 s.
        if now >= self.next_plot_def:
            if now - self.last_plot_def_broadcast >= 5.0 or self.last_plot_def_broadcast == 0.0:
                out.append("!pd 0 tri:s2*0.01:V ramp:u2 ftest:f4")
                out.append("!pd 1 state:u1:=0=IDLE,1=ARMED,2=RUN")
                out.append("!pd 2 gpio:u1:/led,irq,pwm_en")
                self.last_plot_def_broadcast = now
        # Samples at 20 Hz for both the ad-hoc and typed streams.
        beats, self.next_plot = _due_beats(now, self.next_plot, 0.05)
        for _ in range(beats):
            tick = self.state.tick_ms()
            phase = tick / 1000.0
            # Ad-hoc !p: sine and noisy (sine plus small deterministic wobble).
            sine = math.sin(phase * 2 * math.pi)
            noisy = sine + 0.05 * math.sin(phase * 37.0)
            out.append(f"!p {tick} sine={sine:.4f} noisy={noisy:.4f}")
            # Typed !ps samples always flow. With --plot-late-def the !pd above is held
            # back 5 s, so these early samples are undecodable at the consumer (SPEC 7).
            tri = int(2000 * _triangle(phase))          # s2, scaled by 0.01 -> +-20 V
            ramp = tick & 0xFFFF                          # u2
            ftest = math.sin(phase * 0.5 * 2 * math.pi)  # f4, slow sine
            packed = struct.pack("<hHf", _clip_s16(tri), ramp, ftest)
            out.append(_format_typed_sample("0", tick, packed, ("h", "H", "f")))
            # Enum state machine (stream 1): step 0->1->2->0 every ~1 s.
            state = (tick // 1000) % 3
            out.append(_format_typed_sample("1", tick, struct.pack("<B", state), ("B",)))
            # Packed bits (stream 2): led ~1 Hz, irq ~0.7 Hz, pwm_en fast.
            bits = ((tick // 500) & 1) | (((tick // 1500) & 1) << 1) | (((tick // 200) & 1) << 2)
            out.append(_format_typed_sample("2", tick, struct.pack("<B", bits), ("B",)))
        return out


# --- typed-sample encoding (SPEC 2.5) ------------------------------------------------

# Struct format char -> (byte width). Fields are packed little-endian on the wire from
# the MCU's struct, then re-emitted as big-endian fixed-width hex.
_FIELD_WIDTH = {"b": 1, "B": 1, "h": 2, "H": 2, "i": 4, "I": 4, "f": 4}


def _format_typed_sample(sid: str, tick: int, packed: bytes, fmt: tuple[str, ...]) -> str:
    """Encode one `!ps` line: big-endian fixed-width uppercase hex, comma separated.

    `packed` is a little-endian struct (as the MCU would hold it); each field is
    re-read and emitted big-endian, matching SPEC 2.5's natural reading order.
    """
    vals: list[str] = []
    off = 0
    for ch in fmt:
        width = _FIELD_WIDTH[ch]
        field_le = packed[off : off + width]
        off += width
        be = field_le[::-1]  # little-endian struct bytes reversed to big-endian
        vals.append(be.hex().upper())
    return f"!ps {sid} {tick:X} " + ",".join(vals)


def _triangle(phase: float) -> float:
    """A triangle wave in [-1, 1] with period 1.0."""
    frac = phase - math.floor(phase)
    return 4.0 * abs(frac - 0.5) - 1.0


def _clip_s16(v: int) -> int:
    return max(-32768, min(32767, v))


def _i2c_addr(text: str) -> int:
    # SPEC 2.4: a 7-bit address, 00 to 7F. Out of range is badarg, not the nack the
    # unbounded lookup used to answer - a typo is not the bus replying.
    addr = p.parse_hex_int(text)
    if addr > 0x7F:
        raise p.ProtocolError(f"i2c addr {addr:X} out of range")
    return addr


def _parse_dec(text: str, lo: int, hi: int) -> int:
    # is_decimal_token(), not isdecimal(): the latter accepts other scripts' digits, which
    # int() then converts, so the simulator answered a command no firmware would.
    if not p.is_decimal_token(text):
        raise p.ProtocolError(f"expected decimal in {lo}..{hi}")
    val = int(text)
    if not (lo <= val <= hi):
        raise p.ProtocolError(f"value {val} out of range {lo}..{hi}")
    return val


# --- connection serving (shared by both transports) ----------------------------------


def _recover_seq(line: str) -> int | None:
    """The seq of a line being rejected whole, so the error can still be addressed.

    monitor.c's recover_seq(): the first token survives the truncation, so an over-length
    command is still answerable. `line` carries its own sigil ('>' or '<').
    """
    try:
        return p.parse_seq_token(p.normalize_line(line)[1:].split(" ")[0])
    except p.ProtocolError:
        return None


def _process_incoming(sim: Simulator, rx: bytearray, chunk: bytes) -> list[str]:
    """Feed received bytes into the sim, returning the lines to send back.

    Line assembly matches monitor.c (SPEC 2.1/5.4): the buffer holds at most
    MAX_LINE_BYTES, and anything past that is discarded until the next LF, at which point
    an over-length command is answered `ERR 8 overflow` if its seq was recoverable and
    ignored otherwise. Without the cap a peer that never sends an LF grew `rx` without
    bound, which no firmware can do.

    Detects a `gpio set` command and appends the debug burst that SPEC 7 requires
    right after one, to exercise line interleaving.
    """
    out: list[str] = []
    segments = chunk.split(b"\n")
    for i, seg in enumerate(segments):
        # CR first, then the length test: monitor.c's assemble_one() drops \r before it
        # counts (it tolerates CRLF), so counting it here made a CRLF sender at exactly
        # MAX_LINE_BYTES get ERR 8 overflow from the sim and a normal answer from firmware.
        seg = seg.replace(b"\r", b"")
        room = p.MAX_LINE_BYTES - len(rx)
        if len(seg) > room:
            sim.rx_overflow = True
            seg = seg[:room]
        rx.extend(seg)
        if i == len(segments) - 1:
            break                      # no terminator yet: the rest waits for more bytes
        line = bytes(rx).decode("ascii", "replace")
        overflow, sim.rx_overflow = sim.rx_overflow, False
        rx.clear()
        if overflow:
            seq = _recover_seq(line) if line.startswith(">") else None
            if seq is not None:
                out.append(p.format_response_err(seq, p.ERROR_CODES["overflow"]))
            continue
        was_gpio_set = _is_gpio_set(line)
        out.extend(sim.handle_line(line))
        if was_gpio_set:
            out.extend(sim.burst_debug())
    return out


def _is_gpio_set(line: str) -> bool:
    """SPEC 7: the debug burst follows any `gpio set` command, sound or not.

    Token-exact rather than a `" gpio set "` substring test, which fired on any line
    carrying that text: `>7 mark gpio set led` is a marker command and drove no GPIO at all,
    yet it produced a burst the firmware contract does not put there.
    """
    try:
        cmd = p.parse_command(line)
    except p.ProtocolError:
        return False
    return cmd.tokens[:2] == ("gpio", "set")


# --- TCP transport (default, cross-platform) -----------------------------------------


def open_tcp_listener(port: int) -> socket.socket:
    """Bind a TCP listener on 127.0.0.1:port (port 0 picks an ephemeral port)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR means opposite things on the two platforms: on POSIX it only skips the
    # TIME_WAIT wait (which restart_sim genuinely needs), but on Windows it also lets a
    # second socket bind an address that is already actively listening. A second `mcu-sim`
    # on the default port would then start silently and never be connected to, where a
    # Linux user gets a loud EADDRINUSE. SO_EXCLUSIVEADDRUSE restores the refusal while
    # still permitting the legitimate rebind after a closed connection.
    if os.name == "nt":
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    return srv


# errno values that mean the descriptor the loop is built on is gone: the TCP listener in
# serve_listener, the pty master in serve_pty. Anything else (ECONNABORTED from a peer that
# resets between connect and accept, EMFILE/ENFILE under fd pressure) leaves it usable and
# must not end the loop.
_FD_DEAD_ERRNOS = frozenset(
    getattr(errno, name) for name in ("EBADF", "ENOTSOCK", "EINVAL", "WSAENOTSOCK")
    if hasattr(errno, name)
)

# How long to wait after a recoverable serving error before trying again: long enough
# that a persistent failure does not spin the CPU, short enough to be invisible.
ERROR_BACKOFF_S = 0.1


def serve_listener(
    args: argparse.Namespace,
    srv: socket.socket,
    stop: threading.Event | None = None,
) -> None:
    """Accept one client at a time and serve it, looping back to accept on disconnect.

    A serial port is point-to-point, so only one connection is served at once. When
    the daemon drops and reconnects, the next accept picks it up, which is what the
    phase 2 reconnect test exercises.
    """
    srv.settimeout(0.5)
    while stop is None or not stop.is_set():
        try:
            conn, _ = srv.accept()
        except TimeoutError:
            continue
        except OSError as exc:
            # Only a genuine shutdown ends the loop. Breaking on every OSError left the
            # listener bound and listening with no thread behind it: the kernel kept
            # completing handshakes from the backlog, so the daemon reconnected to a
            # corpse and reported the port healthy while no byte was ever exchanged
            # again. Same healthy-while-dead failure the client-session guard below fixes.
            if srv.fileno() == -1 or exc.errno in _FD_DEAD_ERRNOS:
                break
            print(f"mcu-sim: accept failed, retrying: {exc!r}", file=sys.stderr, flush=True)
            if stop is not None:
                stop.wait(ERROR_BACKOFF_S)
            else:
                time.sleep(ERROR_BACKOFF_S)
            continue
        # A bug in one client's session must not take the listener down with it. It
        # used to: an exception from poll_events unwound out of serve_listener and
        # killed the serving thread, but left `srv` open, so the OS kept completing
        # handshakes into the backlog and the daemon reconnected to a corpse and
        # reported the port healthy while nothing was ever read or written again.
        # The close is inside the guard for the same reason: `with conn:` let an OSError
        # from the implicit close() escape and kill the thread the guard just saved.
        try:
            _serve_socket_client(args, conn, stop)
        except Exception as exc:  # noqa: BLE001 - the listener must outlive any client
            print(f"mcu-sim: client session failed: {exc!r}", file=sys.stderr, flush=True)
        finally:
            try:
                conn.close()
            except OSError:
                pass


def _serve_socket_client(
    args: argparse.Namespace,
    conn: socket.socket,
    stop: threading.Event | None,
) -> None:
    sim = Simulator(args)
    conn.setblocking(False)
    rx = bytearray()
    while stop is None or not stop.is_set():
        readable, _, _ = select.select([conn], [], [], 0.01)
        if readable:
            try:
                chunk = conn.recv(4096)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                return
            if not chunk:
                return  # client closed the connection
            if not _sock_send_lines(conn, _process_incoming(sim, rx, chunk)):
                return
        if not _sock_send_lines(conn, sim.poll_events()):
            return


class RawJunk(str):
    """A deliberately non-conformant line: encode_lines must not sanitize it.

    Only the --garbage fault injector emits one; its whole job is putting bytes on the
    wire that a conformant firmware never would (SPEC 7), so the SPEC 2.2 sanitizer
    below must not repair it into a line the daemon has no trouble with.
    """


def _sanitize(line: str) -> str:
    """Replace every character outside printable ASCII with '.' (monitor.c write_line)."""
    return "".join(c if 0x20 <= ord(c) <= 0x7E else "." for c in line)


def encode_lines(lines: list[str]) -> bytes:
    """Encode a pass's output as 7-bit ASCII, LF-terminated, within SPEC 2.1's limits.

    A real monitor writes through a fixed TX buffer and physically cannot emit more than
    MAX_LINE_BYTES; the simulator must not be able to hand the host a line the protocol
    forbids either, so an oversized line is truncated the way full firmware buffer would
    truncate it, and the truncation is reported so it is not silent in development.

    A response is the exception (SPEC 2.3, monitor.c emit_ok): it is answered
    `ERR 8 overflow` instead, since a cut hex payload cannot be told from a short one.

    Every byte outside printable ASCII is replaced first, as monitor.c's write_line() does
    (SPEC 2.2). This is the one place every outgoing line passes through, so it covers the
    reflected payloads too: `>1 pi\\x00ng` came back carrying the caller's NUL.
    A `RawJunk` line (the --garbage injector) is the deliberate exception.
    """
    if not lines:
        return b""                     # an empty pass emits nothing, not a blank line
    out: list[str] = []
    for line in lines:
        if not isinstance(line, RawJunk):
            line = _sanitize(line)
        if p.is_oversized(line) and line.startswith("<"):
            seq = _recover_seq(line)
            if seq is not None:
                out.append(p.format_response_err(seq, p.ERROR_CODES["overflow"]))
                continue
        if p.is_oversized(line):
            print(
                f"mcu-sim: truncating a {len(line)}-char line to {p.MAX_LINE_BYTES} bytes",
                file=sys.stderr, flush=True,
            )
            line = line.encode("ascii", "replace")[: p.MAX_LINE_BYTES].decode("ascii")
        out.append(line)
    return ("\n".join(out) + "\n").encode("ascii", "replace")


# How long a send may make no progress before the reader is declared gone. Only a stall
# this long says nothing is draining: a full kernel send buffer on the nonblocking socket
# is an ordinary slow reader, and dropping the session for it reset every bit of simulated
# state on the next accept.
SEND_STALL_TIMEOUT_S = 5.0


def _sock_send_lines(conn: socket.socket, lines: list[str]) -> bool:
    """Write a whole pass's output. Returns False once the peer is gone.

    One send per pass, not per line: a syscall per line shows up as soon as the sim emits
    at any rate (`--flood`), and the lines are due at the same instant anyway.

    The socket is nonblocking, so a full send buffer answers BlockingIOError and a send can
    accept only part of the buffer. Both mean a live reader that is behind, the same reading
    the recv side already gives BlockingIOError. Resume from the unsent offset and never
    re-send an accepted byte, so no line is torn or duplicated, and wait for writability
    rather than spinning; only SEND_STALL_TIMEOUT_S with no byte accepted ends the session.
    """
    if not lines:
        return True
    buf = memoryview(encode_lines(lines))
    sent = 0
    deadline = time.monotonic() + SEND_STALL_TIMEOUT_S
    while sent < len(buf):
        try:
            sent += conn.send(buf[sent:])
            deadline = time.monotonic() + SEND_STALL_TIMEOUT_S   # progress resets the budget
            continue
        except (BlockingIOError, InterruptedError):
            pass
        except OSError:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            # Sliced so a stopping simulator is not stuck here for the whole budget.
            select.select([], [conn], [], min(remaining, 0.5))
        except OSError:
            return False
    return True


# --- in-process transport (no socket at all) -----------------------------------------


class SimSource:
    """The simulator as a byte source for `link.SourceLink`: no socket, no thread, no port.

    The same core the TCP listener serves, reached directly. A fresh Simulator each time,
    matching `_serve_socket_client`, so a reconnect finds a far end that restarted clean.
    """

    def __init__(self, args: argparse.Namespace | None = None) -> None:
        self.args = args if args is not None else build_parser().parse_args([])
        self.sim = Simulator(self.args)
        self._rx = bytearray()

    def feed(self, data: bytes) -> bytes:
        lines = _process_incoming(self.sim, self._rx, data)
        return encode_lines(lines) if lines else b""

    def poll(self) -> bytes:
        lines = self.sim.poll_events()
        return encode_lines(lines) if lines else b""


def open_sim_link(device: str = "sim://", baud: int = 115200, args=None):
    """A `link.open_link_fn` that hands back a simulator on the other end of the port."""
    from .link import SourceLink  # local: link.py must not depend on the simulator

    return SourceLink(SimSource(args), device=device)


@dataclass
class SimHandle:
    """A running in-process simulator: where to reach it, and how to stop it."""

    device: str                      # socket:// url, ready to hand to a port
    port: int
    _sock: socket.socket
    _thread: threading.Thread
    _stop: threading.Event

    def stop(self, timeout: float = 2.0) -> None:
        """Stop serving and release the listener. Safe to call twice."""
        self._stop.set()
        with contextlib.suppress(OSError):
            self._sock.close()
        self._thread.join(timeout=timeout)


def spawn(args: argparse.Namespace | None = None, port: int = 0) -> SimHandle:
    """Run the simulator on a background thread and return a handle to it.

    The listener is closed when the serving thread ends, in one place: a listener left
    bound with no thread behind it keeps completing handshakes out of the kernel backlog,
    so a client connects, sees a healthy port and never exchanges a byte.
    """
    if args is None:
        args = build_parser().parse_args([])
    stop = threading.Event()
    sock = open_tcp_listener(port)
    bound = sock.getsockname()[1]

    def serve() -> None:
        try:
            serve_listener(args, sock, stop)
        finally:
            with contextlib.suppress(OSError):
                sock.close()

    thread = threading.Thread(target=serve, name="mcu-sim", daemon=True)
    thread.start()
    return SimHandle(
        device=f"socket://127.0.0.1:{bound}", port=bound,
        _sock=sock, _thread=thread, _stop=stop,
    )


def serve_tcp(args: argparse.Namespace) -> int:
    srv = open_tcp_listener(args.tcp_port)
    port = srv.getsockname()[1]
    # The device string the daemon attaches to; also directly usable by test scripts.
    print(f"socket://127.0.0.1:{port}", flush=True)
    try:
        serve_listener(args, srv)
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()
    return 0


# --- pty transport (POSIX only, opt-in) ----------------------------------------------


def _pty_write_lines(master: int, lines: list[str], budget: float = SEND_STALL_TIMEOUT_S) -> bool:
    """Write a pass's output to a nonblocking pty master. False if the backlog was dropped.

    The same unsent-offset resume as _sock_send_lines, with one difference at the end of
    the budget: a socket peer that stops reading is gone and the session ends, while a pty
    slave with nothing attached is the documented `mcu-sim --pty` startup window. So the
    backlog is dropped (sim output is disposable) and the session keeps serving.

    Before this, one blocking os.write() wedged the serving thread for good once the
    slave's 4 kB input queue filled: the sim read nothing and polled nothing while its
    slave path stayed stat-able, so a daemon's presence check attached it to a corpse.
    """
    if not lines:
        return True
    buf = memoryview(encode_lines(lines))
    sent = 0
    deadline = time.monotonic() + budget
    while sent < len(buf):
        try:
            sent += os.write(master, buf[sent:])
            deadline = time.monotonic() + budget   # progress resets the budget
            continue
        except (BlockingIOError, InterruptedError):
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        # Sliced so a stopping simulator is not stuck here for the whole budget.
        select.select([], [master], [], min(remaining, 0.5))
    return True


def serve_pty(args: argparse.Namespace) -> int:
    if os.name == "nt":
        print("--pty requires a POSIX pty and is not available on Windows.", file=sys.stderr)
        return 2

    import pty  # POSIX only; imported here so the module still imports on Windows.
    import tty

    master, slave = pty.openpty()
    # openpty() leaves the slave canonical with ECHO/ICANON/OPOST on, so the line discipline
    # rewrote the sim's own output (\x7f is VERASE) and echoed everything back into the read
    # path. pyserial clears these when it opens the slave; nothing covered the window before
    # that, or a plain `cat` of the slave.
    tty.setraw(slave)
    # Writes must not block forever when nothing is draining the slave (see write_lines);
    # the read side handles the EAGAIN this also brings.
    os.set_blocking(master, False)
    slave_path = os.ttyname(slave)
    print(slave_path, flush=True)
    if args.symlink:
        _make_symlink(args.symlink, slave_path)

    sim = Simulator(args)
    rx = bytearray()

    def write_lines(lines: list[str]) -> None:
        _pty_write_lines(master, lines)

    try:
        while True:
            try:
                readable, _, _ = select.select([master], [], [], 0.01)
                if readable:
                    try:
                        chunk: bytes | None = os.read(master, 4096)
                    except BlockingIOError:
                        chunk = None   # spurious readability on the nonblocking master
                    except OSError:
                        break
                    if chunk is not None:
                        if not chunk:
                            break
                        write_lines(_process_incoming(sim, rx, chunk))
                write_lines(sim.poll_events())
            except Exception as exc:  # noqa: BLE001 - one session must not end the process
                # The TCP path has kept serving across a failed session since the
                # healthy-while-dead fix; this path had no guard at all, so the same
                # exception ended the process instead. Reset the session state, as
                # accepting a fresh client does over TCP, and keep serving.
                #
                # Same distinction the accept loop makes: a dead master is not a failed
                # session, and restarting on it spins at 1/ERROR_BACKOFF_S forever,
                # printing the same error, with no client and no way to get one.
                if isinstance(exc, OSError) and exc.errno in _FD_DEAD_ERRNOS:
                    print(f"mcu-sim: pty master is gone: {exc!r}", file=sys.stderr, flush=True)
                    break
                print(f"mcu-sim: session failed, restarting: {exc!r}", file=sys.stderr,
                      flush=True)
                sim = Simulator(args)
                rx = bytearray()
                time.sleep(ERROR_BACKOFF_S)
    except KeyboardInterrupt:
        pass
    finally:
        # Tolerate an already-dead descriptor: closing it raises EBADF, which would both
        # mask the reason we are here and leak the other half of the pair.
        for fd in (master, slave):
            with contextlib.suppress(OSError):
                os.close(fd)
        if args.symlink and os.path.islink(args.symlink):
            try:
                os.remove(args.symlink)
            except OSError:
                pass
    return 0


def _make_symlink(link: str, target: str) -> None:
    try:
        if os.path.islink(link) or os.path.exists(link):
            os.remove(link)
        os.symlink(target, link)
    except OSError as exc:
        print(f"warning: could not create symlink {link}: {exc}", file=sys.stderr)


# --- entry point ---------------------------------------------------------------------


def serve(args: argparse.Namespace) -> int:
    if args.pty:
        return serve_pty(args)
    return serve_tcp(args)


def _tcp_port_arg(text: str) -> int:
    """A TCP port for --tcp-port: 0..65535, refused as a usage error rather than a crash.

    Out of range reached bind() and raised OverflowError, which console_entry's backstop
    turned into a crash report for a typo.
    """
    try:
        port = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"must be 0..65535, got {port}")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcu_sim",
        description="Simulated MCU speaking the monitor protocol over TCP or a pty (SPEC 7).",
    )
    parser.add_argument(
        "--tcp-port",
        type=_tcp_port_arg,
        default=9900,
        metavar="PORT",
        help="TCP port to listen on (default 9900; 0 picks an ephemeral port).",
    )
    parser.add_argument(
        "--pty",
        action="store_true",
        help="POSIX only: serve over a pty instead of TCP, printing the slave path.",
    )
    parser.add_argument(
        "--symlink",
        metavar="PATH",
        help="With --pty: create a stable symlink to the pty slave path.",
    )
    parser.add_argument(
        "--drop-response",
        type=int,
        default=0,
        metavar="N",
        help="Swallow the response to the Nth command (exercises the timeout path).",
    )
    parser.add_argument(
        "--garbage",
        action="store_true",
        help="Occasionally emit binary junk lines.",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Emit ad-hoc !p and typed !pd/!ps plot streams at 20 Hz.",
    )
    parser.add_argument(
        "--plot-late-def",
        action="store_true",
        help="Delay the first !pd by 5 s (tests the undecodable-sample path).",
    )
    parser.add_argument(
        "--flood",
        type=int,
        default=0,
        metavar="LINES_PER_S",
        help="Emit this many extra debug lines per second (0 = off). For load testing "
        "the capture path and the web UI's high-rate behaviour.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return serve(args)


def console_entry() -> int:
    """Console-script entry: repaired std streams plus a crash-file backstop."""
    from . import _stdio

    return _stdio.console_entry(main, "mcu-sim")


if __name__ == "__main__":
    raise SystemExit(console_entry())
