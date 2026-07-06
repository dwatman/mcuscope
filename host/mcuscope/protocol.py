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

import re
import struct
from dataclasses import dataclass
from enum import StrEnum

# --- constants (SPEC 2.1, 2.3) -------------------------------------------------------

PROTO_VERSION = 1

# Maximum line length including the terminator, both directions (SPEC 2.1).
MAX_LINE_BYTES = 255

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


class LineClass(StrEnum):
    """Classification of a line by its first character (SPEC 2.2)."""

    COMMAND = "cmd"    # '>' PC to MCU (we send these; not expected on RX)
    RESPONSE = "resp"  # '<' MCU to PC, response to a command
    EVENT = "event"    # '!' MCU to PC, asynchronous event
    DEBUG = "debug"    # anything else, normal application output


# --- line hygiene --------------------------------------------------------------------


def normalize_line(raw: str) -> str:
    """Strip a single trailing CRLF/LF/CR pair, tolerating a preceding CR (SPEC 2.1)."""
    return raw.rstrip("\r\n")


def is_oversized(body: str) -> bool:
    """True if `body` plus its LF terminator exceeds the 255-byte line limit."""
    return len(body.encode("ascii", "replace")) + 1 > MAX_LINE_BYTES


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
    """Parse a hex integer, tolerating an optional `0x`/`0X` prefix (SPEC 3.4)."""
    t = text[2:] if text[:2] in ("0x", "0X") else text
    if not t or any(c not in "0123456789abcdefABCDEF" for c in t):
        raise ProtocolError(f"invalid hex integer: {text!r}")
    return int(t, 16)


# --- sequence numbers (SPEC 2.3) -----------------------------------------------------

SEQ_MIN = 1
SEQ_MAX = 65535


def next_seq(seq: int) -> int:
    """Return the seq after `seq`: 1..65535, wrapping 65535 -> 1, never 0 (SPEC 2.3)."""
    nxt = seq + 1
    if nxt > SEQ_MAX or nxt < SEQ_MIN:
        return SEQ_MIN
    return nxt


# --- commands (SPEC 2.3) -------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    """A parsed `>SEQ CMD [ARGS...]` command line."""

    seq: int
    tokens: tuple[str, ...]  # CMD and its arguments, seq removed

    @property
    def name(self) -> str:
        return self.tokens[0] if self.tokens else ""


def format_command(seq: int, cmd: str) -> str:
    """Build a `>SEQ CMD ...` line body (no terminator). `cmd` is text without seq."""
    if not (SEQ_MIN <= seq <= SEQ_MAX):
        raise ProtocolError(f"seq out of range: {seq}")
    cmd = cmd.strip()
    if not cmd:
        raise ProtocolError("empty command")
    return f">{seq} {cmd}"


def parse_command(raw: str) -> Command:
    """Parse a `>SEQ CMD [ARGS...]` line. Raises ProtocolError if it is not one."""
    line = normalize_line(raw)
    if not line.startswith(">"):
        raise ProtocolError(f"not a command line: {raw!r}")
    parts = line[1:].split()
    if len(parts) < 2:
        raise ProtocolError(f"command missing seq or name: {raw!r}")
    try:
        seq = int(parts[0])
    except ValueError as exc:
        raise ProtocolError(f"bad seq token: {parts[0]!r}") from exc
    if not (SEQ_MIN <= seq <= SEQ_MAX):
        raise ProtocolError(f"seq out of range: {seq}")
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
    """Build a `<SEQ OK [data]` line body (no terminator)."""
    data = data.strip()
    return f"<{seq} OK {data}".rstrip() if data else f"<{seq} OK"


def format_response_err(seq: int, code: int, detail: str = "") -> str:
    """Build a `<SEQ ERR CODE NAME [detail]` line body (no terminator)."""
    name = ERROR_NAMES.get(code)
    if name is None:
        raise ProtocolError(f"unknown error code: {code}")
    detail = detail.strip()
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
    try:
        seq = int(parts[0])
    except ValueError as exc:
        raise ProtocolError(f"bad seq token: {parts[0]!r}") from exc
    kind = parts[1]
    if kind == "OK":
        return Response(seq=seq, ok=True, data=" ".join(parts[2:]))
    if kind == "ERR":
        if len(parts) < 4:
            raise ProtocolError(f"ERR response missing code/name: {raw!r}")
        try:
            code = int(parts[2])
        except ValueError as exc:
            raise ProtocolError(f"bad error code token: {parts[2]!r}") from exc
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

    def __post_init__(self) -> None:
        if not self.rtr:
            self.dlc = len(self.data)


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
    flags = format_can_flags(frame.ext, frame.rtr)
    can_id = format_can_id(frame.can_id)
    if frame.rtr:
        payload = str(frame.dlc)
    elif frame.data:
        payload = bytes_to_hex(frame.data)
    else:
        payload = "-"
    return f"!can {frame.tick_ms} {flags} {can_id} {payload}"


def parse_can_event(raw: str) -> CanFrame | None:
    """Decode an `!can` event line.

    Returns a CanFrame, or None if the line is malformed. SPEC 3.5 requires a bad
    `!can` line to still be stored as a generic event rather than raising, so callers
    use the None to mean "store as generic event, skip can_frames".
    """
    line = normalize_line(raw)
    parts = line.split()
    if len(parts) != 5 or parts[0] != "!can":
        return None
    _, tick_s, flags_s, id_s, payload_s = parts
    try:
        if not tick_s.isdigit():
            return None
        tick = int(tick_s)
        if tick > 0xFFFFFFFF:
            return None
        ext, rtr = parse_can_flags(flags_s)
        can_id = parse_hex_int(id_s)
        if rtr:
            if not (payload_s.isdigit() and len(payload_s) == 1):
                return None
            dlc = int(payload_s)
            if dlc > 8:
                return None
            return CanFrame(can_id=can_id, ext=ext, rtr=True, dlc=dlc, tick_ms=tick)
        if payload_s == "-":
            data = b""
        else:
            data = hex_to_bytes(payload_s)
        if len(data) > 8:
            return None
        return CanFrame(can_id=can_id, data=data, ext=ext, rtr=False, tick_ms=tick)
    except ProtocolError:
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
    if rtr:
        if not (data_tok.isdigit() and len(data_tok) == 1):
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
_ENUM_TYPES = frozenset({"u1", "s1", "u2", "s2", "u4", "s4"})
_BITS_TYPES = frozenset({"u1", "u2", "u4"})


@dataclass(frozen=True)
class PlotChannel:
    """One channel within a typed stream definition (`!pd`)."""

    name: str
    type: str
    scale: float | None = None
    unit: str | None = None
    kind: str = "analog"                                     # "analog" | "enum" | "bits"
    labels: tuple[tuple[int, str], ...] | None = None        # enum: (value, label) pairs
    lanes: tuple[str | None, ...] | None = None               # bits: LSB-first lane names


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
    """Parse an ad-hoc `!p` value: optional sign, digits, optional fractional part."""
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        return None
    return float(text)


def parse_plot_adhoc(raw: str) -> PlotSample | None:
    """Decode an ad-hoc `!p <tick> name=value ...` line, or None if malformed."""
    parts = normalize_line(raw).split()
    if len(parts) < 3 or parts[0] != "!p":
        return None
    tick_s = parts[1]
    if not tick_s.isdigit() or int(tick_s) > 0xFFFFFFFF:
        return None
    tick = int(tick_s)
    points: list[tuple[str, float]] = []
    for pair in parts[2:]:
        name, sep, value_s = pair.partition("=")
        if not sep or not _valid_plot_name(name):
            return None
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
    if len(sid) != 1 or not sid.isdigit():
        return None
    channels: list[PlotChannel] = []
    for spec in parts[2:]:
        chan = _parse_channel_spec(spec)
        if chan is None:
            return None
        channels.append(chan)
    if not channels:
        return None
    return PlotDef(sid=sid, channels=tuple(channels))


def _parse_enum_labels(body: str, signed: bool) -> tuple[tuple[int, str], ...] | None:
    """Parse `v=label,v=label,...` into (value, label) pairs, or None if malformed."""
    pairs: list[tuple[int, str]] = []
    for item in body.split(","):
        val_s, sep, label = item.partition("=")
        if not sep or not _LABEL_RE.fullmatch(label):
            return None
        try:
            val = int(val_s, 10)
        except ValueError:
            return None
        if not signed and val < 0:
            return None
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
        return float(struct.unpack(">f", raw)[0])
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
    try:
        tick = int(tick_s, 16)
    except ValueError:
        return None
    if tick < 0 or tick > 0xFFFFFFFF:
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
            points.append((chan.name, decoded))
    return PlotSample(tick_ms=tick, sid=sid, points=tuple(points))
