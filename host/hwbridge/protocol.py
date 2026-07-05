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
