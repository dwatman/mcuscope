"""Pure encode/decode of the UART line protocol (SPEC section 2). No I/O here.

This module is shared by the daemon (which classifies and parses lines arriving from
the MCU, and formats the commands it sends) and by the MCU simulator (which parses
commands and formats responses/events), so both sides speak one implementation of the
wire protocol.

Conventions used throughout:

- Line bodies never include the trailing LF. The I/O layer adds the terminator, and
  the store keeps `raw` with the terminator stripped (SPEC 3.5).
- Hex payloads are pairs of hex digits, no separators, no `0x` prefix. Encoders emit
  uppercase; decoders accept either case.
- Functions that decode attacker/hardware-controlled input either raise `ProtocolError`
  (for the seq/response machinery, where a malformed line is a real fault) or return
  `None` (for `!can` events, where SPEC 3.5 requires storing the line as a generic
  event rather than failing).
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass
from enum import Enum
from typing import Any

# --- constants (SPEC 2.1, 2.3) -------------------------------------------------------

PROTO_VERSION = 1

# Maximum bytes of line *content*, both directions, excluding the LF terminator
# (SPEC 2.1: "255 bytes of content plus the LF terminator, 256 bytes total on the wire").
MAX_LINE_BYTES = 255

# Line ending appended to an outgoing line. The monitor expects LF (SPEC 2.1) and tolerates
# CRLF, so those are the two terminators; "none" sends the body alone, which is what a bare
# control character (Ctrl-C, magic SysRq) needs and what a terminator would corrupt.
EOL_BYTES: dict[str, bytes] = {"none": b"", "lf": b"\n", "crlf": b"\r\n"}
DEFAULT_EOL = "lf"

# Fixed shared error table (SPEC 2.3). Keep code -> name and name -> code in sync.
ERROR_NAMES: dict[int, str] = {
    1: "badcmd",
    2: "badarg",
    3: "timeout",
    4: "buserr",
    5: "nack",
    6: "busy",
    7: "nosup",
    8: "overflow",
    9: "internal",
}
ERROR_CODES: dict[str, int] = {name: code for code, name in ERROR_NAMES.items()}


class ProtocolError(ValueError):
    """A line that should have parsed as a command or response did not."""


class LineClass(str, Enum):
    """Classification of a line by its first character (SPEC 2.2)."""

    # (str, Enum) + __str__ is StrEnum for 3.10, where enum.StrEnum does not exist.
    def __str__(self) -> str:
        return self.value

    COMMAND = "cmd"    # '>' PC to MCU (we send these; not expected on RX)
    RESPONSE = "resp"  # '<' MCU to PC, response to a command
    EVENT = "event"    # '!' MCU to PC, asynchronous event
    DEBUG = "debug"    # anything else, normal application output


# --- line hygiene --------------------------------------------------------------------


def normalize_line(raw: str) -> str:
    """Strip a single trailing CRLF/LF/CR pair, tolerating a preceding CR (SPEC 2.1).

    Exactly one terminator: rstrip("\\r\\n") ate every trailing CR and LF, so a payload
    ending in blank lines lost them rather than the one the transport added.
    """
    if raw.endswith("\r\n"):
        return raw[:-2]
    if raw.endswith(("\n", "\r")):
        return raw[:-1]
    return raw


def _check_no_break(text: str, what: str) -> None:
    """Raise if `text` carries a CR or LF, which would forge a second wire line.

    Same class as format_marker's tick-sigil refusal: an emitter that lets a terminator
    through does not produce a bad line, it produces two lines nobody asked for.
    """
    if "\n" in text or "\r" in text:
        raise ProtocolError(f"{what} contains a line break: {text!r}")


def is_oversized(body: str) -> bool:
    """True if `body` exceeds the 255-byte content limit (the LF terminator is extra).

    The `+ 1` this used to carry made the effective limit 254 content bytes, so the host
    refused to send a maximal line that SPEC 2.1 and the firmware (monitor.c accepts while
    `g_line_len < MONITOR_LINE_MAX`, 255) both allow.
    """
    return len(body.encode("ascii", "replace")) > MAX_LINE_BYTES


def classify(raw: str) -> LineClass:
    """Classify a received line by its first character (SPEC 2.2).

    The line may still carry its terminator; it is normalized first. An empty line is
    treated as debug output.
    """
    line = normalize_line(raw)
    if not line:
        return LineClass.DEBUG
    first = line[0]
    if first == ">":
        return LineClass.COMMAND
    if first == "<":
        return LineClass.RESPONSE
    if first == "!":
        return LineClass.EVENT
    return LineClass.DEBUG


# --- hex helpers (SPEC 2.1) ----------------------------------------------------------


def bytes_to_hex(data: bytes | bytearray) -> str:
    """Encode bytes as uppercase hex pairs with no separators."""
    return data.hex().upper()


def hex_to_bytes(text: str) -> bytes:
    """Decode hex pairs to bytes. Raises ProtocolError on odd length or bad digits."""
    if len(text) % 2 != 0:
        raise ProtocolError(f"hex payload has odd length: {text!r}")
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise ProtocolError(f"invalid hex payload: {text!r}") from exc


def parse_hex_int(text: str) -> int:
    """Parse a hex integer, tolerating an optional `0x`/`0X` prefix (SPEC 3.4).

    Capped at 16 hex digits (64 bits) to bound the work int() does on a hostile token; it
    is not a range check, since 16 digits still exceed what an SQLite INTEGER bind takes.
    Every caller range-checks the value it gets back.
    """
    t = text[2:] if text[:2] in ("0x", "0X") else text
    if not t or len(t) > 16 or any(c not in "0123456789abcdefABCDEF" for c in t):
        raise ProtocolError(f"invalid hex integer: {text!r}")
    return int(t, 16)


# CAN id ranges (SPEC 2.4): 11-bit standard, 29-bit extended.
CAN_ID_MAX_STD = 0x7FF
CAN_ID_MAX_EXT = 0x1FFFFFFF

# MCU tick counters are 32-bit milliseconds on the wire (SPEC 2.4, 2.5).
TICK_MS_MAX = 0xFFFFFFFF


# --- decimal tokens ------------------------------------------------------------------

# CPython refuses int() on a decimal string longer than sys.get_int_max_str_digits()
# (4300 by default) and raises a bare ValueError doing it: not a ProtocolError, and not
# the None the event decoders are documented to return. Every decimal token on the wire is
# bounded far below that (a 32-bit tick is 10 digits), so the length is gated before every
# int() rather than left to a caller's except clause. SPEC 2.1 caps a line at 255 bytes, so
# a conforming target cannot produce such a token; a misbehaving one can.
MAX_DECIMAL_DIGITS = 20   # past 64 bits, nowhere near CPython's conversion limit


def is_decimal_token(token: str, max_digits: int = MAX_DECIMAL_DIGITS) -> bool:
    """True if `token` is a plain ASCII decimal integer short enough for int() to convert.

    Bare `str.isdecimal()` falls short on both sides: it accepts other scripts' digits,
    which int() then happily converts (`٤` -> 4), and it bounds the length not at all.
    """
    return token.isascii() and token.isdecimal() and len(token) <= max_digits


# --- sequence numbers (SPEC 2.3) -----------------------------------------------------

SEQ_MIN = 1
SEQ_MAX = 65535


def _check_seq(seq: int) -> None:
    """Raise unless `seq` is a legal wire sequence number (SPEC 2.3)."""
    if not (SEQ_MIN <= seq <= SEQ_MAX):
        raise ProtocolError(f"seq out of range: {seq}")


def parse_seq_token(token: str) -> int:
    """Parse a wire seq token strictly: ASCII decimal digits only, in range (SPEC 2.3).

    Bare int() is far more permissive than the wire grammar: it accepts PEP-515 digit
    grouping, a leading sign, surrounding whitespace and non-ASCII decimal digits, so a
    garbled `<+17 OK` or `<1_7 OK` resolved the pending command for seq 17 instead of
    being rejected as malformed. It also raises on an absurdly long token, which
    is_decimal_token screens out so this stays a ProtocolError.
    """
    if not is_decimal_token(token):
        raise ProtocolError(f"bad seq token: {token!r}")
    seq = int(token)
    _check_seq(seq)
    return seq


def next_seq(seq: int) -> int:
    """Return the seq after `seq`: 1..65535, wrapping 65535 -> 1, never 0 (SPEC 2.3)."""
    nxt = seq + 1
    if nxt > SEQ_MAX or nxt < SEQ_MIN:
        return SEQ_MIN
    return nxt


# --- commands (SPEC 2.3) -------------------------------------------------------------

# SPEC 2.3/5.4: at most 12 tokens including the seq.
MAX_COMMAND_TOKENS = 12


@dataclass(frozen=True)
class Command:
    """A parsed `>SEQ CMD [ARGS...]` command line."""

    seq: int
    tokens: tuple[str, ...]  # CMD and its arguments, seq removed

    @property
    def name(self) -> str:
        return self.tokens[0] if self.tokens else ""


def split_tokens(body: str) -> list[str]:
    """Split a line body into tokens exactly as monitor.c's tokenize() does.

    The wire grammar (SPEC 2.1) separates tokens with spaces and nothing else, so a tab or
    a vertical tab is an ordinary token byte. str.split() splits on all whitespace, which
    made the host and the simulator execute `>1 gpio\\tset\\tled\\t1` that the reference
    firmware answers `ERR 1 badcmd`. Runs of spaces collapse and leading spaces are skipped,
    as tokenize does; _recover_seq stays stricter, matching monitor.c's recover_seq().
    """
    return [tok for tok in body.split(" ") if tok]


def format_command(seq: int, cmd: str) -> str:
    """Build a `>SEQ CMD ...` line body (no terminator). `cmd` is text without seq."""
    _check_seq(seq)
    cmd = cmd.strip()
    if not cmd:
        raise ProtocolError("empty command")
    _check_no_break(cmd, "command")
    # SPEC 2.3 caps a command at 12 tokens including the seq, and both reference receivers
    # enforce it; enforcing it on send turns a guaranteed `ERR 2 badarg` round trip into a
    # local refusal.
    ntok = 1 + len(split_tokens(cmd))
    if ntok > MAX_COMMAND_TOKENS:
        raise ProtocolError(f"command has {ntok} tokens, over the {MAX_COMMAND_TOKENS} cap")
    return f">{seq} {cmd}"


def parse_command(raw: str) -> Command:
    """Parse a `>SEQ CMD [ARGS...]` line. Raises ProtocolError if it is not one."""
    line = normalize_line(raw)
    if not line.startswith(">"):
        raise ProtocolError(f"not a command line: {raw!r}")
    parts = split_tokens(line[1:])
    if len(parts) < 2:
        raise ProtocolError(f"command missing seq or name: {raw!r}")
    seq = parse_seq_token(parts[0])
    return Command(seq=seq, tokens=tuple(parts[1:]))


# --- responses (SPEC 2.3) ------------------------------------------------------------


@dataclass(frozen=True)
class Response:
    """A parsed `<SEQ OK ...` or `<SEQ ERR CODE NAME ...` response line."""

    seq: int
    ok: bool
    data: str = ""                    # tokens after OK, joined by single spaces
    err_code: int | None = None
    err_name: str | None = None
    err_detail: str = ""


def format_response_ok(seq: int, data: str = "") -> str:
    """Build a `<SEQ OK [data]` line body (no terminator).

    The seq check is hardening, not a fix for a live defect: every caller today takes its
    seq from parse_command, which already validated it. It is here because format_command
    has always checked and these did not, so `format_response_ok(0)` emitted `<0 OK`, a
    line the module's own parse_response rejects.
    """
    _check_seq(seq)
    data = data.strip()
    _check_no_break(data, "response data")
    return f"<{seq} OK {data}".rstrip() if data else f"<{seq} OK"


def format_response_err(seq: int, code: int, detail: str = "") -> str:
    """Build a `<SEQ ERR CODE NAME [detail]` line body (no terminator)."""
    _check_seq(seq)
    name = ERROR_NAMES.get(code)
    if name is None:
        raise ProtocolError(f"unknown error code: {code}")
    detail = detail.strip()
    _check_no_break(detail, "error detail")
    base = f"<{seq} ERR {code} {name}"
    return f"{base} {detail}" if detail else base


def parse_response(raw: str) -> Response:
    """Parse a response line. Raises ProtocolError if the shape is not recognized."""
    line = normalize_line(raw)
    if not line.startswith("<"):
        raise ProtocolError(f"not a response line: {raw!r}")
    parts = line[1:].split()
    if len(parts) < 2:
        raise ProtocolError(f"response too short: {raw!r}")
    seq = parse_seq_token(parts[0])
    kind = parts[1]
    if kind == "OK":
        return Response(seq=seq, ok=True, data=" ".join(parts[2:]))
    if kind == "ERR":
        if len(parts) < 4:
            raise ProtocolError(f"ERR response missing code/name: {raw!r}")
        # Same ASCII-decimal gate as the seq token, and for the same reason: bare int()
        # accepted `+4`, `1_0`, `-3` and other scripts' digits, so a garbled ERR line was
        # reported to the user under a plausible but wrong error code.
        if not is_decimal_token(parts[2]):
            raise ProtocolError(f"bad error code token: {parts[2]!r}")
        code = int(parts[2])
        return Response(
            seq=seq,
            ok=False,
            err_code=code,
            err_name=parts[3],
            err_detail=" ".join(parts[4:]),
        )
    raise ProtocolError(f"response is neither OK nor ERR: {raw!r}")


# --- CAN frames and events (SPEC 2.4, 2.5) -------------------------------------------


@dataclass
class CanFrame:
    """A CAN classic frame, used for both `can tx` args and `!can` events.

    For data frames, `data` holds the payload and `dlc` equals its length. For RTR
    frames, `data` is empty and `dlc` is the requested length (0..8), matching the
    single decimal DLC digit carried on the wire.
    """

    can_id: int
    data: bytes = b""
    ext: bool = False
    rtr: bool = False
    dlc: int = 0
    tick_ms: int | None = None
    bus: int = 1   # SPEC 2.4 bus digit, 1..9; bus 1 is unmarked on the wire

    def __post_init__(self) -> None:
        if not self.rtr:
            self.dlc = len(self.data)


CAN_BUS_MIN = 1
CAN_BUS_MAX = 9


def format_can_family(bus: int, prefix: str = "can") -> str:
    """The family token for a bus: `can` for bus 1, `can2`..`can9` otherwise (SPEC 2.4).

    `prefix` is `!can` for the event name, which follows the same rule.
    """
    if not CAN_BUS_MIN <= bus <= CAN_BUS_MAX:
        raise ProtocolError(f"can bus out of range {CAN_BUS_MIN}..{CAN_BUS_MAX}: {bus}")
    return prefix if bus == 1 else f"{prefix}{bus}"


def parse_can_family(token: str, prefix: str = "can") -> int | None:
    """The bus a family token selects, or None if it is not one.

    `can` and `can1` are both bus 1; `can2`..`can9` select that bus. `can0` is None: a
    receiver answers badarg to it, but it is not a bus and this function does not name one.
    """
    if token == prefix:
        return 1
    if len(token) == len(prefix) + 1 and token.startswith(prefix) and token[-1] in "123456789":
        return int(token[-1])
    return None


def format_can_flags(ext: bool, rtr: bool) -> str:
    """Build the `<flags>` token: `-` for none, else a run of `x` and/or `r`."""
    token = ("x" if ext else "") + ("r" if rtr else "")
    return token or "-"


def parse_can_flags(token: str) -> tuple[bool, bool]:
    """Parse a `<flags>` token into (ext, rtr). Raises ProtocolError on unknown chars."""
    if token == "-":
        return (False, False)
    if not token or any(c not in "xr" for c in token):
        raise ProtocolError(f"invalid flags token: {token!r}")
    return ("x" in token, "r" in token)


def format_can_event(frame: CanFrame) -> str:
    """Format an `!can <tick> <flags> <id> <data|->` event line body (no terminator)."""
    if frame.tick_ms is None:
        raise ProtocolError("can event requires tick_ms")
    # Refuse ids parse_can_event would reject, so format and parse accept the same set.
    # Without this the two were asymmetric and a caller could emit a line its own decoder
    # throws away: the simulator's `can tx` echo adds 1 to the id, so `can tx 7FF` produced
    # `!can ... 800 ...`, stored as a generic event with no can_frames row.
    limit = CAN_ID_MAX_EXT if frame.ext else CAN_ID_MAX_STD
    if not 0 <= frame.can_id <= limit:
        raise ProtocolError(
            f"can id {frame.can_id:X} out of range for "
            f"{'extended' if frame.ext else 'standard'} frame"
        )
    # The other three fields, for the same symmetry. Hardening rather than a live defect:
    # the only caller is the simulator, which masks its tick and takes dlc and data from
    # parse_can_tx_args, which already bounds both.
    if not 0 <= frame.tick_ms <= TICK_MS_MAX:
        raise ProtocolError(f"can tick_ms out of range: {frame.tick_ms}")
    if not 0 <= frame.dlc <= 8:
        raise ProtocolError(f"can dlc out of range 0..8: {frame.dlc}")
    if len(frame.data) > 8:
        raise ProtocolError(f"can payload longer than 8 bytes: {len(frame.data)}")
    # An RTR frame carries a DLC and no payload on the wire, so data here would be dropped
    # silently; every other inconsistency in this function raises rather than lose data.
    if frame.rtr and frame.data:
        raise ProtocolError(f"rtr can frame carries a {len(frame.data)}-byte payload")
    name = format_can_family(frame.bus, "!can")
    flags = format_can_flags(frame.ext, frame.rtr)
    can_id = format_can_id(frame.can_id)
    if frame.rtr:
        payload = str(frame.dlc)
    elif frame.data:
        payload = bytes_to_hex(frame.data)
    else:
        payload = "-"
    return f"{name} {frame.tick_ms} {flags} {can_id} {payload}"


def parse_can_event(raw: str) -> CanFrame | None:
    """Decode an `!can` event line.

    Returns a CanFrame, or None if the line is malformed. SPEC 3.5 requires a bad
    `!can` line to still be stored as a generic event rather than raising, so callers
    use the None to mean "store as generic event, skip can_frames".
    """
    line = normalize_line(raw)
    parts = line.split()
    if len(parts) != 5:
        return None
    bus = parse_can_family(parts[0], "!can")
    if bus is None:
        return None
    _, tick_s, flags_s, id_s, payload_s = parts
    try:
        # is_decimal_token(), not isdigit() or even isdecimal(): those are true for
        # characters int() rejects (superscripts) or silently converts (other scripts'
        # digits), and neither bounds the length. The except below catches it too, belt
        # and braces.
        if not is_decimal_token(tick_s):
            return None
        tick = int(tick_s)
        if tick > TICK_MS_MAX:
            return None
        ext, rtr = parse_can_flags(flags_s)
        can_id = parse_hex_int(id_s)
        if can_id > (CAN_ID_MAX_EXT if ext else CAN_ID_MAX_STD):
            return None
        if rtr:
            # The ASCII set, not isdecimal(): the same rule the sid and tick tokens follow.
            # `'٣'.isdecimal()` is true and `int()` converts it to 3, so a garbled line
            # decoded into a can_frames row instead of being kept as a generic event.
            if len(payload_s) != 1 or payload_s not in "0123456789":
                return None
            dlc = int(payload_s)
            if dlc > 8:
                return None
            return CanFrame(can_id=can_id, ext=ext, rtr=True, dlc=dlc, tick_ms=tick, bus=bus)
        if payload_s == "-":
            data = b""
        else:
            data = hex_to_bytes(payload_s)
        if len(data) > 8:
            return None
        return CanFrame(can_id=can_id, data=data, ext=ext, rtr=False, tick_ms=tick, bus=bus)
    except (ProtocolError, ValueError):
        return None


def format_can_id(can_id: int) -> str:
    """Format a CAN id as bare uppercase hex, no `0x` prefix (SPEC 2.1)."""
    if can_id < 0:
        raise ProtocolError(f"negative can id: {can_id}")
    return f"{can_id:X}"


# --- can tx command argument parsing (SPEC 2.4) --------------------------------------


def parse_can_tx_args(args: tuple[str, ...] | list[str]) -> CanFrame:
    """Parse the arguments after `can tx` into a CanFrame (SPEC 2.4).

    `<id>` hex, `<data>` hex pairs (0..8 bytes) or `-` for zero length. Optional
    `flags` token may contain `x` (extended id) and/or `r` (RTR); for RTR the data
    token is a single decimal digit giving the DLC. Raises ProtocolError on bad args.
    """
    if len(args) < 2 or len(args) > 3:
        raise ProtocolError("can tx expects <id> <data|-> [flags]")
    can_id = parse_hex_int(args[0])
    data_tok = args[1]
    ext = rtr = False
    if len(args) == 3:
        ext, rtr = parse_can_flags(args[2])
    max_id = CAN_ID_MAX_EXT if ext else CAN_ID_MAX_STD
    if can_id > max_id:
        raise ProtocolError(f"can id out of range (max {max_id:X})")
    if rtr:
        if len(data_tok) != 1 or data_tok not in "0123456789":
            raise ProtocolError("rtr frame needs a single decimal DLC digit")
        dlc = int(data_tok)
        if dlc > 8:
            raise ProtocolError("dlc out of range 0..8")
        return CanFrame(can_id=can_id, ext=ext, rtr=True, dlc=dlc)
    data = b"" if data_tok == "-" else hex_to_bytes(data_tok)
    if len(data) > 8:
        raise ProtocolError("can payload longer than 8 bytes")
    return CanFrame(can_id=can_id, data=data, ext=ext, rtr=False)


# --- plot data (SPEC 2.5) ------------------------------------------------------------
#
# Two formats share one downstream pipeline: ad-hoc `!p` name=value pairs, and typed
# streams (`!pd` definition, `!ps` samples decoded against the latest def per sid).
# Every decoder returns None on a malformed line so the daemon stores it as a generic
# event rather than raising (mirrors parse_can_event).

# type token -> (byte width, signed, is_float). Widths are the on-wire hex field's
# byte count; the hex token itself is exactly twice as many characters (zero-padded).
_PLOT_TYPES: dict[str, tuple[int, bool, bool]] = {
    "u1": (1, False, False),
    "s1": (1, True, False),
    "u2": (2, False, False),
    "s2": (2, True, False),
    "u4": (4, False, False),
    "s4": (4, True, False),
    "f4": (4, False, True),
}

# Channel name grammar (SPEC 2.5): letter/underscore lead, then word chars or dot,
# at most 16 characters total.
_PLOT_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_MAX_PLOT_NAME = 16

# Enum label grammar (SPEC 2.5): word chars and dots, at most 16 characters.
_LABEL_RE = re.compile(r"[A-Za-z0-9_.]{1,16}")
# Enum value grammar (SPEC 2.5): a plain decimal integer, optional leading '-'. Kept
# strict (no '+', no '_' digit grouping that int(_, 10) would otherwise accept) so the
# host and the web UI's parser agree on exactly which defs are valid. [0-9] rather than
# \d, which also matches non-ASCII decimal digits that JavaScript's parser rejects.
_ENUM_VAL_RE = re.compile(r"-?[0-9]+")
# Sample tick grammar (SPEC 2.5): bare fixed-width hex, no '0x'/'+'/'_' that int(_, 16)
# would tolerate; matches the web UI decoder.
_TICK_HEX_RE = re.compile(r"[0-9a-fA-F]+")
# Plot value / scale grammar (SPEC 2.5): optional sign, digits, optional fraction, optional
# decimal exponent. See parse_plot_value for why the exponent is accepted.
_PLOT_VALUE_RE = re.compile(r"-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?")
_ENUM_TYPES = frozenset({"u1", "s1", "u2", "s2", "u4", "s4"})
_BITS_TYPES = frozenset({"u1", "u2", "u4"})


@dataclass(frozen=True)
class PlotChannel:
    """One channel within a typed stream definition (`!pd`)."""

    name: str
    type: str
    scale: float | None = None
    unit: str | None = None
    kind: str = "analog"                               # "analog" | "enum" | "bits"
    labels: tuple[tuple[int, str], ...] | None = None  # enum: (value, label) pairs
    lanes: tuple[str | None, ...] | None = None        # bits: LSB-first lane names


@dataclass(frozen=True)
class PlotDef:
    """A typed-stream definition (`!pd`): an ordered list of channels keyed by sid."""

    sid: str
    channels: tuple[PlotChannel, ...]


@dataclass(frozen=True)
class PlotSample:
    """One decoded plot line: a tick and its (name, scaled float value) points.

    `sid` is None for ad-hoc `!p` lines, else the typed stream's id.
    """

    tick_ms: int
    sid: str | None
    points: tuple[tuple[str, float], ...]


def _valid_plot_name(name: str) -> bool:
    return len(name) <= _MAX_PLOT_NAME and _PLOT_NAME_RE.fullmatch(name) is not None


def parse_plot_value(text: str) -> float | None:
    """Parse an ad-hoc `!p` value or a `*<scale>` factor (SPEC 2.5), or None if malformed.

    Scientific notation is accepted because firmware that does have float printf emits it
    unprompted: `%g` prints `1.2e-05` on its own, and rejecting the exponent dropped the
    whole line from plotting rather than just that value. It also spells a small scale
    factor legibly (`*9.8e-4` over `*0.00098`).

    Kept strict either side of that: no leading `+`, no bare `.5`, no `inf`/`nan` spellings,
    matching the web UI's parser (`plots.js parsePlotValue`).
    """
    if not _PLOT_VALUE_RE.fullmatch(text):
        return None
    value = float(text)
    # An in-grammar literal can still overflow to infinity ("1e999"). Storing that would
    # poison the channel's whole y range, so treat it as malformed like any other bad token.
    if not math.isfinite(value):
        return None
    return value


def parse_plot_adhoc(raw: str) -> PlotSample | None:
    """Decode an ad-hoc `!p <tick> name=value ...` line, or None if malformed."""
    parts = normalize_line(raw).split()
    if len(parts) < 3 or parts[0] != "!p":
        return None
    tick_s = parts[1]
    if not is_decimal_token(tick_s):
        return None
    tick = int(tick_s)
    if tick > TICK_MS_MAX:
        return None
    points: list[tuple[str, float]] = []
    seen: set[str] = set()
    for pair in parts[2:]:
        name, sep, value_s = pair.partition("=")
        if not sep or not _valid_plot_name(name):
            return None
        # SPEC 2.5: names are unique within one line. Two writers for one name in one sample
        # cannot be stored or charted coherently (the web UI pushes both into a single chart's
        # y array, one x apart), so the whole line is malformed.
        if name in seen:
            return None
        seen.add(name)
        value = parse_plot_value(value_s)
        if value is None:
            return None
        points.append((name, value))
    if not points:
        return None
    return PlotSample(tick_ms=tick, sid=None, points=tuple(points))


def parse_plot_def(raw: str) -> PlotDef | None:
    """Decode a typed-stream definition `!pd <sid> <name>:<type>[*scale][:unit] ...`.

    Returns None on any malformation so the caller stores it as a generic event.
    """
    parts = normalize_line(raw).split()
    if len(parts) < 3 or parts[0] != "!pd":
        return None
    sid = parts[1]
    # SPEC 2.5 says a single ASCII digit, so test the ASCII set rather than isdigit()/
    # isdecimal(), both of which accept other scripts' digits.
    if len(sid) != 1 or sid not in "0123456789":
        return None
    channels: list[PlotChannel] = []
    # SPEC 2.5: every emitted name in one definition is unique, channel names and bit lane
    # names in one namespace. A lane sharing an analog channel's name reclassifies that
    # channel's points as digital, and the web UI's name index is last-writer-wins.
    seen: set[str] = set()
    for spec in parts[2:]:
        chan = _parse_channel_spec(spec)
        if chan is None:
            return None
        emitted = [chan.name]
        if chan.lanes:
            emitted.extend(x for x in chan.lanes if x is not None)
        for name in emitted:
            if name in seen:
                return None
            seen.add(name)
        channels.append(chan)
    if not channels:
        return None
    return PlotDef(sid=sid, channels=tuple(channels))


def _parse_enum_labels(body: str, signed: bool) -> tuple[tuple[int, str], ...] | None:
    """Parse `v=label,v=label,...` into (value, label) pairs, or None if malformed."""
    pairs: list[tuple[int, str]] = []
    for item in body.split(","):
        val_s, sep, label = item.partition("=")
        if not sep or not _LABEL_RE.fullmatch(label) or not _ENUM_VAL_RE.fullmatch(val_s):
            return None
        # The regex bounds the character set but not the length, and int() raises on a
        # token past CPython's digit limit (see is_decimal_token).
        if len(val_s.removeprefix("-")) > MAX_DECIMAL_DIGITS:
            return None
        # The sign, not the value: monitor.c rejects any '-' on an unsigned channel, so
        # host-side `-0=LABEL` would otherwise be accepted where the firmware refuses it.
        if not signed and val_s.startswith("-"):
            return None
        val = int(val_s, 10)
        pairs.append((val, label))
    return tuple(pairs) if pairs else None


def _parse_bit_lanes(body: str, width: int) -> tuple[str | None, ...] | None:
    """Parse `lane,lane,,lane` into LSB-first names (None = skipped bit), or None."""
    lanes: list[str | None] = [item if item != "" else None for item in body.split(",")]
    if any(x is not None and not _valid_plot_name(x) for x in lanes):
        return None
    if not lanes or len(lanes) > width * 8 or all(x is None for x in lanes):
        return None
    return tuple(lanes)


def _parse_channel_spec(spec: str) -> PlotChannel | None:
    """Parse one `<name>:<type>[*<scale>][:<unit>]` channel spec, or None if malformed.

    The `<unit>` slot may instead carry an enum (`=v=label,...`) or packed-bits
    (`/lane,lane,...`) sigil, selecting `kind="enum"`/`"bits"` (SPEC 2.5).
    """
    fields = spec.split(":")
    if len(fields) < 2 or len(fields) > 3:
        return None
    name, type_spec = fields[0], fields[1]
    unit = fields[2] if len(fields) == 3 else None
    if not _valid_plot_name(name):
        return None
    type_tok, star, scale_s = type_spec.partition("*")
    if type_tok not in _PLOT_TYPES:
        return None
    scale: float | None = None
    if star:
        parsed = parse_plot_value(scale_s)
        if parsed is None:
            return None
        scale = parsed
    if unit is not None and unit == "":
        return None
    kind, labels, lanes = "analog", None, None
    if unit is not None and unit[0] in "=/":
        if scale is not None:
            return None  # a *scale is meaningless on an enum/bits channel; reject, do not drop it
        width, signed, _ = _PLOT_TYPES[type_tok]
        if unit[0] == "=":
            if type_tok not in _ENUM_TYPES:
                return None
            labels = _parse_enum_labels(unit[1:], signed)
            if labels is None:
                return None
            kind = "enum"
        else:  # "/" -> packed bits
            if type_tok not in _BITS_TYPES:
                return None
            lanes = _parse_bit_lanes(unit[1:], width)
            if lanes is None:
                return None
            kind = "bits"
        unit = None  # the sigil consumed the unit slot; it is not a display unit
    return PlotChannel(name=name, type=type_tok, scale=scale, unit=unit,
                        kind=kind, labels=labels, lanes=lanes)


def _decode_field(hex_tok: str, type_tok: str) -> float | None:
    """Decode one big-endian fixed-width hex field to a float, or None if malformed."""
    width, signed, is_float = _PLOT_TYPES[type_tok]
    if len(hex_tok) != width * 2:
        return None
    try:
        raw = bytes.fromhex(hex_tok)
    except ValueError:
        return None
    if is_float:
        value = float(struct.unpack(">f", raw)[0])
        # 7F800000 / FF800000 / 7FC00000 are what an uninitialised float or a 0.0/0.0 holds,
        # and the typed path used to accept all three where parse_plot_value refuses the
        # same value spelled `1e999`. NaN then broke the store's NOT NULL bind and Inf made
        # GET /plot/series render a 500 for the channel's whole window. None here routes the
        # line to a generic event, exactly as a width mismatch does. plots.js:195 mirrors it.
        return value if math.isfinite(value) else None
    return float(int.from_bytes(raw, "big", signed=signed))


def decode_plot_sample(raw: str, definition: PlotDef) -> PlotSample | None:
    """Decode a typed sample `!ps <sid> <tick> v,v,...` against `definition`.

    Returns None if the line is malformed, the sid does not match, or the value count
    or field width disagrees with the definition, so it is stored as a generic event.
    """
    parts = normalize_line(raw).split()
    if len(parts) != 4 or parts[0] != "!ps":
        return None
    sid, tick_s, values_s = parts[1], parts[2], parts[3]
    if sid != definition.sid:
        return None
    if not _TICK_HEX_RE.fullmatch(tick_s):
        return None
    # Base 16 is exempt from CPython's int() digit limit, so no length gate is needed here.
    tick = int(tick_s, 16)
    if tick > TICK_MS_MAX:
        return None
    values = values_s.split(",")
    if len(values) != len(definition.channels):
        return None
    points: list[tuple[str, float]] = []
    for hex_tok, chan in zip(values, definition.channels, strict=True):
        decoded = _decode_field(hex_tok, chan.type)
        if decoded is None:
            return None
        if chan.kind == "bits":
            bits = int(decoded)
            for i, lane in enumerate(chan.lanes or ()):
                if lane is not None:
                    points.append((lane, float((bits >> i) & 1)))
        elif chan.kind == "enum":
            points.append((chan.name, decoded))  # raw integer value, not scaled
        else:
            if chan.scale is not None:
                decoded *= chan.scale
            # Re-check after scaling: a finite integer field times a large *scale reaches
            # infinity, which _decode_field's gate cannot see (plots.js:227 mirrors this).
            if not math.isfinite(decoded):
                return None
            points.append((chan.name, decoded))
    return PlotSample(tick_ms=tick, sid=sid, points=tuple(points))


class PlotDecoder:
    """The typed-stream definition cache, and the grammar that reaches into it.

    A `!ps` sample names its stream by a sid that lives *inside* the line, so a caller
    holding only `decode_plot_sample(raw, definition)` has to re-implement enough of the
    grammar to find the sid before it can look the definition up. serial_link did exactly
    that, and the web UI does it again in JS. Feeding a whole line to one decoder keeps
    the grammar in the module that owns it, and gives the cache somewhere to live other
    than a private attribute other classes reach into.

    Pure in the sense that matters: no I/O, no clock. The only state is the def map.
    """

    def __init__(self) -> None:
        self._defs: dict[str, PlotDef] = {}   # latest !pd per sid (SPEC 2.5)

    def learn(self, raw: str, keep_existing: bool = False) -> bool:
        """Take a `!pd` definition into the cache. Returns False if it was not stored.

        Live, the newest declaration wins: firmware that redefines a sid mid-run describes
        the samples after it. `keep_existing` reverses that for priming newest-first out of
        the store, where the first row seen for a sid is the current one.
        """
        definition = parse_plot_def(raw)
        if definition is None:
            return False
        if keep_existing and definition.sid in self._defs:
            return False
        # Reinserted, not updated in place, so the map's order is learn recency. Two streams
        # can declare the same channel name, and SPEC 2.5 gives the render metadata to the
        # last definition seen; updating in place kept the sid at its first position, so
        # channel_meta's iteration handed that name to whichever sid was declared first.
        self._defs.pop(definition.sid, None)
        self._defs[definition.sid] = definition
        return True

    def definition(self, sid: str) -> PlotDef | None:
        """The cached definition for `sid`, if one has been learned."""
        return self._defs.get(sid)

    def feed(self, raw: str) -> PlotSample | None:
        """Decode any `!p` / `!pd` / `!ps` line.

        `!pd` updates the cache and yields no sample. `!ps` decodes against the cached
        definition for its sid, and yields None when that sid has not been declared yet
        (a sample ahead of its definition) or when the widths disagree. `!p` carries its
        own names and needs no cache.
        """
        if raw.startswith("!pd"):
            self.learn(raw)
            return None
        if raw.startswith("!ps"):
            parts = raw.split()
            if len(parts) < 2:
                return None
            definition = self._defs.get(parts[1])
            return decode_plot_sample(raw, definition) if definition is not None else None
        return parse_plot_adhoc(raw)

    def points(self, raw: str) -> list[dict[str, Any]] | None:
        """`feed`, flattened into the per-channel rows the store writes."""
        sample = self.feed(raw)
        if sample is None:
            return None
        return [
            {"tick_ms": sample.tick_ms, "sid": sample.sid, "name": name, "value": value}
            for name, value in sample.points
        ]

    def channel_meta(self) -> dict[str, dict[str, Any]]:
        """Channel name -> render metadata for every declared stream (SPEC 9.2).

        Channels are keyed by name globally. Enum channels carry their label map;
        packed-bits channels expand into one entry per lane (kind "bit"), each tagged
        with its parent group and bit index; analog channels keep type/scale/unit.

        Iteration is in learn order (see learn), so where two streams share a channel name
        the most recently declared one wins, as SPEC 2.5 requires.
        """
        meta: dict[str, dict[str, Any]] = {}
        for sid, definition in self._defs.items():
            for chan in definition.channels:
                if chan.kind == "bits":
                    for i, lane in enumerate(chan.lanes or ()):
                        if lane is not None:
                            meta[lane] = {
                                "sid": sid, "type": "u1", "scale": None, "unit": None,
                                "kind": "bit", "group": chan.name, "bit": i,
                            }
                elif chan.kind == "enum":
                    meta[chan.name] = {
                        "sid": sid, "type": chan.type, "scale": None, "unit": None,
                        "kind": "enum",
                        "labels": [list(pair) for pair in (chan.labels or ())],
                    }
                else:
                    meta[chan.name] = {
                        "sid": sid, "type": chan.type, "scale": chan.scale,
                        "unit": chan.unit, "kind": "analog",
                    }
        return meta


# --- markers (SPEC 2.5) --------------------------------------------------------------

# The optional tick carries an explicit '@' sigil, in the sigil style SPEC 2.5 already uses
# for the `!pd` unit slot. A bare leading number would be ambiguous against marker text,
# which is free-form and often built at runtime: "!m 12 cells balanced" would silently lose
# its first word to a tick. With the sigil, the failure mode of omitting it is exactly the
# no-tick form, so forgetting it costs the tick and nothing else.
# [0-9] rather than \d: \d also matches non-ASCII decimal digits, so "!m @٥٥ hi"
# yielded a tick of 55 from text the rest of the stack treats as 7-bit ASCII.
_MARKER_TICK_RE = re.compile(r"@([0-9]+)")


@dataclass(frozen=True)
class Marker:
    """A firmware-emitted marker (`!m`): free-form text plus an optional MCU tick."""

    text: str
    tick_ms: int | None = None


def format_marker(text: str, tick_ms: int | None = None) -> str:
    """Format a marker event line `!m [@<tick>] <text>` (SPEC 2.5).

    Hardening, not a fix for a live defect: nothing in the daemon or the simulator calls
    this with unchecked values today. It is worth the two lines because one case was worse
    than a clean rejection: `format_marker('x', -1)` emitted `!m @-1 x`, which parse_marker
    reads back as text `@-1 x` with no tick, i.e. silent corruption rather than a failure.
    """
    if not text.strip():
        raise ProtocolError("marker text is empty")
    _check_no_break(text, "marker text")
    if tick_ms is not None and not 0 <= tick_ms <= TICK_MS_MAX:
        raise ProtocolError(f"marker tick out of range: {tick_ms}")
    if tick_ms is None and _MARKER_TICK_RE.fullmatch(text.strip().split(" ")[0]):
        # Text whose first word is itself a tick sigil: emitted as-is it parses back as a
        # marker carrying a tick nobody set. Refusing is the only honest round trip.
        raise ProtocolError(f"marker text starts with a tick sigil: {text!r}")
    at = f"@{tick_ms} " if tick_ms is not None else ""
    return f"!m {at}{text}"


def parse_marker(raw: str) -> Marker | None:
    """Decode a `!m [@<tick>] <text>` marker line, or None if malformed.

    Returning None (empty text, no text at all, an out-of-range tick) leaves the caller to
    store the line as a generic event, the same contract as `!can` and the plot lines.
    """
    head, sep, rest = normalize_line(raw).partition(" ")
    if head != "!m" or not sep:
        return None
    tick: int | None = None
    first, _, tail = rest.strip().partition(" ")
    at = _MARKER_TICK_RE.fullmatch(first)
    if at is not None:
        digits = at.group(1)
        # Length first: the regex bounds the character set but not the count, and int()
        # raises on a token past CPython's digit limit (see is_decimal_token). Both an
        # over-long and an over-range tick answer None, as documented.
        if len(digits) > MAX_DECIMAL_DIGITS:
            return None
        tick = int(digits)
        if tick > TICK_MS_MAX:
            return None
        rest = tail
    # Only the surrounding whitespace goes: the text is the user's, so internal spacing
    # survives a round trip through format_marker.
    text = rest.strip()
    if not text:
        return None
    return Marker(text=text, tick_ms=tick)
