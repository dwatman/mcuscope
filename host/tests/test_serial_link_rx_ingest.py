"""SerialPort ingest: decode what is stored (SPEC 2.1), the U+FFFD replacement, the unstorable
episode and the ad-hoc name cap (SPEC 2.2, 2.5)."""

from __future__ import annotations

import asyncio
import json
import pathlib
import time

import pytest

from mcuscope import protocol as p
from mcuscope.serial_link import SerialPort
from mcuscope.store import Store

CASES = json.loads(
    (pathlib.Path(__file__).parent / "plot_grammar_cases.json").read_text(encoding="utf-8")
)


async def _port(tmp_path, name: str = "ing.db") -> tuple[Store, SerialPort]:
    store = Store(str(tmp_path / name))
    await store.start()
    return store, SerialPort(store, asyncio.get_running_loop(), "board", identify=False)


async def _ingest(port: SerialPort, data: bytes) -> None:
    """Wire bytes through the reader's framing, then the consumer's store path."""
    port._on_bytes(time.time(), data)
    batch = list(port._rx_lines)
    port._rx_lines.clear()
    await port._store_rx_batch(batch)
    await asyncio.gather(*list(port._bg_tasks))


def _rows(store: Store, chan: str) -> list[str]:
    rows, _more = store.query_lines(chans=[chan], limit=1000, order="asc")
    return [r["raw"] for r in rows]


@pytest.mark.parametrize("case", CASES["ingest"], ids=lambda c: repr(c["wire"]))
async def test_ingest_decodes_the_text_that_is_stored(tmp_path, case) -> None:
    store, port = await _port(tmp_path)
    try:
        if case["kind"] == "plot":
            await _ingest(port, case["wire"].encode())
            names = [c["name"] for c in await store.query_plot_channels_safe()]
            assert names == case["points"], case["why"]
        elif case["kind"] == "def":
            await _ingest(port, case["wire"].encode())
            assert port.plot_decoder.definition("0") is not None, case["why"]
        else:
            await _ingest(port, case["wire"].encode())
            frames, _more = store.query_can_frames()
            assert [f["can_id"] for f in frames] == [case["can_id"]], case["why"]
        # The stored row holds no CR or LF: one row is one line.
        assert all("\r" not in r and "\n" not in r for r in _rows(store, "event"))
    finally:
        await store.stop()


def test_the_ingest_section_covers_every_kind() -> None:
    assert {c["kind"] for c in CASES["ingest"]} == {"plot", "def", "can"}


async def test_a_replaced_byte_is_counted_and_announced_once_per_episode(tmp_path) -> None:
    store, port = await _port(tmp_path)
    try:
        await _ingest(port, b"t=25\xb0C\nx\xff\n")
        assert port.rx_replaced == 2
        assert port.status()["rx_replaced"] == 2
        assert _rows(store, "debug") == ["t=25�C", "x�"]
        notice = [r for r in _rows(store, "sys") if "U+FFFD" in r]
        assert notice == ["port board: received bytes above 0x7F, stored as U+FFFD and counted "
                          "in rx_replaced; a baud mismatch looks like this"]
        await _ingest(port, b"more\xb0\n")
        assert sum("U+FFFD" in r for r in _rows(store, "sys")) == 1   # same episode
        await _ingest(port, b"clean\n")
        await _ingest(port, b"again\xb0\n")
        assert sum("U+FFFD" in r for r in _rows(store, "sys")) == 2   # a new episode
        assert port.rx_replaced == 4
    finally:
        await store.stop()


async def test_a_clean_ascii_line_is_not_counted_as_replaced(tmp_path) -> None:
    store, port = await _port(tmp_path)
    try:
        await _ingest(port, b"plain\n")
        assert port.rx_replaced == 0
        assert not [r for r in _rows(store, "sys") if "U+FFFD" in r]
    finally:
        await store.stop()


async def test_an_unstorable_episode_is_written_when_a_line_stores_again(tmp_path) -> None:
    store, port = await _port(tmp_path)
    real = store.submit_line_nowait
    failing = [True]

    def flaky(**kw):
        if failing[0]:
            raise RuntimeError("disk is full")
        return real(**kw)

    store.submit_line_nowait = flaky
    try:
        await _ingest(port, b"a\nb\nc\n")
        assert port.rx_dropped == 3
        # Nothing was recorded while the store refused: the notice would have gone the same way.
        assert not [r for r in _rows(store, "sys") if "could not be stored" in r]
        failing[0] = False
        await _ingest(port, b"back\n")
        (notice,) = [r for r in _rows(store, "sys") if "could not be stored" in r]
        assert notice.startswith("port board: 3 rx lines could not be stored between ")
        assert notice.endswith(": disk is full")
        assert _rows(store, "debug") == ["back"]
        # A second episode is its own row, and a single line reads as singular.
        failing[0] = True
        await _ingest(port, b"d\n")
        failing[0] = False
        await _ingest(port, b"back2\n")
        notices = [r for r in _rows(store, "sys") if "could not be stored" in r]
        assert len(notices) == 2
        assert notices[1].startswith("port board: 1 rx line could not be stored between ")
    finally:
        store.submit_line_nowait = real
        await store.stop()


async def test_a_settle_failure_also_counts_in_the_episode(tmp_path) -> None:
    store, port = await _port(tmp_path)
    real = store.submit_line_nowait

    def fut_fails(**kw):
        fut = asyncio.get_running_loop().create_future()
        fut.set_exception(RuntimeError("write failed"))
        return fut

    store.submit_line_nowait = fut_fails
    try:
        await _ingest(port, b"x\n")
        store.submit_line_nowait = real
        await _ingest(port, b"y\n")
        (notice,) = [r for r in _rows(store, "sys") if "could not be stored" in r]
        assert "1 rx line could not be stored" in notice
        assert notice.endswith(": write failed")
    finally:
        store.submit_line_nowait = real
        await store.stop()


def _names(n: int) -> bytes:
    return b"".join(f"!p 1 n{i}=1\n".encode() for i in range(n))


def _plotted(store: Store) -> set[str]:
    return {r[0] for r in store._conn.execute("SELECT DISTINCT name FROM plot_points")}


async def test_the_port_admits_a_bounded_number_of_names(tmp_path) -> None:
    store, port = await _port(tmp_path)
    try:
        await _ingest(port, _names(p.ADHOC_NAMES_MAX))
        assert port.plot_name_refused == 0 and len(port.plot_names) == p.ADHOC_NAMES_MAX
        await _ingest(port, b"!p 2 n0=1 n1=2\n")              # known names still plot
        # All or nothing: one new name refuses the line, known names with it, none admitted.
        await _ingest(port, b"!p 3 n0=7 fresh=2\n!p 4 fresh=2\n")
        assert port.plot_name_refused == 2
        assert "fresh" not in port.plot_names and "fresh" not in _plotted(store)
        n0 = store._conn.execute(
            "SELECT tick_ms FROM plot_points WHERE name = 'n0' ORDER BY line_id").fetchall()
        assert [t for (t,) in n0] == [1, 2]   # line 3's known name was not plotted either
        # A malformed `!p` is no name; a typed sample with a new channel is one, and refused.
        await _ingest(port, b"!p 5 bad\n!pd 0 v:u1\n!ps 0 6 05\n")
        assert port.plot_name_refused == 3
        assert "v" not in _plotted(store)
    finally:
        await store.stop()


async def test_the_name_cap_notice_is_written_once_however_lines_interleave(tmp_path) -> None:
    store, port = await _port(tmp_path)
    try:
        await _ingest(port, _names(p.ADHOC_NAMES_MAX))
        await _ingest(port, b"!p 2 over1=1\n!p 3 over2=1\n")
        assert port.plot_name_refused == 2
        assert port.status()["plot_name_refused"] == 2
        events = _rows(store, "event")
        assert "!p 2 over1=1" in events and "!p 3 over2=1" in events   # stored, as text
        assert len(await store.query_plot_channels_safe()) == p.ADHOC_NAMES_MAX
        # A steady channel between new names does not reopen the notice: no slot ever frees.
        for i in range(5):
            await _ingest(port, f"!p {10 + i} n0=1\n!pd 1 w:u1\n!ps 1 1 01\n".encode())
            await _ingest(port, f"!p {20 + i} run{i}=1\n".encode())
        assert port.plot_name_refused == 12   # 2 + 5 new names + 5 typed samples (new name `w`)
        (notice,) = [r for r in _rows(store, "sys") if "distinct plot names" in r]
        assert notice.endswith("not plotted (counted in plot_name_refused)")
    finally:
        await store.stop()


async def test_a_line_the_store_refuses_spends_no_name_slot(tmp_path) -> None:
    store, port = await _port(tmp_path)
    real = store.submit_line_nowait

    def refuse(**kw):
        raise RuntimeError("disk is full")

    def fut_fails(**kw):
        fut = asyncio.get_running_loop().create_future()
        fut.set_exception(RuntimeError("write failed"))
        return fut

    try:
        store.submit_line_nowait = refuse
        await _ingest(port, b"!p 1 ghost=1\n")
        store.submit_line_nowait = fut_fails
        await _ingest(port, b"!p 2 ghost2=1\n")
        assert port.rx_dropped == 2 and not port.plot_names
        # Positive control: the same line, stored, does take its slot.
        store.submit_line_nowait = real
        await _ingest(port, b"!p 3 ghost=1\n")
        assert port.plot_names == {"ghost"}
    finally:
        store.submit_line_nowait = real
        await store.stop()


async def test_an_unstorable_episode_names_its_first_and_last_drop(
    tmp_path, monkeypatch
) -> None:
    from mcuscope import serial_link

    store, port = await _port(tmp_path)
    real = store.submit_line_nowait
    # A value set per batch, not an iterator: `time` is the shared module, so every other
    # caller in the process (the store's background tasks) reads this clock too.
    now = [0.0]
    monkeypatch.setattr(serial_link.time, "time", lambda: now[0])

    class Silent(Exception):
        pass

    def refuse(**kw):
        raise Silent()

    def stamp(t: float) -> str:
        return time.strftime("%H:%M:%S", time.localtime(t))

    try:
        store.submit_line_nowait = refuse
        for t, line in ((1_000_000.0, "a"), (1_000_100.0, "b"), (1_000_200.0, "c")):
            now[0] = t
            await port._store_rx_batch([(0.0, line)])
        store.submit_line_nowait = real
        now[0] = 1_000_500.0
        await port._store_rx_batch([(0.0, "back")])
        await asyncio.gather(*list(port._bg_tasks))
        (notice,) = [r for r in _rows(store, "sys") if "could not be stored" in r]
        assert notice == (f"port board: 3 rx lines could not be stored between "
                          f"{stamp(1_000_000.0)} and {stamp(1_000_200.0)}: Silent")
    finally:
        store.submit_line_nowait = real
        await store.stop()


async def test_an_episode_open_at_stop_with_the_store_still_failing_is_logged_whole(
    tmp_path, monkeypatch, caplog
) -> None:
    """The row is refused by the same store, so the log line must carry its window and cause."""
    from mcuscope import serial_link
    from mcuscope.store import StoreError

    store, port = await _port(tmp_path)
    real_submit, real_add = store.submit_line_nowait, store.add_line
    now = [0.0]
    monkeypatch.setattr(serial_link.time, "time", lambda: now[0])

    def refuse(**kw):
        raise StoreError("disk is full")

    async def refuse_add(**kw):
        raise StoreError("disk is full")

    def stamp(t: float) -> str:
        return time.strftime("%H:%M:%S", time.localtime(t))

    try:
        store.submit_line_nowait, store.add_line = refuse, refuse_add
        for t, line in ((1_000_000.0, "a"), (1_000_100.0, "b")):
            now[0] = t
            await port._store_rx_batch([(0.0, line)])
        with caplog.at_level("WARNING"):
            await port.stop("detach")
        assert (f"port board: 2 rx lines could not be stored between {stamp(1_000_000.0)} "
                f"and {stamp(1_000_100.0)}: disk is full") in caplog.text
        store.submit_line_nowait, store.add_line = real_submit, real_add
        assert not [r for r in _rows(store, "sys") if "could not be stored" in r]
    finally:
        store.submit_line_nowait, store.add_line = real_submit, real_add
        await store.stop()


async def test_send_raw_returns_the_tx_echo_row(tmp_path) -> None:
    store, port = await _port(tmp_path)

    class Wire:
        def write(self, data: bytes) -> None:
            pass

    port._link = Wire()
    try:
        row = await port.send_raw("hello")
        assert row is not None and row["raw"] == "hello" and row["dir"] == "tx"
        stored, _more = store.query_lines(chans=["cmd"], limit=10, order="asc")
        assert [r["id"] for r in stored] == [row["id"]]
        assert await port.send_raw("quiet", log=False) is None
    finally:
        await store.stop()


async def test_the_counters_and_the_name_set_survive_a_re_attach() -> None:
    from mcuscope.serial_link import PortManager
    from tests.support import UNOPENABLE

    store = Store(":memory:")
    await store.start()
    mgr = PortManager(store, asyncio.get_running_loop())
    try:
        port = await mgr.attach("t", device=UNOPENABLE)
        port.rx_replaced, port.plot_name_refused = 7, 3
        port.plot_names.update(f"n{i}" for i in range(p.ADHOC_NAMES_MAX))
        await mgr.detach("t")
        again = await mgr.attach("t", device=UNOPENABLE)
        assert (again.rx_replaced, again.plot_name_refused) == (7, 3)
        assert len(again.plot_names) == p.ADHOC_NAMES_MAX
        # A replacing attach (a baud change) carries them the same way.
        third = await mgr.attach("t", device=UNOPENABLE, baud=9600)
        assert third is not again and len(third.plot_names) == p.ADHOC_NAMES_MAX
        await mgr.detach("t")
    finally:
        await store.stop()


async def test_typed_names_count_against_the_same_cap(tmp_path) -> None:
    store, port = await _port(tmp_path)
    try:
        # One sid redefined with fresh channel names each time, as a hostile device would.
        for i in range(p.ADHOC_NAMES_MAX + 5):
            await _ingest(port, f"!pd 0 t{i}:u1\n!ps 0 {i} 01\n".encode())
        assert len(port.plot_names) == p.ADHOC_NAMES_MAX
        assert port.plot_name_refused == 5
        assert len(_plotted(store)) == p.ADHOC_NAMES_MAX
        (notice,) = [r for r in _rows(store, "sys") if "distinct plot names" in r]
        assert "!pd" in notice
    finally:
        await store.stop()


async def test_an_open_unstorable_episode_is_recorded_at_stop(tmp_path, caplog) -> None:
    store, port = await _port(tmp_path)
    real = store.submit_line_nowait

    def broken(**kw):
        raise RuntimeError("disk is full")

    store.submit_line_nowait = broken
    try:
        await _ingest(port, b"a\nb\n")
        store.submit_line_nowait = real
        with caplog.at_level("WARNING"):
            await port.stop("detach")
        (notice,) = [r for r in _rows(store, "sys") if "could not be stored" in r]
        assert notice.startswith("port board: 2 rx lines could not be stored between ")
        assert "port board: 2 rx lines could not be stored between " in caplog.text
        assert port._unstorable_n == 0
    finally:
        store.submit_line_nowait = real
        await store.stop()


async def test_stranded_lines_are_logged_not_only_recorded(tmp_path, caplog) -> None:
    store, port = await _port(tmp_path)
    try:
        port._on_bytes(time.time(), b"x\ny\n")
        with caplog.at_level("WARNING"):
            await port.stop("detach")
        assert "port board: dropped 2 received lines not yet stored at detach" in caplog.text
    finally:
        await store.stop()
