"""The link that has a source behind it instead of a port.

`SourceLink` is where the read/drain contract is implemented once: which byte answers the
read, what the drain appends, and where an error surfaces. Everything that used to need a
real socket to reach the reader's success path goes through here, so the contract itself
is asserted here rather than inferred from a running stack.
"""

from __future__ import annotations

import asyncio

import httpx
import serial

from mcuscope import sim as mcu_sim
from mcuscope.config import Config, PortConfig, ServerConfig, StorageConfig
from mcuscope.link import READ_CHUNK, BurstThenError, SourceLink, is_url_device, validate_device
from mcuscope.server import create_app
from tests.support import Scripted


def read_burst(link: SourceLink) -> bytes:
    """One reader-loop iteration: read(1) anchors the timestamp, then drain takes the rest."""
    buf = bytearray(link.read(1))
    link.drain(buf)
    return bytes(buf)


def test_the_first_byte_answers_the_read_and_the_drain_takes_the_rest() -> None:
    link = SourceLink(Scripted([b"hello\nworld\n"]), idle=0)
    assert read_burst(link) == b"hello\nworld\n"


def test_nothing_due_is_a_read_timeout_not_an_error() -> None:
    link = SourceLink(Scripted([b"", b"late\n"]), idle=0)
    assert link.read(1) == b""
    assert read_burst(link) == b"late\n"


def test_a_source_that_raises_surfaces_from_the_read() -> None:
    link = SourceLink(Scripted([serial.SerialException("gone")]), idle=0)
    try:
        link.read(1)
    except serial.SerialException as exc:
        assert "gone" in str(exc)
    else:
        raise AssertionError("the read must not swallow the source's failure")


def test_a_burst_that_dies_in_the_drain_still_delivers_its_bytes() -> None:
    # At EOF a socket:// port reports readable and pyserial raises from inside the drain
    # with complete lines already buffered (SPEC 3.2 wants those logged, not dropped).
    link = SourceLink(
        Scripted([BurstThenError(b"last words\n", serial.SerialException("eof"))]), idle=0
    )
    buf = bytearray(link.read(1))
    try:
        link.drain(buf)
    except serial.SerialException:
        pass
    else:
        raise AssertionError("the drain must report the failure")
    assert bytes(buf) == b"last words\n", "the burst was thrown away with the error"


def test_the_drain_is_bounded_by_the_chunk_size() -> None:
    link = SourceLink(Scripted([b"x" * (READ_CHUNK * 2)]), idle=0)
    assert len(read_burst(link)) == READ_CHUNK


def test_a_write_is_answered_before_the_next_unprompted_line() -> None:
    # Ordering the far end guarantees: a command's response precedes whatever the next poll
    # would have emitted, or a response arrives interleaved into its own echo.
    src = Scripted([b"async\n"])
    src.replies[b"> ping\n"] = b"< pong\n"
    link = SourceLink(src, idle=0)
    link.write(b"> ping\n")
    assert read_burst(link) == b"< pong\n"
    assert read_burst(link) == b"async\n"
    assert link.written == b"> ping\n"


def test_a_closed_link_refuses_writes() -> None:
    link = SourceLink(Scripted([]), idle=0)
    link.close()
    assert link.closed
    try:
        link.write(b"> ping\n")
    except serial.SerialException:
        pass
    else:
        raise AssertionError("a closed link must not accept a write")


def test_cancel_reports_what_the_transport_can_actually_do() -> None:
    # Default False models a URL transport, whose pyserial handler has no cancel_read: a
    # test must not get a capability the transport it stands in for does not have.
    plain = SourceLink(Scripted([]), idle=0)
    assert plain.cancel_read() is False
    assert plain.cancel_write() is False
    native = SourceLink(Scripted([]), idle=0, cancellable=True)
    assert native.cancel_read() is True
    assert native.cancelled_reads == 1


# -- the simulator as a source ----------------------------------------------------------


def test_the_sim_answers_a_command_over_a_link_with_no_socket() -> None:
    link = mcu_sim.open_sim_link()
    link.write(b"> 1 gpio set led 1\n")
    text = read_burst(link).decode()
    # The response, plus the debug burst SPEC 7 has the sim emit right after a gpio set.
    assert text.startswith("<1 OK"), text
    assert "gpio-burst" in text, text


def test_the_sim_emits_its_heartbeat_unprompted() -> None:
    # The 10 Hz CAN heartbeat is wall-clock paced inside Simulator, so this waits on the
    # sim's own schedule the way the reader thread does.
    link = mcu_sim.open_sim_link()
    seen = b""
    for _ in range(60):
        seen += read_burst(link)
        if b"!can" in seen:
            break
    assert b"!can" in seen, seen[:200]


def test_each_link_gets_its_own_simulator() -> None:
    # _serve_socket_client builds a fresh Simulator per connection, so a reconnect sees a
    # far end that restarted clean; the in-process link must not share one either.
    a, b = mcu_sim.open_sim_link(), mcu_sim.open_sim_link()
    a.write(b"> 1 gpio set led 1\n")
    read_burst(a)
    b.write(b"> 1 gpio get led\n")
    assert b"<1 OK 0" in read_burst(b), "the second link inherited the first's state"


# -- the injection seam through the app ---------------------------------------------------


async def test_the_app_can_be_given_the_link_its_ports_open(tmp_path) -> None:
    """create_app -> PortManager -> SerialPort, with no socket anywhere in the path.

    SerialPort has accepted `open_link_fn` since the link seam landed, but PortManager
    hard-coded the constructor, so nothing above it could reach the seam - which is why the
    only in-process transport a whole-stack test could use was a loopback socket.
    """
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "capture.db"), retention_days=7),
        ports=[PortConfig(alias="sim", device="sim://board", baud=115200, autoconnect=True)],
    )
    app = create_app(config, open_link_fn=mcu_sim.open_sim_link)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        for _ in range(100):
            port = (await client.get("/status")).json()["ports"][0]
            if port["connected"] and port["lines_rx"]:
                break
            await asyncio.sleep(0.02)
        assert port["connected"], "the port never opened its link"
        assert port["device"] == "sim://board"

        resp = await client.post("/cmd", json={"cmd": "ping", "timeout_ms": 2000})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == "monitor 1 sim"


def test_a_sim_device_validates_and_reads_as_a_remote_transport() -> None:
    # Presence-gating stats a bare path and finds nothing, so the reader would never even
    # try to open it. A sim device has no node to stat, exactly like the socket:// it stands
    # in for, and the exponential backoff stays in charge.
    validate_device("sim://board")
    assert is_url_device("sim://board")
