"""Cross-platform tests for the simulator's I/O-free command dispatch (SPEC 7).

The pty serving loop is POSIX only and is exercised end to end in the phase 2 e2e
suite; here we drive the Simulator class directly so the protocol behavior is checked
on any platform.
"""

from __future__ import annotations

import struct
import time

import mcu_sim
import pytest

from mcuscope import protocol as p


@pytest.fixture
def sim() -> mcu_sim.Simulator:
    args = mcu_sim.build_parser().parse_args([])
    return mcu_sim.Simulator(args)


def only(lines: list[str]) -> str:
    assert len(lines) == 1, f"expected one line, got {lines!r}"
    return lines[0]


def resp(sim: mcu_sim.Simulator, line: str) -> p.Response:
    return p.parse_response(only(sim.handle_line(line)))


# --- core commands -------------------------------------------------------------------


def test_ping(sim: mcu_sim.Simulator) -> None:
    assert only(sim.handle_line(">1 ping")) == "<1 OK monitor 1 sim"


def test_info_reports_uptime(sim: mcu_sim.Simulator) -> None:
    r = resp(sim, ">2 info")
    assert r.ok and r.data.startswith("up=")


def test_i2c_scan_finds_two_devices(sim: mcu_sim.Simulator) -> None:
    r = resp(sim, ">3 i2c scan")
    assert r.data == "48 50"


def test_i2c_temp_read_two_bytes(sim: mcu_sim.Simulator) -> None:
    r = resp(sim, ">4 i2c rd 48 2")
    assert r.ok and len(p.hex_to_bytes(r.data)) == 2


def test_spi_inverts(sim: mcu_sim.Simulator) -> None:
    r = resp(sim, ">5 spi xfer imu 00FF")
    assert r.data == "FF00"


def test_spi_unknown_cs_is_badarg(sim: mcu_sim.Simulator) -> None:
    r = resp(sim, ">6 spi xfer nope 00")
    assert not r.ok and r.err_code == p.ERROR_CODES["badarg"]


def test_gpio_set_get_round_trip(sim: mcu_sim.Simulator) -> None:
    assert resp(sim, ">7 gpio set led 1").ok
    assert resp(sim, ">8 gpio get led").data == "1"
    assert resp(sim, ">9 gpio set led 0").ok
    assert resp(sim, ">10 gpio get led").data == "0"


def test_adc_read(sim: mcu_sim.Simulator) -> None:
    r = resp(sim, ">11 adc read vbat")
    assert r.ok and "raw=" in r.data and "mv=" in r.data


def test_can_tx_ok_and_stat(sim: mcu_sim.Simulator) -> None:
    assert resp(sim, ">12 can tx 100 DEADBEEF").ok
    r = resp(sim, ">13 can stat")
    assert "tx=1" in r.data and "state=active" in r.data


def test_eeprom_write_read_round_trip(sim: mcu_sim.Simulator) -> None:
    # Offset byte 0x10, then payload; read it back via wrrd from the same offset.
    assert resp(sim, ">14 i2c wr 50 10CAFEBABE").ok
    r = resp(sim, ">15 i2c wrrd 50 10 4")
    assert r.data == "CAFEBABE"


# --- error paths ---------------------------------------------------------------------


def test_unknown_command_is_badcmd(sim: mcu_sim.Simulator) -> None:
    r = resp(sim, ">16 frobnicate")
    assert not r.ok and r.err_code == p.ERROR_CODES["badcmd"]


def test_bad_gpio_name_is_badarg(sim: mcu_sim.Simulator) -> None:
    r = resp(sim, ">17 gpio set nope 1")
    assert not r.ok and r.err_code == p.ERROR_CODES["badarg"]


def test_non_command_is_ignored(sim: mcu_sim.Simulator) -> None:
    assert sim.handle_line("sim alive n=1") == []


def test_unparseable_seq_is_silent(sim: mcu_sim.Simulator) -> None:
    assert sim.handle_line(">notaseq ping") == []


def test_drop_response_swallows_nth() -> None:
    args = mcu_sim.build_parser().parse_args(["--drop-response", "2"])
    s = mcu_sim.Simulator(args)
    assert s.handle_line(">1 ping") != []   # first answered
    assert s.handle_line(">2 ping") == []   # second dropped
    assert s.handle_line(">3 ping") != []   # third answered


def test_gpio_set_triggers_debug_burst(sim: mcu_sim.Simulator) -> None:
    # The pty loop emits a debug burst after a gpio set; the burst itself is here.
    burst = sim.burst_debug()
    assert burst and all(p.classify(line) is p.LineClass.DEBUG for line in burst)


# --- typed plot sample encoding (SPEC 2.5) -------------------------------------------


def test_typed_sample_matches_spec_shape() -> None:
    # -1023 s2, 512 u2, 2.0 f4  ->  big-endian fixed-width hex, comma separated.
    packed = struct.pack("<hHf", -1023, 512, 2.0)
    line = mcu_sim._format_typed_sample("0", 0x12D687, packed, ("h", "H", "f"))
    assert line == "!ps 0 12D687 FC01,0200,40000000"


def test_typed_sample_f4_bit_exact() -> None:
    packed = struct.pack("<f", 1.0)
    line = mcu_sim._format_typed_sample("0", 1, packed, ("f",))
    assert line.endswith("3F800000")  # IEEE754 1.0f big-endian


def test_sim_emits_enum_and_bits_streams(sim: mcu_sim.Simulator) -> None:
    # Drive _poll_plot directly with a synthetic clock so no real sleeping is needed;
    # a few seconds of simulated time is enough for both !pd defs and !ps samples on
    # streams 1 (enum) and 2 (bits) to appear alongside stream 0.
    lines: list[str] = []
    now = time.monotonic()
    for _i in range(60):
        now += 0.05
        lines.extend(sim._poll_plot(now))

    defs: dict[str, p.PlotDef] = {}
    got_enum = False
    got_bits = False
    for line in lines:
        if line.startswith("!pd"):
            d = p.parse_plot_def(line)
            if d:
                defs[d.sid] = d
        elif line.startswith("!ps"):
            sid = line.split()[1]
            d = defs.get(sid)
            if not d:
                continue
            s = p.decode_plot_sample(line, d)
            if s and d.channels[0].kind == "enum":
                got_enum = got_enum or any(n == "state" for n, _ in s.points)
            if s and d.channels[0].kind == "bits":
                got_bits = got_bits or any(n in ("led", "irq", "pwm_en") for n, _ in s.points)

    assert got_enum, f"expected a decodable enum sample on stream 1, lines={lines!r}"
    assert got_bits, f"expected a decodable bits sample on stream 2, lines={lines!r}"
