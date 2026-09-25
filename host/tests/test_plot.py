"""Plot pipeline tests (SPEC 2.5, 9.2): ingest, decode, endpoints, CSV export.

Two halves: a live sim (`--plot`) exercises the endpoints and typed/ad-hoc decode over
real sockets; a set of direct-ingest tests drive `SerialPort._store_rx_line` against a
temp Store to cover the awkward cases (late definition, width/count mismatch, restart
recovery, exact decimation) without depending on wall-clock timing in the simulator.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import httpx

from mcuscope import serial_link
from mcuscope.serial_link import PortManager, SerialPort
from mcuscope.store import Store
from tests.support import Stack, stack_client
from tests.test_store_plot_summary import query_plot_channels


def poll(fn: Callable[[], bool], timeout: float = 6.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return False


# -- live sim: endpoints + typed/ad-hoc decode ----------------------------------------


def _channels(c: httpx.Client) -> dict[str, dict]:
    return {ch["name"]: ch for ch in c.get("/plot/channels").json()["channels"]}


def _series(c: httpx.Client, name: str) -> list[dict]:
    return c.get("/plot/series", params={"name": name}).json()["points"]


def test_plot_channels_report_meta(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    with stack_client(stack) as c:
        assert poll(lambda: "tri" in _channels(c) and _channels(c)["tri"]["count"] >= 5)
        chans = _channels(c)
        # ad-hoc channels have no sid; typed channels carry their stream's sid + meta.
        assert chans["sine"]["sid"] is None
        assert chans["tri"]["sid"] == "0"
        assert chans["tri"]["type"] == "s2"
        assert chans["tri"]["unit"] == "V"
        assert chans["tri"]["scale"] == 0.01
        assert chans["ftest"]["type"] == "f4"


def test_plot_series_scale_applied(make_stack: Callable[..., Stack]) -> None:
    # tri is s2 scaled by 0.01 to +-20 V; the stored value must be the scaled float, so
    # the magnitude stays well under the raw +-2000 count range.
    stack = make_stack(["--plot"])
    with stack_client(stack) as c:
        assert poll(lambda: len(_series(c, "tri")) >= 5)
        pts = _series(c, "tri")
    values = [pt["value"] for pt in pts]
    assert max(abs(v) for v in values) <= 30
    assert any(v < 0 for v in values) or any(v > 0 for v in values)


def test_plot_series_float_channel(make_stack: Callable[..., Stack]) -> None:
    # ftest is an f4 slow sine in [-1, 1]; decoding must yield non-integer floats.
    stack = make_stack(["--plot"])
    with stack_client(stack) as c:
        assert poll(lambda: len(_series(c, "ftest")) >= 10)
        pts = _series(c, "ftest")
    values = [pt["value"] for pt in pts]
    assert all(-1.5 <= v <= 1.5 for v in values)
    assert any(abs(v - round(v)) > 1e-6 for v in values)


def test_plot_adhoc_negative_values(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    with stack_client(stack) as c:
        assert poll(lambda: len(_series(c, "sine")) >= 20)
        pts = _series(c, "sine")
    values = [pt["value"] for pt in pts]
    assert min(values) < 0 < max(values)


def test_plot_export_long_and_wide(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    with stack_client(stack) as c:
        assert poll(lambda: "tri" in _channels(c) and _channels(c)["tri"]["count"] >= 10)
        long = c.get("/plot/export", params={"names": "tri", "format": "long"})
        wide = c.get("/plot/export", params={"names": "tri,ramp,ftest", "format": "wide"})
    assert "text/csv" in long.headers["content-type"]
    long_lines = long.text.strip().splitlines()
    assert long_lines[0] == "ts,tick_ms,sid,name,value"
    assert long_lines[1].split(",")[3] == "tri"

    wide_lines = wide.text.strip().splitlines()
    assert wide_lines[0] == "ts,tick_ms,tri,ramp,ftest"
    assert len(wide_lines[1].split(",")) == 5  # ts, tick, 3 channels


def test_plot_export_wide_rejects_mixed_streams(make_stack: Callable[..., Stack]) -> None:
    # sine is ad-hoc (sid None), tri is stream 0; wide needs a single shared sid.
    stack = make_stack(["--plot"])
    with stack_client(stack) as c:
        assert poll(lambda: {"sine", "tri"} <= set(_channels(c)))
        r = c.get("/plot/export", params={"names": "sine,tri", "format": "wide"})
    assert r.status_code == 400
    assert "error" in r.json()


# -- direct ingest: late def, mismatch, restart recovery, decimation ------------------


async def _fresh_store(tmp_path) -> Store:
    return await _open_store(str(tmp_path / "cap.db"))


async def _open_store(path: str) -> Store:
    store = Store(path)
    await store.start(retention_days=7)
    return store


async def _feed(port: SerialPort, *lines: str) -> None:
    for line in lines:
        await port._store_rx_line(time.time(), line)


async def test_plot_channel_meta_enum_and_bits(tmp_path) -> None:
    store = await _fresh_store(tmp_path)
    try:
        loop = asyncio.get_running_loop()
        port = SerialPort(store, loop, "board")
        port.plot_decoder.learn("!pd 0 state:u1:=0=IDLE,1=ARMED gpio:u1:/led,irq")
        pm = PortManager(store, loop)
        pm._ports["board"] = port

        meta = pm.plot_channel_meta_by_port()["board"]

        assert meta["state"]["kind"] == "enum"
        assert meta["state"]["labels"] == [[0, "IDLE"], [1, "ARMED"]]
        assert meta["led"]["kind"] == "bit"
        assert meta["led"]["group"] == "gpio"
        assert meta["led"]["bit"] == 0
        assert meta["irq"]["kind"] == "bit"
        assert meta["irq"]["group"] == "gpio"
        assert meta["irq"]["bit"] == 1
        assert "gpio" not in meta
    finally:
        await store.stop()


async def test_ingest_typed_decode_and_scale(tmp_path) -> None:
    store = await _fresh_store(tmp_path)
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board")
        await _feed(
            port,
            "!pd 0 ax:s2*0.00098:g ay:s2 az:u4",
            "!ps 0 10 FC01,0200,0000FFFF",
        )
        chans = {c["name"]: c for c in query_plot_channels(store)}
        assert chans["ax"]["last_value"] == -1023 * 0.00098
        assert chans["ay"]["last_value"] == 512.0
        assert chans["az"]["last_value"] == 65535.0
        assert chans["ax"]["sid"] == "0"
    finally:
        await store.stop()


async def test_ingest_sample_before_def_is_skipped(tmp_path) -> None:
    store = await _fresh_store(tmp_path)
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board")
        # No definition yet: the sample is stored as a generic event, not a plot point.
        await _feed(port, "!ps 0 10 FC01,0200")
        assert query_plot_channels(store) == []
        # Definition arrives; decoding starts from here.
        await _feed(port, "!pd 0 a:s2 b:s2", "!ps 0 20 FC01,0200")
        chans = {c["name"]: c for c in query_plot_channels(store)}
        assert set(chans) == {"a", "b"}
        assert chans["a"]["count"] == 1  # only the post-def sample decoded
    finally:
        await store.stop()


async def test_ingest_mismatch_is_skipped(tmp_path) -> None:
    store = await _fresh_store(tmp_path)
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board")
        await _feed(
            port,
            "!pd 0 a:s2 b:s2",
            "!ps 0 10 FC01",            # too few values
            "!ps 0 20 FC01,0200,4000",  # too many values
            "!ps 0 30 FC0,0200",        # wrong field width
            "!ps 0 40 FC01,0200",       # the one good sample
        )
        chans = {c["name"]: c for c in query_plot_channels(store)}
        assert chans["a"]["count"] == 1
    finally:
        await store.stop()


async def test_ingest_restart_recovers_defs(tmp_path) -> None:
    store = await _fresh_store(tmp_path)
    try:
        # First "run": a def and a sample land in the db.
        p1 = SerialPort(store, asyncio.get_running_loop(), "board")
        await _feed(p1, "!pd 0 a:s2 b:s2", "!ps 0 10 FC01,0200")
        # "Restart": a brand new port primes its def cache from stored lines, then a
        # sample decodes immediately without waiting for a fresh !pd.
        p2 = SerialPort(store, asyncio.get_running_loop(), "board")
        await p2.prime_plot_defs()
        await _feed(p2, "!ps 0 20 F000,0100")
        chans = {c["name"]: c for c in query_plot_channels(store)}
        assert chans["a"]["count"] == 2  # both samples decoded across the "restart"
    finally:
        await store.stop()


def _bump_max_id(store: Store, new_id: int) -> None:
    """Park one row at `new_id` so the store's head jumps without 20k real inserts."""
    store._conn.execute(
        "INSERT INTO lines(id, ts, port, dir, chan, seq, raw) "
        "VALUES(?, ?, 'board', 'rx', 'debug', NULL, 'filler')",
        (new_id, time.time()),
    )
    store._conn.commit()
    store._next_id = new_id + 1   # max_id() answers from the writer's sequence


async def test_prime_plot_defs_recovers_def_inside_lookback(tmp_path) -> None:
    store = await _fresh_store(tmp_path)
    try:
        p1 = SerialPort(store, asyncio.get_running_loop(), "board")
        await _feed(p1, "!pd 0 a:s2 b:s2")
        # Head pushed forward, but the def still sits inside the lookback window.
        _bump_max_id(store, store.max_id() + serial_link.PLOT_DEF_LOOKBACK - 10)

        p2 = SerialPort(store, asyncio.get_running_loop(), "board")
        await p2.prime_plot_defs()
        await _feed(p2, "!ps 0 20 F000,0100")

        chans = {c["name"]: c for c in query_plot_channels(store)}
        assert chans["a"]["count"] == 1
    finally:
        await store.stop()


async def test_prime_plot_defs_ignores_def_beyond_lookback(tmp_path) -> None:
    store = await _fresh_store(tmp_path)
    try:
        p1 = SerialPort(store, asyncio.get_running_loop(), "board")
        await _feed(p1, "!pd 0 a:s2 b:s2")
        # Same def, now pushed out of the window: recovery must not reach it, and the
        # sample stores as a generic event until the firmware rebroadcasts.
        _bump_max_id(store, store.max_id() + serial_link.PLOT_DEF_LOOKBACK + 10)

        p2 = SerialPort(store, asyncio.get_running_loop(), "board")
        await p2.prime_plot_defs()
        await _feed(p2, "!ps 0 20 F000,0100")

        assert query_plot_channels(store) == []
    finally:
        await store.stop()


async def test_ingest_series_decimation(tmp_path) -> None:
    store = await _fresh_store(tmp_path)
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board")
        for i in range(10):
            await _feed(port, f"!p {i} v={i}")
        full = store.query_plot_series(name="v")
        assert [pt["value"] for pt in full] == [float(i) for i in range(10)]
        # decimate buckets N points counting back from the newest and keeps each bucket's
        # min and max, so a monotonic ramp survives intact.
        dec = store.query_plot_series(name="v", decimate=2)
        assert [pt["value"] for pt in dec] == [float(i) for i in range(10)]
        # A bucket whose samples are all equal collapses to a single point (min == max).
        flat = store.query_plot_series(name="v", decimate=10)
        assert [pt["value"] for pt in flat] == [0.0, 9.0]

        # since_id is an exclusive cursor, like /lines and /can/frames: a polling client
        # passes back the last line_id it saw, and an inclusive bound would re-deliver
        # that sample on every poll. The boundary point is the whole test.
        boundary = full[4]["line_id"]
        after = store.query_plot_series(name="v", since_id=boundary)
        assert [pt["value"] for pt in after] == [float(i) for i in range(5, 10)]
        assert store.query_plot_series(name="v", since_id=full[-1]["line_id"]) == []
    finally:
        await store.stop()


async def test_decimation_keeps_spikes(tmp_path) -> None:
    # The point of min/max decimation over every-Nth: a transient between kept samples
    # must not vanish. Every-Nth would drop this spike entirely at decimate=8.
    store = await _fresh_store(tmp_path)
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board")
        for i in range(64):
            value = 99 if i == 13 else 0        # one lone spike, off every 8-point boundary
            await _feed(port, f"!p {i} v={value}")
        dec = store.query_plot_series(name="v", decimate=8)
        values = [pt["value"] for pt in dec]
        assert 99.0 in values, f"the spike was decimated away: {values}"
        # Reduction is real: 64 points in, at most 2 per 8-point bucket out.
        assert len(values) <= 16
        # Chronological order is preserved regardless of which extreme each bucket kept.
        ids = [pt["line_id"] for pt in dec]
        assert ids == sorted(ids)
    finally:
        await store.stop()


async def test_retention_cascade_removes_plot_points_and_can_frames(tmp_path) -> None:
    # The ON DELETE CASCADE is the ONLY thing that removes a deleted line's children: every
    # delete path issues `DELETE FROM lines` and nothing else, and SQLite enforces the
    # constraint only while `foreign_keys` is on - a per-connection pragma, off by default,
    # set at one line in start(). Asserted on the child tables directly, because the
    # endpoints read them through an inner join to `lines`: with the parent rows gone they
    # answer empty whether the children cascaded or were orphaned, so the whole suite
    # passed with the pragma turned off. An orphan here means the file grows forever while
    # the size cap trims `lines` and every health surface stays green.
    def child_counts(store: Store) -> tuple[int, int]:
        return (
            store._conn.execute("SELECT COUNT(*) FROM plot_points").fetchone()[0],
            store._conn.execute("SELECT COUNT(*) FROM can_frames").fetchone()[0],
        )

    store = await _fresh_store(tmp_path)
    try:
        assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1, \
            "the cascade is unenforced unless this pragma reads back on"
        port = SerialPort(store, asyncio.get_running_loop(), "board")
        await _feed(port, "!p 1 v=5", "!can 100 - 100 01")
        assert child_counts(store) == (1, 1), "sanity: a point and a frame were stored"

        # Age the lines past the retention window and sweep; the cascade drops both.
        store._retention_days = 0
        store._conn.execute("UPDATE lines SET ts = ts - 999999")
        store._conn.commit()
        store._last_age_sweep = None   # aged behind the writer, which resets it on an old row
        await store._sweep_retention_async()
        assert store._conn.execute("SELECT COUNT(*) FROM lines").fetchone()[0] == 0
        assert child_counts(store) == (0, 0), "children outlived their lines"
        assert query_plot_channels(store) == []
    finally:
        await store.stop()


async def test_session_export_carries_the_child_tables(tmp_path) -> None:
    # The export's stated value is that every query works unchanged on the copy, and the
    # two queries that need a child table - /can/frames and /plot/series - are exactly the
    # ones no export test drove: every one of them wrote markers only, so both
    # `INSERT ... SELECT`s could be deleted with the suite still green.
    store = await _fresh_store(tmp_path)
    dest = str(tmp_path / "run.db")
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board")
        session = await store.start_session("run-a")
        await _feed(port, "!p 1 v=5", "!can 100 - 100 01")
        ended = await store.stop_session()
        assert ended is not None

        copied = store.export_session_db(
            dest, id_from=ended["start_id"], id_to=ended["end_id"], session=ended
        )
        assert copied > 0 and session["id"] == ended["id"]
    finally:
        await store.stop()

    copy = await _open_store(dest)
    try:
        assert [(c["name"], c["count"]) for c in query_plot_channels(copy)] == [("v", 1)]
        assert [pt["value"] for pt in copy.query_plot_series(name="v")] == [5.0]
        frames, _ = copy.query_can_frames(limit=10)
        assert [f["can_id"] for f in frames] == [0x100]
    finally:
        await copy.stop()


async def test_plot_export_scopes_to_one_port(tmp_path) -> None:
    # Channel names are unique only within a port (SPEC 9.2): two boards declaring `temp`
    # interleave in one CSV column unless the export is scoped to one of them.
    store = await _fresh_store(tmp_path)
    try:
        loop = asyncio.get_running_loop()
        a, b = SerialPort(store, loop, "a"), SerialPort(store, loop, "b")
        await _feed(a, "!pd 0 temp:u1", "!ps 0 100 01", "!ps 0 200 02")
        await _feed(b, "!pd 0 temp:u1", "!ps 0 300 09")
        both = list(store.iter_plot_export(names=["temp"]))
        only_a = list(store.iter_plot_export(names=["temp"], port="a"))
        assert [r["value"] for r in both] == [1, 2, 9]
        assert [r["value"] for r in only_a] == [1, 2]
        assert len(list(store.iter_plot_export(names=["temp"], port="b"))) == 1
        assert len(store.export_sids(names=["temp"], port="b")) == 1
        assert list(store.iter_plot_export(names=["temp"], port="nope")) == []
    finally:
        await store.stop()


# 7F800000 = +inf, FF800000 = -inf, 7FC00000 = NaN: an uninitialised float, an overflowing
# accumulator and a 0.0/0.0 in ordinary firmware.
NON_FINITE_PATTERNS = ("7F800000", "FF800000", "7FC00000")


def _ingest(stack: Stack, *lines: str) -> None:
    """Feed lines to the daemon's rx path on its own loop, as the serial reader does."""
    ports = stack.app.state.ports
    port = ports._ports[stack.alias]
    for line in lines:
        fut = asyncio.run_coroutine_threadsafe(
            port._store_rx_line(time.time(), line), ports._loop
        )
        fut.result(5.0)


def test_non_finite_typed_value_drops_that_point_only(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    with httpx.Client(base_url=stack.base_url, timeout=5.0) as c:
        _ingest(stack, "!pd 3 volts:f4 amps:f4")
        for i, pattern in enumerate(NON_FINITE_PATTERNS):
            _ingest(stack, f"!ps 3 {i + 1:X} {pattern},41200000")

        # SPEC 2.5: the non-finite point goes, the finite one beside it is stored.
        names = [ch["name"] for ch in c.get("/plot/channels").json()["channels"]]
        assert names == ["amps"], f"a non-finite value was stored as a plot point: {names}"
        r = c.get("/plot/series", params={"name": "volts"})
        assert r.status_code == 200, r.text
        assert r.json()["points"] == []
        r = c.get("/plot/series", params={"name": "amps"})
        assert r.status_code == 200, r.text
        assert [pt["value"] for pt in r.json()["points"]] == [10.0, 10.0, 10.0]

        # The line itself is kept, so nothing is lost silently.
        rows = c.get("/lines", params={"match": "7F800000"}).json()["lines"]
        assert len(rows) == 1, rows


def test_post_scale_overflow_is_a_generic_event(make_stack: Callable[..., Stack]) -> None:
    """A finite u4 field carried to infinity by its own *scale factor (RG-F13)."""
    stack = make_stack()
    with httpx.Client(base_url=stack.base_url, timeout=5.0) as c:
        _ingest(stack, "!pd 4 big:u4*1e308", "!ps 4 A FFFFFFFF")

        names = [ch["name"] for ch in c.get("/plot/channels").json()["channels"]]
        assert names == [], f"a post-scale infinity was stored: {names}"
        r = c.get("/plot/series", params={"name": "big"})
        assert r.status_code == 200, r.text
        assert r.json()["points"] == []
