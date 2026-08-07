"""Unit tests for mcuscope.protocol, covering SPEC sections 2.3 to 2.5.

These are pure tests: no I/O, no daemon, no simulator.
"""

from __future__ import annotations

import pytest

from mcuscope import protocol as p

# --- error table (SPEC 2.3) ----------------------------------------------------------


def test_error_table_bijective() -> None:
    assert p.ERROR_NAMES[5] == "nack"
    assert p.ERROR_CODES["overflow"] == 8
    # code <-> name maps are inverses of each other
    for code, name in p.ERROR_NAMES.items():
        assert p.ERROR_CODES[name] == code
    assert set(p.ERROR_NAMES) == set(range(1, 10))


# --- classification (SPEC 2.2) -------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected",
    [
        (">1 ping", p.LineClass.COMMAND),
        ("<1 OK monitor 1 sim", p.LineClass.RESPONSE),
        ("!can 100 - 100 DEADBEEF", p.LineClass.EVENT),
        ("sim alive n=3", p.LineClass.DEBUG),
        ("", p.LineClass.DEBUG),
    ],
)
def test_classify(line: str, expected: p.LineClass) -> None:
    assert p.classify(line) is expected


def test_classify_tolerates_terminator() -> None:
    assert p.classify("<7 OK\r\n") is p.LineClass.RESPONSE


# --- line hygiene (SPEC 2.1) ---------------------------------------------------------


def test_crlf_tolerance() -> None:
    assert p.normalize_line("<3 OK\r\n") == "<3 OK"
    assert p.normalize_line("<3 OK\n") == "<3 OK"
    assert p.normalize_line("<3 OK\r") == "<3 OK"
    assert p.normalize_line("<3 OK") == "<3 OK"


def test_crlf_tolerance_in_parse() -> None:
    r = p.parse_response("<3 OK 48 4A 68\r\n")
    assert r.seq == 3 and r.ok and r.data == "48 4A 68"


def test_oversized_line() -> None:
    body = "<1 OK " + "A" * 300
    assert p.is_oversized(body)
    assert not p.is_oversized("<1 OK short")
    # SPEC 2.1: 255 bytes of content plus the LF terminator. So 255 content bytes is the
    # largest legal line (the firmware accepts it too) and 256 is the first oversized one.
    assert not p.is_oversized("x" * 254)
    assert not p.is_oversized("x" * 255)
    assert p.is_oversized("x" * 256)


# --- hex helpers (SPEC 2.1) ----------------------------------------------------------


def test_hex_round_trip() -> None:
    assert p.bytes_to_hex(b"\xde\xad\xbe\xef") == "DEADBEEF"
    assert p.hex_to_bytes("deadBEEF") == b"\xde\xad\xbe\xef"


def test_hex_to_bytes_rejects_bad() -> None:
    with pytest.raises(p.ProtocolError):
        p.hex_to_bytes("ABC")  # odd length
    with pytest.raises(p.ProtocolError):
        p.hex_to_bytes("ZZ")  # not hex


def test_parse_hex_int() -> None:
    assert p.parse_hex_int("1A3") == 0x1A3
    assert p.parse_hex_int("0x1a3") == 0x1A3
    with pytest.raises(p.ProtocolError):
        p.parse_hex_int("")
    with pytest.raises(p.ProtocolError):
        p.parse_hex_int("0xGG")


# --- sequence numbers (SPEC 2.3) -----------------------------------------------------


def test_next_seq_basic() -> None:
    assert p.next_seq(1) == 2
    assert p.next_seq(0) == 1  # never yields 0, starts at 1


def test_next_seq_wraps_at_65535() -> None:
    assert p.next_seq(65534) == 65535
    assert p.next_seq(65535) == 1  # wrap, never 0


# --- commands (SPEC 2.3) -------------------------------------------------------------


def test_format_command() -> None:
    assert p.format_command(17, "i2c rd 48 2") == ">17 i2c rd 48 2"


def test_format_command_rejects_bad_seq() -> None:
    with pytest.raises(p.ProtocolError):
        p.format_command(0, "ping")
    with pytest.raises(p.ProtocolError):
        p.format_command(70000, "ping")


def test_parse_command() -> None:
    c = p.parse_command(">42 can tx 1A3 DEADBEEF x")
    assert c.seq == 42
    assert c.name == "can"
    assert c.tokens == ("can", "tx", "1A3", "DEADBEEF", "x")


def test_parse_command_rejects_non_command() -> None:
    with pytest.raises(p.ProtocolError):
        p.parse_command("<1 OK")
    with pytest.raises(p.ProtocolError):
        p.parse_command(">notanumber ping")
    with pytest.raises(p.ProtocolError):
        p.parse_command(">1")  # missing name


# --- responses (SPEC 2.3) ------------------------------------------------------------


def test_format_response_ok() -> None:
    assert p.format_response_ok(1) == "<1 OK"
    assert p.format_response_ok(1, "monitor 1 sim") == "<1 OK monitor 1 sim"


def test_format_response_err() -> None:
    assert p.format_response_err(5, 5) == "<5 ERR 5 nack"
    assert p.format_response_err(5, 2, "bad addr") == "<5 ERR 2 badarg bad addr"
    with pytest.raises(p.ProtocolError):
        p.format_response_err(1, 99)  # unknown code


def test_parse_response_ok() -> None:
    r = p.parse_response("<17 OK 4A2B")
    assert r.seq == 17 and r.ok and r.data == "4A2B"
    empty = p.parse_response("<17 OK")
    assert empty.ok and empty.data == ""


def test_parse_response_err() -> None:
    r = p.parse_response("<9 ERR 5 nack device gone")
    assert r.seq == 9 and not r.ok
    assert r.err_code == 5 and r.err_name == "nack" and r.err_detail == "device gone"


def test_parse_response_rejects_malformed() -> None:
    for bad in ["<1 MAYBE", "<x OK", "<1", "<1 ERR 5", "not a response"]:
        with pytest.raises(p.ProtocolError):
            p.parse_response(bad)


def test_command_response_round_trip() -> None:
    line = p.format_command(123, "gpio get led")
    cmd = p.parse_command(line)
    assert cmd.seq == 123 and cmd.tokens == ("gpio", "get", "led")
    resp = p.parse_response(p.format_response_ok(cmd.seq, "1"))
    assert resp.seq == 123 and resp.data == "1"


# --- CAN flags (SPEC 2.5) ------------------------------------------------------------


@pytest.mark.parametrize(
    "ext,rtr,token",
    [(False, False, "-"), (True, False, "x"), (False, True, "r"), (True, True, "xr")],
)
def test_can_flags_round_trip(ext: bool, rtr: bool, token: str) -> None:
    assert p.format_can_flags(ext, rtr) == token
    assert p.parse_can_flags(token) == (ext, rtr)


def test_parse_can_flags_rejects_junk() -> None:
    with pytest.raises(p.ProtocolError):
        p.parse_can_flags("q")


# --- CAN events (SPEC 2.5) -----------------------------------------------------------


def test_can_event_data_round_trip() -> None:
    frame = p.CanFrame(can_id=0x100, data=b"\x01\x02\x03", tick_ms=1234)
    line = p.format_can_event(frame)
    assert line == "!can 1234 - 100 010203"
    back = p.parse_can_event(line)
    assert back is not None
    assert back.can_id == 0x100 and back.data == b"\x01\x02\x03"
    assert back.dlc == 3 and not back.ext and not back.rtr and back.tick_ms == 1234


def test_can_event_zero_length() -> None:
    line = p.format_can_event(p.CanFrame(can_id=0x7FF, data=b"", tick_ms=5))
    assert line == "!can 5 - 7FF -"
    frame = p.parse_can_event(line)
    assert frame is not None and frame.data == b"" and frame.dlc == 0


def test_can_event_extended_id() -> None:
    frame = p.CanFrame(can_id=0x18DAF110, data=b"\xaa", ext=True, tick_ms=99)
    line = p.format_can_event(frame)
    assert line == "!can 99 x 18DAF110 AA"
    back = p.parse_can_event(line)
    assert back is not None and back.ext and back.can_id == 0x18DAF110


def test_can_event_rtr() -> None:
    # RTR: payload is a single decimal DLC digit, not hex data (SPEC 2.5).
    frame = p.CanFrame(can_id=0x1A3, rtr=True, dlc=4, tick_ms=7)
    line = p.format_can_event(frame)
    assert line == "!can 7 r 1A3 4"
    back = p.parse_can_event(line)
    assert back is not None and back.rtr and back.dlc == 4 and back.data == b""


def test_can_event_rtr_extended() -> None:
    frame = p.CanFrame(can_id=0x100, rtr=True, ext=True, dlc=8, tick_ms=1)
    line = p.format_can_event(frame)
    assert line == "!can 1 xr 100 8"
    back = p.parse_can_event(line)
    assert back is not None and back.ext and back.rtr and back.dlc == 8


@pytest.mark.parametrize(
    "bad",
    [
        "!can 100 - 100",           # too few tokens
        "!can 100 - 100 AA BB",     # too many tokens
        "!nope 1 - 1 AA",           # wrong prefix
        "!can x - 100 AA",          # non-decimal tick
        "!can 100 q 100 AA",        # bad flags
        "!can 100 - ZZ AA",         # bad hex id
        "!can 100 - 100 A",         # odd-length data
        "!can 100 - 100 ZZ",        # non-hex data
        "!can 100 r 100 12",        # rtr dlc not a single digit
        "!can 100 r 100 9",         # rtr dlc out of range
        "!can 100 - 100 00112233445566778899",  # more than 8 data bytes
        "!can 5000000000 - 100 AA",  # tick beyond 2^32-1
    ],
)
def test_malformed_can_event_returns_none(bad: str) -> None:
    # SPEC 3.5: a malformed !can is stored as a generic event, so decode returns None
    # instead of raising.
    assert p.parse_can_event(bad) is None


# --- can tx argument parsing (SPEC 2.4) ----------------------------------------------


def test_parse_can_tx_data() -> None:
    f = p.parse_can_tx_args(["1A3", "DEADBEEF"])
    assert f.can_id == 0x1A3 and f.data == b"\xde\xad\xbe\xef" and f.dlc == 4


def test_parse_can_tx_zero_length_and_flags() -> None:
    f = p.parse_can_tx_args(["1A3", "-", "x"])
    assert f.ext and f.data == b"" and f.dlc == 0


def test_parse_can_tx_rtr() -> None:
    f = p.parse_can_tx_args(["1A3", "4", "r"])
    assert f.rtr and f.dlc == 4 and f.data == b""


def test_parse_can_tx_rejects_bad() -> None:
    with pytest.raises(p.ProtocolError):
        p.parse_can_tx_args(["1A3"])  # missing data
    with pytest.raises(p.ProtocolError):
        p.parse_can_tx_args(["1A3", "0011223344556677AA"])  # 9 bytes
    with pytest.raises(p.ProtocolError):
        p.parse_can_tx_args(["1A3", "12", "r"])  # rtr dlc not single digit


# --- plot data (SPEC 2.5) ------------------------------------------------------------


def test_parse_plot_adhoc_basic() -> None:
    s = p.parse_plot_adhoc("!p 1234 ax=-12 ay=3.5")
    assert s is not None
    assert s.tick_ms == 1234 and s.sid is None
    assert s.points == (("ax", -12.0), ("ay", 3.5))


def test_parse_plot_adhoc_rejects_malformed() -> None:
    assert p.parse_plot_adhoc("!p 1234") is None            # no pairs
    assert p.parse_plot_adhoc("!p 1234 ax") is None         # no '='
    assert p.parse_plot_adhoc("!p 1234 ax=") is None        # empty value
    assert p.parse_plot_adhoc("!p 1234 ax=1.2.3") is None   # bad number
    assert p.parse_plot_adhoc("!p xx ax=1") is None         # non-decimal tick
    assert p.parse_plot_adhoc("!p 1234 1bad=1") is None     # bad name
    assert p.parse_plot_adhoc("!can 1 - 100 -") is None     # wrong prefix


def test_parse_plot_value_scientific_notation() -> None:
    # "%g" on a float emits exponents unprompted, so the grammar has to take them.
    assert p.parse_plot_value("1.2e-05") == 1.2e-05
    assert p.parse_plot_value("9.8e-4") == 9.8e-4
    assert p.parse_plot_value("-3E+2") == -300.0
    assert p.parse_plot_value("1e6") == 1e6


def test_parse_plot_value_rejects_malformed() -> None:
    assert p.parse_plot_value("1e999") is None    # in grammar, overflows to inf
    assert p.parse_plot_value("-1e999") is None
    assert p.parse_plot_value("inf") is None
    assert p.parse_plot_value("nan") is None
    assert p.parse_plot_value("+1.5") is None     # no leading '+', as before
    assert p.parse_plot_value(".5") is None       # no bare fraction, as before
    assert p.parse_plot_value("1e") is None       # empty exponent
    assert p.parse_plot_value("1e+") is None
    assert p.parse_plot_value("1.2e1.5") is None  # fractional exponent


def test_plot_scientific_notation_end_to_end() -> None:
    # A scientific scale must reach the decoded value, not just parse.
    d = p.parse_plot_def("!pd 0 ax:s2*9.8e-4:g")
    assert d is not None and d.channels[0].scale == 9.8e-4
    s = p.decode_plot_sample("!ps 0 64 0064", d)
    assert s is not None and s.points[0][1] == pytest.approx(100 * 9.8e-4)
    adhoc = p.parse_plot_adhoc("!p 1234 leak=1.2e-05")
    assert adhoc is not None and adhoc.points == (("leak", 1.2e-05),)


def test_parse_plot_def_full() -> None:
    d = p.parse_plot_def("!pd 0 ax:s2*0.00098:g ay:s2*0.00098:g az:s2*0.00098:g")
    assert d is not None
    assert d.sid == "0" and len(d.channels) == 3
    ax = d.channels[0]
    assert ax.name == "ax" and ax.type == "s2" and ax.scale == 0.00098 and ax.unit == "g"


def test_parse_plot_def_optional_scale_and_unit() -> None:
    d = p.parse_plot_def("!pd 3 tri:s2*0.01:V ramp:u2 ftest:f4")
    assert d is not None and d.sid == "3"
    assert d.channels[1] == p.PlotChannel(name="ramp", type="u2", scale=None, unit=None)
    assert d.channels[2].type == "f4"


def test_parse_plot_def_rejects_malformed() -> None:
    assert p.parse_plot_def("!pd 0") is None                 # no channels
    assert p.parse_plot_def("!pd 12 a:s2") is None           # sid not single digit
    assert p.parse_plot_def("!pd 0 a:x9") is None            # unknown type
    assert p.parse_plot_def("!pd 0 1bad:s2") is None         # bad name
    assert p.parse_plot_def("!pd 0 a:s2*x") is None          # bad scale
    assert p.parse_plot_def("!pd 0 a:s2:g:extra") is None    # too many colon fields


def test_decode_plot_sample_spec_example() -> None:
    d = p.parse_plot_def("!pd 0 ax:s2*0.00098:g ay:s2*0.00098:g az:s2*0.00098:g")
    assert d is not None
    s = p.decode_plot_sample("!ps 0 12D687 FC01,0200,4000", d)
    assert s is not None
    assert s.tick_ms == 0x12D687 and s.sid == "0"
    names = [n for n, _ in s.points]
    vals = [v for _, v in s.points]
    assert names == ["ax", "ay", "az"]
    # FC01 s2 = -1023, 0200 = 512, 4000 = 16384, each * 0.00098
    assert vals[0] == pytest.approx(-1023 * 0.00098)
    assert vals[1] == pytest.approx(512 * 0.00098)
    assert vals[2] == pytest.approx(16384 * 0.00098)


def test_decode_plot_sample_float_bit_exact() -> None:
    d = p.parse_plot_def("!pd 1 x:f4")
    assert d is not None
    # 0x3F800000 is IEEE754 1.0; 0xBF800000 is -1.0
    assert p.decode_plot_sample("!ps 1 A 3F800000", d).points[0][1] == 1.0
    assert p.decode_plot_sample("!ps 1 A BF800000", d).points[0][1] == -1.0


def test_decode_plot_sample_unsigned_and_signed() -> None:
    d = p.parse_plot_def("!pd 2 u:u1 s:s1")
    assert d is not None
    s = p.decode_plot_sample("!ps 2 0 FF,FF", d)
    assert s.points[0][1] == 255.0 and s.points[1][1] == -1.0


def test_decode_plot_sample_rejects_mismatch() -> None:
    d = p.parse_plot_def("!pd 0 a:s2 b:s2")
    assert d is not None
    assert p.decode_plot_sample("!ps 0 0 FC01", d) is None          # too few values
    assert p.decode_plot_sample("!ps 0 0 FC01,0200,4000", d) is None  # too many
    assert p.decode_plot_sample("!ps 0 0 FC0,0200", d) is None       # wrong field width
    assert p.decode_plot_sample("!ps 0 0 GGGG,0200", d) is None      # bad hex
    assert p.decode_plot_sample("!ps 1 0 FC01,0200", d) is None      # sid mismatch
    assert p.decode_plot_sample("!ps 0 XY FC01,0200", d) is None     # bad tick hex


def test_parse_enum_channel() -> None:
    d = p.parse_plot_def("!pd 0 state:u1:=0=IDLE,1=ARMED,4=RUN")
    assert d is not None
    ch = d.channels[0]
    assert ch.kind == "enum"
    assert ch.labels == ((0, "IDLE"), (1, "ARMED"), (4, "RUN"))
    assert ch.unit is None and ch.type == "u1"


def test_parse_bits_channel_with_skip() -> None:
    d = p.parse_plot_def("!pd 0 gpio:u1:/led,,pwm_en")
    ch = d.channels[0]
    assert ch.kind == "bits"
    assert ch.lanes == ("led", None, "pwm_en")


def test_parse_rejects_bad_kinds() -> None:
    assert p.parse_plot_def("!pd 0 x:f4:=0=A") is None
    assert p.parse_plot_def("!pd 0 x:s1:/a") is None
    assert p.parse_plot_def("!pd 0 x:u1:/a,b,c,d,e,f,g,h,i") is None
    assert p.parse_plot_def("!pd 0 x:u1:/,,,") is None
    assert p.parse_plot_def("!pd 0 x:u1:=0=a!b") is None


def test_parse_rejects_negative_value_on_unsigned_enum() -> None:
    assert p.parse_plot_def("!pd 0 x:u1:=-1=A") is None


def test_parse_rejects_more_malformed_kinds() -> None:
    # These all pass their base type but carry a malformed sigil body; the whole !pd
    # must be dropped (mirrors the web UI parser exactly).
    assert p.parse_plot_def("!pd 0 x:u1:=0=") is None       # empty label
    assert p.parse_plot_def("!pd 0 x:u1:=z=A") is None      # non-decimal enum value
    assert p.parse_plot_def("!pd 0 x:u1:=") is None         # sigil with nothing after it
    assert p.parse_plot_def("!pd 0 x:u1:/") is None         # bits sigil, no lanes
    assert p.parse_plot_def("!pd 0 x:u1:") is None          # present-but-empty unit slot
    assert p.parse_plot_def("!pd 0 x:u1:/1bad") is None     # lane name fails _valid_plot_name
    # int(_, 10) leniencies that SPEC 2.5 forbids and the web UI rejects:
    assert p.parse_plot_def("!pd 0 x:u1:=+1=A") is None     # leading '+'
    assert p.parse_plot_def("!pd 0 x:u2:=1_0=A") is None    # underscore digit grouping
    # a *scale on an enum/bits channel is meaningless: reject rather than silently drop it.
    assert p.parse_plot_def("!pd 0 x:u1*2:=0=A") is None
    assert p.parse_plot_def("!pd 0 x:u1*2:/led") is None


def test_decode_plot_sample_rejects_bad_tick() -> None:
    d = p.parse_plot_def("!pd 0 a:u1")
    assert p.decode_plot_sample("!ps 0 100000000 00", d) is None  # tick > 0xFFFFFFFF
    assert p.decode_plot_sample("!ps 0 0x1F 00", d) is None        # '0x' prefix not bare hex
    assert p.decode_plot_sample("!ps 0 +1 00", d) is None          # leading '+'
    assert p.decode_plot_sample("!ps 0 FF FF", d).points == (("a", 255.0),)  # sanity


def test_decode_bits_expands_lsb_first() -> None:
    d = p.parse_plot_def("!pd 0 gpio:u1:/led,irq,pwm_en")
    s = p.decode_plot_sample("!ps 0 5 05", d)
    assert dict(s.points) == {"led": 1.0, "irq": 0.0, "pwm_en": 1.0}


def test_decode_enum_stores_raw_signed() -> None:
    d = p.parse_plot_def("!pd 0 mode:s1:=-1=ERR,0=OK")
    s = p.decode_plot_sample("!ps 0 5 FF", d)
    assert s.points == (("mode", -1.0),)


def test_analog_spec_unchanged() -> None:
    ch = p.parse_plot_def("!pd 0 ax:s2*0.00098:g").channels[0]
    assert ch.kind == "analog" and ch.unit == "g" and ch.scale == 0.00098


# --- markers (SPEC 2.5) --------------------------------------------------------------


def test_parse_marker_with_and_without_tick() -> None:
    m = p.parse_marker("!m @12345 calibration start")
    assert m == p.Marker(text="calibration start", tick_ms=12345)
    m = p.parse_marker("!m boot done")
    assert m == p.Marker(text="boot done", tick_ms=None)


def test_parse_marker_never_eats_a_leading_number() -> None:
    # The whole point of the '@' sigil: free-form text that starts with a bare number
    # keeps its first word, and simply gets no tick.
    assert p.parse_marker("!m 12 cells balanced") == p.Marker(text="12 cells balanced")
    assert p.parse_marker("!m 3.3V rail fault") == p.Marker(text="3.3V rail fault")


def test_parse_marker_preserves_text_verbatim() -> None:
    m = p.parse_marker("!m @7   two  spaces  inside  ")
    assert m is not None and m.text == "two  spaces  inside"


def test_parse_marker_rejects_malformed() -> None:
    assert p.parse_marker("!m") is None                  # no text at all
    assert p.parse_marker("!m ") is None                 # empty text
    assert p.parse_marker("!m @123") is None             # tick but no text
    assert p.parse_marker("!m @123   ") is None
    assert p.parse_marker("!m @4294967296 over") is None  # tick above 2^32-1
    assert p.parse_marker("!mystery boom") is None       # not the !m line type
    assert p.parse_marker("!p 1 a=1") is None


def test_parse_marker_tolerates_a_bad_sigil() -> None:
    # Anything that is not '@' + digits is text, so a typo degrades to the no-tick form
    # rather than losing the line.
    assert p.parse_marker("!m @abc go") == p.Marker(text="@abc go")
    assert p.parse_marker("!m @12x go") == p.Marker(text="@12x go")


def test_marker_round_trip() -> None:
    assert p.parse_marker(p.format_marker("boot done")) == p.Marker(text="boot done")
    assert p.format_marker("boot done", 99) == "!m @99 boot done"
    assert p.parse_marker(p.format_marker("boot done", 99)).tick_ms == 99


def test_marker_line_is_classified_as_an_event() -> None:
    assert p.classify("!m @1 hello") is p.LineClass.EVENT


# --- oversized numeric tokens (SPEC 2.1) ---------------------------------------------
#
# int() refuses a decimal string longer than CPython's 4300-digit limit and raises a bare
# ValueError, which is neither the ProtocolError the seq machinery promises nor the None
# the event decoders promise. SPEC 2.1 caps a line at 255 bytes so a conforming target
# cannot emit one; a misbehaving one can, and such a line took a whole rx burst down with
# it (see test_reconnect.py).

_HUGE = "9" * 5000


def test_seq_machinery_rejects_an_overlong_number() -> None:
    with pytest.raises(p.ProtocolError):
        p.parse_seq_token(_HUGE)
    with pytest.raises(p.ProtocolError):
        p.parse_command(f">{_HUGE} ping")
    with pytest.raises(p.ProtocolError):
        p.parse_response(f"<{_HUGE} OK")


def test_event_decoders_return_none_for_an_overlong_number() -> None:
    assert p.parse_can_event(f"!can {_HUGE} - 100 DEADBEEF") is None
    assert p.parse_plot_adhoc(f"!p {_HUGE} v=1") is None
    assert p.parse_marker(f"!m @{_HUGE} boom") is None
    assert p.parse_plot_def(f"!pd 1 state:u1:={_HUGE}=idle") is None


def test_error_code_token_is_strict_ascii_decimal() -> None:
    # Bare int() accepted every one of these, so a garbled ERR line reached the user under
    # a plausible but wrong error code instead of being rejected as unparseable.
    for token in ("+4", "1_0", "٤", "-3", _HUGE):
        with pytest.raises(p.ProtocolError):
            p.parse_response(f"<1 ERR {token} badarg oops")
    assert p.parse_response("<1 ERR 4 buserr oops").err_code == 4


# --- formatter/parser symmetry -------------------------------------------------------
#
# Hardening: no current caller can reach these, since the only one is the simulator and it
# takes its values from already-bounded parsers. They are here because a formatter that
# emits a line its own decoder throws away has bitten this module twice.


@pytest.mark.parametrize(
    "frame",
    [
        p.CanFrame(can_id=0x100, data=b"\x01" * 9, tick_ms=1),        # 9 data bytes
        p.CanFrame(can_id=0x100, data=b"\x01", tick_ms=2**32),        # tick past 32 bits
        p.CanFrame(can_id=0x100, data=b"\x01", tick_ms=-5),           # negative tick
        p.CanFrame(can_id=0x100, rtr=True, dlc=9, tick_ms=1),         # rtr dlc past 8
        p.CanFrame(can_id=0x100, rtr=True, dlc=-1, tick_ms=1),        # negative rtr dlc
    ],
)
def test_format_can_event_rejects_frames_parse_would_refuse(frame: p.CanFrame) -> None:
    with pytest.raises(p.ProtocolError):
        p.format_can_event(frame)


@pytest.mark.parametrize("seq", [0, -1, p.SEQ_MAX + 1])
def test_response_formatters_range_check_the_seq(seq: int) -> None:
    # format_command has always checked; these did not, so format_response_ok(0) emitted
    # `<0 OK`, which parse_response rejects.
    with pytest.raises(p.ProtocolError):
        p.format_response_ok(seq)
    with pytest.raises(p.ProtocolError):
        p.format_response_err(seq, 2)


def test_format_marker_rejects_what_parse_marker_will_not_read_back() -> None:
    for text in ("", "   "):
        with pytest.raises(p.ProtocolError):
            p.format_marker(text)
    for tick in (-1, 2**32):
        with pytest.raises(p.ProtocolError):
            p.format_marker("x", tick)
    # The negative tick was the worst of them: `!m @-1 x` parsed back as text "@-1 x" with
    # no tick at all, so the marker was silently corrupted rather than visibly refused.


# --- the typed-stream def cache (SPEC 2.5) --------------------------------------------
#
# These are the cache's lifecycle cases. They were reachable only by driving a SerialPort
# with a live store, so nothing covered them; the sid lives inside the sample line, and
# owning the lookup is what makes them testable here.


def test_decoder_ignores_a_sample_that_arrives_before_its_definition() -> None:
    """`!ps` ahead of its `!pd` decodes to nothing, and is stored as a plain event."""
    d = p.PlotDecoder()
    assert d.feed("!ps 0 10 01") is None
    assert d.points("!ps 0 10 01") is None
    assert len(d) == 0
    assert d.learn("!pd 0 v:u1")
    sample = d.feed("!ps 0 10 01")
    assert sample is not None and sample.points == (("v", 1.0),)


def test_decoder_takes_a_mid_stream_redefinition() -> None:
    """The newest `!pd` for a sid describes the samples after it, including a new width."""
    d = p.PlotDecoder()
    d.feed("!pd 0 v:u1")
    assert d.feed("!ps 0 10 01,02") is None          # two values against a one-value def
    d.feed("!pd 0 a:u1 b:u1")                        # redefined, same sid, wider
    sample = d.feed("!ps 0 10 01,02")
    assert sample is not None
    assert sample.points == (("a", 1.0), ("b", 2.0))
    assert len(d) == 1                               # replaced, not accumulated


def test_learn_if_new_keeps_the_first_definition_seen() -> None:
    """Priming reads newest-first, so an older row must not overwrite the current def."""
    d = p.PlotDecoder()
    assert d.learn_if_new("!pd 0 new:u1") is True
    assert d.learn_if_new("!pd 0 old:u1") is False    # older row, same sid
    assert d.channel_meta().keys() == {"new"}
    assert d.learn_if_new("!pd 1 other:u1") is True   # a different sid still lands


def test_decoder_rejects_a_malformed_definition_without_disturbing_the_cache() -> None:
    d = p.PlotDecoder()
    d.learn("!pd 0 v:u1")
    assert d.learn("!pd") is False
    assert d.learn("!pd 0 v:nosuchtype") is False
    assert d.channel_meta().keys() == {"v"}


def test_decoder_keeps_separate_sids_apart() -> None:
    d = p.PlotDecoder()
    d.feed("!pd 0 temp:u1")
    d.feed("!pd 1 volts:u1")
    assert d.feed("!ps 0 10 07").points == (("temp", 7.0),)
    assert d.feed("!ps 1 10 09").points == (("volts", 9.0),)
    assert d.feed("!ps 2 10 09") is None       # undeclared sid
    assert len(d) == 2


def test_adhoc_plot_needs_no_definition() -> None:
    """`!p` carries its own names, so it decodes on an empty cache."""
    d = p.PlotDecoder()
    sample = d.feed("!p 10 temp=21.5")
    assert sample is not None and sample.points == (("temp", 21.5),)
    assert len(d) == 0


def test_points_flattens_a_sample_into_store_rows() -> None:
    d = p.PlotDecoder()
    d.learn("!pd 0 gpio:u1:/led,irq")
    rows = d.points("!ps 0 1A 03")
    assert rows == [
        {"tick_ms": 0x1A, "sid": "0", "name": "led", "value": 1.0},
        {"tick_ms": 0x1A, "sid": "0", "name": "irq", "value": 1.0},
    ]
    assert d.points("!pd 0 gpio:u1:/led,irq") is None   # a definition carries no points
