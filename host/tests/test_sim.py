"""Cross-platform tests for the simulator's I/O-free command dispatch (SPEC 7).

Here the Simulator class is driven directly, so the protocol behaviour is checked on any
platform. The serving loops around it get their end-to-end exercise elsewhere: TCP in
test_sim_tcp.py, the pty in test_sim_pty.py (POSIX only).
"""

from __future__ import annotations

import errno
import os
import socket
import struct
import threading
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


def test_mark_without_text_is_badarg(sim: mcu_sim.Simulator) -> None:
    # SPEC 7. Without the check the sim answered OK and then raised inside format_marker,
    # reaching badarg by accident through dispatch's outer handler.
    r = resp(sim, ">18 mark")
    assert not r.ok and r.err_code == p.ERROR_CODES["badarg"]
    assert sim.async_lines == []


def test_non_command_is_ignored(sim: mcu_sim.Simulator) -> None:
    assert sim.handle_line("sim alive n=1") == []


def test_unparseable_seq_is_silent(sim: mcu_sim.Simulator) -> None:
    assert sim.handle_line(">notaseq ping") == []


def test_seq_without_command_is_badcmd(sim: mcu_sim.Simulator) -> None:
    # SPEC 2.3: the seq parses, so it gets exactly one response. The sim used to stay
    # silent and leave the daemon waiting out its timeout; the C monitor answers badcmd.
    r = resp(sim, ">5")
    assert not r.ok and r.seq == 5 and r.err_code == p.ERROR_CODES["badcmd"]
    assert sim.handle_line(">0") == []          # seq out of range: still silent
    assert sim.handle_line(">") == []


def test_more_than_twelve_tokens_is_badarg(sim: mcu_sim.Simulator) -> None:
    # SPEC 2.3/5.4: at most 12 tokens including the seq. monitor.c rejects a longer line
    # whole; the sim used to dispatch it, so a command the target refuses "worked" here.
    # `mark` takes free text, so the only thing that can reject these is the token cap.
    twelve = ">1 mark " + " ".join("w" for _ in range(10))               # 12 with the seq
    assert len(p.parse_command(twelve).tokens) + 1 == 12
    assert resp(sim, twelve).ok                                          # dispatched
    thirteen = ">2 mark " + " ".join("w" for _ in range(11))
    r = resp(sim, thirteen)
    assert not r.ok and r.seq == 2 and r.err_code == p.ERROR_CODES["badarg"]


def test_an_over_length_command_is_answered_overflow(sim: mcu_sim.Simulator) -> None:
    # SPEC 2.1/5.4: an inbound line past 255 bytes is discarded to the next LF and
    # answered ERR 8 overflow when the seq survives. The sim used to parse it happily.
    rx = bytearray()
    out = mcu_sim._process_incoming(sim, rx, b">3 mark " + b"x" * 400 + b"\n")
    r = p.parse_response(only(out))
    assert not r.ok and r.seq == 3 and r.err_code == p.ERROR_CODES["overflow"]
    assert rx == b""
    # The discard ends at the LF: the next line is a normal command again.
    assert mcu_sim._process_incoming(sim, rx, b">4 ping\n") == ["<4 OK monitor 1 sim"]
    # No recoverable seq (not a command at all): discarded in silence, like the firmware.
    assert mcu_sim._process_incoming(sim, rx, b"noise " + b"y" * 400 + b"\n") == []
    # A maximal-length line is still accepted (the cap is 255, not 254).
    maximal = ">5 mark " + "z" * (p.MAX_LINE_BYTES - len(">5 mark "))
    assert len(maximal) == p.MAX_LINE_BYTES
    assert mcu_sim._process_incoming(sim, rx, maximal.encode() + b"\n")[0].startswith("<5 OK")
    # Same line from a CRLF sender: monitor.c skips \r before the length test, so counting
    # it here made the sim answer ERR 8 where the firmware answers normally.
    crlf = ">6 mark " + "z" * (p.MAX_LINE_BYTES - len(">6 mark "))
    assert mcu_sim._process_incoming(sim, rx, crlf.encode() + b"\r\n")[0].startswith("<6 OK")


def test_a_peer_that_never_sends_a_newline_cannot_grow_the_buffer(
    sim: mcu_sim.Simulator,
) -> None:
    # A real monitor assembles into a fixed buffer; the sim grew `rx` for as long as the
    # peer kept typing, so 5 MB with no LF became 5 MB of resident memory.
    rx = bytearray()
    for _ in range(50):
        assert mcu_sim._process_incoming(sim, rx, b"a" * 100_000) == []
        assert len(rx) <= p.MAX_LINE_BYTES


def test_i2c_address_out_of_range_is_badarg(sim: mcu_sim.Simulator) -> None:
    # SPEC 2.4: 7-bit addresses only. It used to reach the device lookup and answer
    # `nack no device`, telling a user with a typo that the bus had replied.
    for line in (">19 i2c wr 999 AA", ">20 i2c rd 80 1", ">21 i2c wrrd 100 00 1"):
        r = resp(sim, line)
        assert not r.ok and r.err_code == p.ERROR_CODES["badarg"], line
    assert resp(sim, ">22 i2c rd 7F 1").err_code == p.ERROR_CODES["nack"]   # in range, absent


def test_drop_response_swallows_nth() -> None:
    args = mcu_sim.build_parser().parse_args(["--drop-response", "2"])
    s = mcu_sim.Simulator(args)
    assert s.handle_line(">1 ping") != []   # first answered
    assert s.handle_line(">2 ping") == []   # second dropped
    assert s.handle_line(">3 ping") != []   # third answered


def test_a_gpio_set_triggers_the_debug_burst(sim: mcu_sim.Simulator) -> None:
    # SPEC 7: a burst of debug lines follows any `gpio set`, to exercise interleaving.
    # Driven through _process_incoming, which is where the trigger lives; asserting on
    # burst_debug() alone left `was_gpio_set` free to be False with the suite green.
    out = mcu_sim._process_incoming(sim, bytearray(), b">7 gpio set led 1\n")
    assert out[0].startswith("<7 OK")
    burst = out[1:]
    assert burst == sim.burst_debug()
    assert all(p.classify(line) is p.LineClass.DEBUG for line in burst)


def test_a_plain_command_triggers_no_burst(sim: mcu_sim.Simulator) -> None:
    assert mcu_sim._process_incoming(sim, bytearray(), b">7 ping\n") == ["<7 OK monitor 1 sim"]


# --- CAN filtering (SPEC 2.4) --------------------------------------------------------


def _can_ids(lines: list[str]) -> set[int]:
    frames = [p.parse_can_event(line) for line in lines if line.startswith("!can")]
    assert all(f is not None for f in frames), f"the sim emitted an undecodable !can: {lines!r}"
    return {f.can_id for f in frames}


def _all_can_due(sim: mcu_sim.Simulator) -> None:
    """Make every periodic CAN emission due on the next poll. No sleeping: the schedule is
    the sim's own state, so moving it is the deterministic way to advance its clock."""
    now = time.monotonic()
    sim.next_heartbeat = now
    for cid in sim.next_can:
        sim.next_can[cid] = now


def _rx_count(sim: mcu_sim.Simulator) -> int:
    field = resp(sim, ">99 can stat").data.split()[0]
    return int(field.removeprefix("rx="))


def test_can_filter_decides_which_frames_are_streamed(sim: mcu_sim.Simulator) -> None:
    """SPEC 2.4: `all` at boot, `none` streams nothing, `<id> <mask>` streams the frames
    where `(rx_id & mask) == (id & mask)`. Only the OK response was pinned, so
    _can_passes_filter could `return True` unconditionally with the whole suite green."""
    every_id = {0x100} | {cid for cid, *_ in mcu_sim.CAN_BUS}

    _all_can_due(sim)
    assert _can_ids(sim.poll_events()) == every_id, "`all` is not the boot default"

    assert resp(sim, ">1 can filter none").ok
    _all_can_due(sim)
    before = _rx_count(sim)
    assert _can_ids(sim.poll_events()) == set()
    # The frames arrived and the filter dropped them, rather than never being generated.
    assert _rx_count(sim) > before

    assert resp(sim, ">2 can filter 100 7FF").ok
    _all_can_due(sim)
    assert _can_ids(sim.poll_events()) == {0x100}

    assert resp(sim, ">3 can filter all").ok
    _all_can_due(sim)
    assert _can_ids(sim.poll_events()) == every_id

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


def test_flood_meets_the_requested_rate() -> None:
    # --flood exists so the capture path and the web UI's high-rate behaviour can be
    # exercised without a board that can saturate a link, so the rate it actually
    # produces is the whole point. Driven with a synthetic clock: no real sleeping.
    rate = 5000
    args = mcu_sim.build_parser().parse_args(["--flood", str(rate)])
    sim = mcu_sim.Simulator(args)
    now = sim.next_flood
    lines: list[str] = []
    for _ in range(200):          # 200 passes at 10 ms = 2 s of simulated time
        now += 0.01
        lines.extend(sim._poll_flood(now))

    assert all(line.startswith("flood line ") for line in lines)
    # Within a couple of percent of rate * 2 s; the +1 per pass rounds slightly high.
    assert 2 * rate <= len(lines) <= 2 * rate + 250, f"got {len(lines)} lines"
    # Sequence numbers are unbroken, so a consumer can detect real capture loss.
    seqs = [int(line.split()[2]) for line in lines]
    assert seqs == list(range(1, len(lines) + 1))


def test_flood_off_by_default(sim: mcu_sim.Simulator) -> None:
    assert sim.args.flood == 0
    assert sim._poll_flood(time.monotonic() + 10.0) == []


# --- the serving loops must outlive a failure ----------------------------------------


class _AcceptFailsOnce:
    """A listening socket whose first accept() raises a transient error.

    ECONNABORTED (a peer that resets between connect and accept) and EMFILE (fd
    pressure) both leave the listener perfectly usable.
    """

    def __init__(self, srv: socket.socket, err: int) -> None:
        self._srv = srv
        self._err = err
        self.failures = 0

    def accept(self):
        if self.failures == 0:
            self.failures += 1
            raise OSError(self._err, "simulated transient accept failure")
        return self._srv.accept()

    def __getattr__(self, name):
        return getattr(self._srv, name)


class _CloseFails:
    """A connection whose close() raises, as a socket with unflushed data can."""

    def __init__(self, conn: socket.socket) -> None:
        self._conn = conn
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        raise OSError(errno.EIO, "simulated close failure")

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _ClientCloseFails:
    """A listening socket handing out connections that fail to close."""

    def __init__(self, srv: socket.socket) -> None:
        self._srv = srv
        self.conns: list[_CloseFails] = []

    def accept(self):
        conn, addr = self._srv.accept()
        wrapped = _CloseFails(conn)
        self.conns.append(wrapped)
        return wrapped, addr

    def __getattr__(self, name):
        return getattr(self._srv, name)


def _ping_over_tcp(port: int, timeout: float = 5.0) -> str:
    """Send `>1 ping` and return the response line, or "" if none arrives.

    "" is the healthy-while-dead symptom: the listener is still bound, so connect()
    completes out of the kernel backlog, but no thread is behind it to answer.
    """
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as conn:
        conn.sendall(b">1 ping\n")
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            conn.settimeout(max(0.01, deadline - time.monotonic()))
            try:
                chunk = conn.recv(4096)
            except (TimeoutError, OSError):
                break
            if not chunk:
                break
            buf.extend(chunk)
            for raw in buf.split(b"\n"):
                if raw.startswith(b"<1 "):
                    return raw.decode("ascii", "replace")
    return ""


@pytest.mark.parametrize("err", [errno.ECONNABORTED, errno.EMFILE])
def test_listener_survives_a_transient_accept_error(err: int) -> None:
    """Breaking the accept loop on any OSError left the listener bound with no thread
    behind it: the daemon reconnected to a corpse, saw a healthy port, and exchanged
    nothing ever again. Only a genuine shutdown may end the loop."""
    args = mcu_sim.build_parser().parse_args([])
    srv = mcu_sim.open_tcp_listener(0)
    port = srv.getsockname()[1]
    flaky = _AcceptFailsOnce(srv, err)
    stop = threading.Event()
    thread = threading.Thread(
        target=mcu_sim.serve_listener, args=(args, flaky, stop), daemon=True
    )
    thread.start()
    try:
        assert _ping_over_tcp(port) == "<1 OK monitor 1 sim"
        assert flaky.failures == 1
        assert thread.is_alive()
    finally:
        stop.set()
        thread.join(timeout=5.0)
        srv.close()


def test_listener_survives_a_failing_client_close() -> None:
    """`with conn:` put the implicit close() outside the per-client guard, so an OSError
    from it killed the serving thread the guard had just saved."""
    args = mcu_sim.build_parser().parse_args([])
    srv = mcu_sim.open_tcp_listener(0)
    port = srv.getsockname()[1]
    listener = _ClientCloseFails(srv)
    stop = threading.Event()
    thread = threading.Thread(
        target=mcu_sim.serve_listener, args=(args, listener, stop), daemon=True
    )
    thread.start()
    try:
        assert _ping_over_tcp(port) == "<1 OK monitor 1 sim"   # first client, then closes
        assert _ping_over_tcp(port) == "<1 OK monitor 1 sim"   # served after the failed close
        assert listener.conns[0].close_calls == 1
        assert thread.is_alive()
    finally:
        stop.set()
        thread.join(timeout=5.0)
        for wrapped in listener.conns:
            wrapped._conn.close()   # the wrapper only ever raised; close the real socket
        srv.close()


class _DeadListener:
    """A listener whose accept() always fails. `fd` is what fileno() reports, so the two
    halves of the fd-dead test - a dead-descriptor errno, and a socket already closed
    underneath the loop - can be driven one at a time."""

    def __init__(self, err: int, fd: int) -> None:
        self._err = err
        self._fd = fd
        self.accepts = 0

    def settimeout(self, _timeout: float) -> None:
        pass

    def fileno(self) -> int:
        return self._fd

    def accept(self):
        self.accepts += 1
        raise OSError(self._err, "simulated dead listener")


@pytest.mark.parametrize("err,fd", [(errno.EBADF, 3), (errno.ECONNABORTED, -1)])
def test_listener_stops_when_the_socket_is_gone(err: int, fd: int) -> None:
    """The mirror of test_pty_stops_when_the_master_is_gone, and the same distinction: a
    dead descriptor is not a transient accept failure. Retrying one spins at
    1/ERROR_BACKOFF_S forever printing the same line, with no client and no way to get one.

    Runs with `stop=None`, the standalone `serve_tcp` shape. Every other test reaches this
    loop with a stop event, and SimHandle.stop() sets it before closing the socket, so the
    loop exits on the event whatever the break below does."""
    args = mcu_sim.build_parser().parse_args([])
    dead = _DeadListener(err, fd)
    thread = threading.Thread(target=mcu_sim.serve_listener, args=(args, dead, None), daemon=True)
    thread.start()
    thread.join(timeout=5.0)
    assert not thread.is_alive(), "the accept loop is retrying a listener that is gone"
    assert dead.accepts >= 1

@pytest.mark.skipif(os.name != "posix", reason="pty transport is POSIX-only")
def test_pty_session_survives_a_failing_poll(tmp_path, monkeypatch) -> None:
    """The TCP path has kept serving across a failed session since the healthy-while-dead
    fix; the pty path had no guard at all, so the same exception ended the process."""
    import serial

    symlink = tmp_path / "sim-pty"
    args = mcu_sim.build_parser().parse_args(["--pty", "--symlink", str(symlink)])
    done = threading.Event()
    real_poll = mcu_sim.Simulator.poll_events
    failed: list[bool] = []

    def flaky_poll(self):
        if not failed:
            failed.append(True)
            raise RuntimeError("simulated event-poll failure")
        if done.is_set():
            raise KeyboardInterrupt   # ends serve_pty cleanly, leaving no thread behind
        return real_poll(self)

    monkeypatch.setattr(mcu_sim.Simulator, "poll_events", flaky_poll)
    thread = threading.Thread(target=mcu_sim.serve_pty, args=(args,), daemon=True)
    thread.start()
    ser = None
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not symlink.is_symlink():
            time.sleep(0.02)
        # Without the guard the exception unwinds out of serve_pty, whose finally removes
        # the symlink and closes the pty: the session is gone rather than restarted.
        assert symlink.is_symlink(), "serve_pty did not survive the failing poll"
        ser = serial.Serial(str(symlink), baudrate=115200, timeout=1.0)
        ser.write(b">1 ping\n")
        buf = bytearray()
        deadline = time.monotonic() + 5.0
        line = ""
        while time.monotonic() < deadline and not line:
            buf.extend(ser.read_until(b"\n"))
            for raw in buf.split(b"\n"):
                if raw.startswith(b"<1 "):
                    line = raw.decode("ascii", "replace")
        assert line == "<1 OK monitor 1 sim"
        assert failed, "the injected failure never ran"
    finally:
        done.set()
        if ser is not None:
            ser.close()
        thread.join(timeout=5.0)


@pytest.mark.skipif(os.name != "posix", reason="pty transport is POSIX-only")
def test_pty_stops_when_the_master_is_gone(tmp_path, monkeypatch) -> None:
    """The per-session guard must make the same distinction the accept loop makes: a dead
    descriptor is not a failed session. Restarting on one spins at 10 Hz forever, printing
    the same error, with no client and no way to get one."""
    symlink = tmp_path / "sim-pty"
    args = mcu_sim.build_parser().parse_args(["--pty", "--symlink", str(symlink)])

    def dead_fd_poll(self):
        raise OSError(errno.EBADF, "Bad file descriptor")

    monkeypatch.setattr(mcu_sim.Simulator, "poll_events", dead_fd_poll)
    thread = threading.Thread(target=mcu_sim.serve_pty, args=(args,), daemon=True)
    thread.start()
    thread.join(timeout=5.0)
    assert not thread.is_alive(), "serve_pty is restarting the session on a dead master"


def test_pty_is_refused_on_windows(monkeypatch, capsys) -> None:
    """The gate `serve_pty` opens with, driven from either platform: without it the pty
    import fails at runtime instead of the user being told why. `os.name` is patched
    rather than the module's own copy because sim.py reads it off the module."""
    monkeypatch.setattr(mcu_sim.os, "name", "nt")
    args = mcu_sim.build_parser().parse_args(["--pty"])
    assert mcu_sim.serve_pty(args) == 2
    assert "Windows" in capsys.readouterr().err

# --- emitted lines stay inside the protocol limits ------------------------------------


def test_emitted_lines_are_bounded_to_the_spec_limit(capsys) -> None:
    """SPEC 2.1 caps a line at 255 bytes and a real monitor's TX buffer enforces that
    physically; the sim must not be able to hand the host a longer one."""
    encoded = mcu_sim.encode_lines(["x" * 300, ">2 short"])

    lines = encoded.decode("ascii").rstrip("\n").split("\n")
    assert lines == ["x" * p.MAX_LINE_BYTES, ">2 short"]
    assert not any(p.is_oversized(line) for line in lines)
    assert "truncating" in capsys.readouterr().err


def test_an_oversized_response_is_answered_overflow_not_truncated() -> None:
    """SPEC 2.3: a response that will not fit is `ERR 8 overflow`; a cut hex payload
    cannot be told from a short one. Events keep the truncation SPEC 2.1 allows."""
    over = "<9 OK " + "AB" * 200
    assert mcu_sim.encode_lines([over]) == b"<9 ERR 8 overflow\n"
    event = "!m " + "e" * 300
    assert mcu_sim.encode_lines([event]).rstrip(b"\n") == event.encode()[:p.MAX_LINE_BYTES]
