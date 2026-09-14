"""Pre-release round 2026-09-15, link batch: shutdown hook, `_serve` exit codes, argv
grammar, write health across threads and reattach, and the reattach plot definitions."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import signal
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import serial
import uvicorn

from mcuscope import daemon as daemon_mod
from mcuscope import protocol as p
from mcuscope import serial_link
from mcuscope import sim as mcu_sim
from mcuscope.link import SourceLink
from mcuscope.serial_link import PortError, SerialPort
from mcuscope.store import Store


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


# -- A-7: the shutdown hook runs as a signal handler ------------------------------------


async def test_a_signal_landing_in_loop_code_schedules_the_sentinel() -> None:
    """A real signal runs `handle_exit` between two bytecodes of the running task. Calling
    `stop_subscribers` there can interleave with the fan-out on the same queue; it has to
    run as a loop callback, where no task is current."""
    ran_in: list[object] = []

    class _Store:
        def stop_subscribers(self) -> None:
            ran_in.append(asyncio.current_task())

    async def app(scope, receive, send) -> None:
        pass

    app.state = SimpleNamespace(store=_Store())
    server = daemon_mod.Server(uvicorn.Config(app))
    previous = signal.signal(signal.SIGTERM, server.handle_exit)
    try:
        signal.raise_signal(signal.SIGTERM)
        assert server.should_exit, "the handler did not run"
        assert ran_in == [], "stop_subscribers ran inside the signal handler"
        await _until(lambda: ran_in)
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert ran_in == [None], "stop_subscribers ran inside a task, not as a loop callback"


# -- C-9 / F-31: `_serve` exit codes, with the real uvicorn server ----------------------


async def _noop_app(scope, receive, send) -> None:
    pass


_SERVE_KW = dict(log_level="critical", lifespan="off", use_colors=False)


def _serve_outcome(**kw) -> BaseException | None:
    # BaseException: a KeyboardInterrupt escaping _serve must fail the test, not end the run.
    try:
        daemon_mod._serve(_noop_app, **_SERVE_KW, **kw)
    except BaseException as exc:
        return exc
    return None


def test_serve_exits_3_when_the_bind_fails() -> None:
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    try:
        outcome = _serve_outcome(host="127.0.0.1", port=holder.getsockname()[1])
    finally:
        holder.close()
    assert isinstance(outcome, SystemExit) and outcome.code == 3, repr(outcome)


def test_serve_exits_3_on_ctrl_c_before_the_server_started(monkeypatch) -> None:
    """uvicorn exits 3 itself on a bind failure; this is the path only `_serve`'s own
    check covers: Ctrl-C during startup is swallowed, and must not read as a clean stop."""

    async def interrupted(self, sockets=None) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(uvicorn.Server, "startup", interrupted)
    outcome = _serve_outcome(host="127.0.0.1", port=0)
    assert isinstance(outcome, SystemExit) and outcome.code == 3, repr(outcome)


def test_serve_returns_normally_once_the_server_started(monkeypatch) -> None:
    async def stop_at_once(self) -> None:
        pass

    monkeypatch.setattr(uvicorn.Server, "main_loop", stop_at_once)
    assert _serve_outcome(host="127.0.0.1", port=0) is None


# -- C-7: integer and float argv on the wire grammar ------------------------------------


NOT_DECIMAL = ["١٨٦٢٣", " 5", "5 ", "+5", "1_0", "0x10", "5.0", "", "1" * 21]


@pytest.mark.parametrize("flag", ["--tcp-port", "--drop-response", "--flood"])
@pytest.mark.parametrize("value", NOT_DECIMAL)
def test_sim_integer_flags_refuse_what_int_would_take(flag, value, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        mcu_sim.build_parser().parse_args([f"{flag}={value}"])
    assert exc.value.code == 2
    assert f"argument {flag}: not a decimal integer" in capsys.readouterr().err


@pytest.mark.parametrize(("flag", "value", "bound"), [
    ("--tcp-port", "-1", "must be 0..65535, got -1"),
    ("--tcp-port", "65536", "must be 0..65535, got 65536"),
    ("--drop-response", "-1", "must be >= 0, got -1"),
    ("--flood", "-5", "must be >= 0, got -5"),
])
def test_sim_integer_flags_refuse_out_of_range(flag, value, bound, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        mcu_sim.build_parser().parse_args([f"{flag}={value}"])
    assert exc.value.code == 2
    assert f"argument {flag}: {bound}" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "1e999", "-1", "1_0", " 1", "٥", "+1"])
def test_flap_refuses_a_value_that_is_not_a_finite_duration(value, capsys) -> None:
    """`nan > 0` is False, so `--flap nan` switched flapping off without a word."""
    with pytest.raises(SystemExit) as exc:
        mcu_sim.build_parser().parse_args([f"--flap={value}"])
    assert exc.value.code == 2
    assert "argument --flap: must be a finite number of seconds >= 0" in capsys.readouterr().err


def test_sim_flags_still_take_their_documented_values() -> None:
    args = mcu_sim.build_parser().parse_args(
        ["--tcp-port", "0", "--drop-response", "2", "--flood", "20000", "--flap", "0.5"]
    )
    assert (args.tcp_port, args.drop_response, args.flood, args.flap) == (0, 2, 20000, 0.5)
    assert mcu_sim.build_parser().parse_args(["--tcp-port", "65535", "--flap", "0"]).flap == 0.0


@pytest.mark.parametrize("value", ["١٨٦١٣", " 8558", "+8558", "8_558", "0", "65536", "-1"])
def test_daemon_port_flag_is_on_the_grammar_and_bounded(value, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        daemon_mod.build_parser().parse_args([f"--port={value}"])
    assert exc.value.code == 2
    assert "argument --port: " in capsys.readouterr().err
    assert daemon_mod.build_parser().parse_args(["--port", "8558"]).port == 8558


# -- C-6: write health read-modify-write against a second writer and the close ----------


class _SlowText(serial.SerialException):
    """A write error whose text is read while the failure is being booked, which is the
    one hook between the health load and its store. Blocks once, bounded."""

    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        super().__init__("Write timeout")
        self._entered, self._release, self._first = entered, release, True

    def __str__(self) -> str:
        if self._first:
            self._first = False
            self._entered.set()
            self._release.wait(0.5)
        return "Write timeout"


class _FailingLink:
    def __init__(self, first_error: Exception | None = None) -> None:
        self._first = first_error

    def write(self, data: bytes) -> None:
        err, self._first = self._first, None
        raise err or serial.SerialTimeoutException("Write timeout")

    def close(self) -> None:
        pass


def _write_quietly(port: SerialPort) -> None:
    with contextlib.suppress(PortError):
        port._write_bytes(b">1 ping\n")


def test_two_failing_writes_in_two_threads_both_count() -> None:
    port = SerialPort(None, None, "board")
    entered, release = threading.Event(), threading.Event()
    port._link = _FailingLink(_SlowText(entered, release))
    first = threading.Thread(target=_write_quietly, args=(port,))
    first.start()
    assert entered.wait(5)

    def second() -> None:
        _write_quietly(port)
        release.set()

    other = threading.Thread(target=second)
    other.start()
    other.join(5)
    first.join(5)
    assert port.status()["write_failures"] == 2


def test_a_close_during_a_failing_write_ends_the_streak_after_it() -> None:
    """The reader's disconnect path, in its order: locked close, then `_on_disconnect`."""
    port = SerialPort(None, None, "board")
    entered, release = threading.Event(), threading.Event()
    link = _FailingLink(_SlowText(entered, release))
    port._link = link
    writer = threading.Thread(target=_write_quietly, args=(port,))
    writer.start()
    assert entered.wait(5)

    def disconnect() -> None:
        port._close_link_locked(link)
        port._on_disconnect()
        release.set()

    closer = threading.Thread(target=disconnect)
    closer.start()
    closer.join(5)
    writer.join(5)
    st = port.status()
    assert (st["write_failures"], st["write_failing_since"]) == (0, None), st
    assert st["last_write_error"] == "Write timeout"


def test_a_late_disconnect_callback_keeps_the_next_links_streak() -> None:
    """`_on_disconnect` is posted to the loop and can run after the reader has reopened; a
    write that already failed on the new link is a streak of that link, not the old one."""
    port = SerialPort(None, None, "board")
    old = _FailingLink()
    port._link = old
    port._close_link_locked(old)
    port._link = _FailingLink()           # reopened before the loop ran the callback
    _write_quietly(port)
    port._on_disconnect()
    assert port.status()["write_failures"] == 1


# -- C-5 and C-4: what a reattach of the same alias keeps --------------------------------


class _Pipe:
    """A far end emitting what the test queues; `fail_writes` makes every write time out."""

    def __init__(self) -> None:
        self.out: collections.deque[bytes] = collections.deque()
        self.fail_writes = False

    def feed(self, data: bytes) -> bytes:
        if self.fail_writes:
            raise serial.SerialTimeoutException("Write timeout")
        return b""

    def poll(self) -> bytes:
        chunks = b""
        while self.out:
            chunks += self.out.popleft()
        return chunks


async def _manager(tmp_path, pipe: _Pipe):
    store = Store(str(tmp_path / "link.db"))
    await store.start()
    mgr = serial_link.PortManager(
        store, asyncio.get_running_loop(),
        open_link_fn=lambda dev, baud: SourceLink(pipe, device=dev),
    )
    return store, mgr


@pytest.mark.parametrize("path", ["replace", "detach then attach"])
async def test_a_reattach_keeps_the_last_write_error(tmp_path, path) -> None:
    pipe = _Pipe()
    store, mgr = await _manager(tmp_path, pipe)
    try:
        port = await mgr.attach("b", device="sim://x", identify=False)
        await _until(lambda: port.connected)
        pipe.fail_writes = True
        with pytest.raises(PortError, match="Write timeout"):
            await port.send_raw("x")
        before = port.status()
        assert before["write_failures"] == 1
        pipe.fail_writes = False
        if path != "replace":
            await mgr.detach("b")
        again = await mgr.attach("b", device="sim://x", identify=False)
        st = again.status()
        assert st["last_write_error"] == "Write timeout"
        assert st["last_write_error_ts"] == before["last_write_error_ts"]
        # The streak ends with the attachment, as it does with a disconnect.
        assert (st["write_failures"], st["write_failing_since"]) == (0, None)
    finally:
        await mgr.stop_all()
        await store.stop()


async def test_a_reattach_decodes_with_a_def_stored_after_its_prime(tmp_path, monkeypatch) -> None:
    """The old port keeps capturing while the new one primes; a redefinition it learns in
    that window is newer than anything the prime read."""
    pipe = _Pipe()
    store, mgr = await _manager(tmp_path, pipe)
    try:
        old = await mgr.attach("b", device="sim://x", identify=False)
        pipe.out.append(b"!pd 0 x:u2\n")
        await _until(lambda: old.plot_decoder.definition("0") is not None)

        prime = SerialPort.prime_plot_defs

        async def prime_then_redefine(self) -> None:
            await prime(self)
            pipe.out.append(b"!pd 0 x:s2*0.5\n")
            await _until(lambda: old.plot_decoder.definition("0").channels[0].type == "s2")

        monkeypatch.setattr(SerialPort, "prime_plot_defs", prime_then_redefine)
        new = await mgr.attach("b", device="sim://x", identify=False)
        monkeypatch.setattr(SerialPort, "prime_plot_defs", prime)
        assert new is not old

        pipe.out.append(b"!ps 0 10 FFFE\n")

        def values() -> list[float]:
            return [r[0] for r in store._conn.execute(
                "SELECT value FROM plot_points WHERE name = 'x'")]

        await _until(values)
        assert values() == [-1.0], "decoded with the definition the prime read"
    finally:
        await mgr.stop_all()
        await store.stop()


def test_adopt_replaces_same_sid_and_keeps_the_rest() -> None:
    primed, live = p.PlotDecoder(), p.PlotDecoder()
    primed.learn("!pd 0 x:u2")
    primed.learn("!pd 7 only_primed:u1")
    live.learn("!pd 0 x:s2*0.5")
    primed.adopt(live)
    assert primed.definition("0").channels[0].type == "s2"
    assert primed.definition("7") is not None
    assert primed.points("!ps 0 10 FFFE") == [(16, "0", "x", -1.0)]


# -- F-32: a bits group's own name is not a declaration of that name -------------------


def test_declared_kinds_skips_the_name_of_a_bits_group() -> None:
    dec = p.PlotDecoder()
    dec.learn("!pd 0 flags:u1:/a,b")
    assert dec.declared_kinds("flags") == []
    assert dec.declared_kinds("a") == ["bit"]
    dec.learn("!pd 1 flags:u2")
    assert dec.declared_kinds("flags") == ["analog"]
