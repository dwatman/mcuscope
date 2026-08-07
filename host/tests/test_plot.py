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

from mcuscope.serial_link import PortManager, SerialPort
from mcuscope.store import Store
from tests.support import Stack


def client(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=5.0)


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
    with client(stack) as c:
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
    with client(stack) as c:
        assert poll(lambda: len(_series(c, "tri")) >= 5)
        pts = _series(c, "tri")
    values = [pt["value"] for pt in pts]
    assert max(abs(v) for v in values) <= 30
    assert any(v < 0 for v in values) or any(v > 0 for v in values)


def test_plot_series_float_channel(make_stack: Callable[..., Stack]) -> None:
    # ftest is an f4 slow sine in [-1, 1]; decoding must yield non-integer floats.
    stack = make_stack(["--plot"])
    with client(stack) as c:
        assert poll(lambda: len(_series(c, "ftest")) >= 10)
        pts = _series(c, "ftest")
    values = [pt["value"] for pt in pts]
    assert all(-1.5 <= v <= 1.5 for v in values)
    assert any(abs(v - round(v)) > 1e-6 for v in values)


def test_plot_adhoc_negative_values(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    with client(stack) as c:
        assert poll(lambda: len(_series(c, "sine")) >= 20)
        pts = _series(c, "sine")
    values = [pt["value"] for pt in pts]
    assert min(values) < 0 < max(values)


def test_plot_export_long_and_wide(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    with client(stack) as c:
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
    with client(stack) as c:
        assert poll(lambda: {"sine", "tri"} <= set(_channels(c)))
        r = c.get("/plot/export", params={"names": "sine,tri", "format": "wide"})
    assert r.status_code == 400
    assert "error" in r.json()


# -- direct ingest: late def, mismatch, restart recovery, decimation ------------------


async def _fresh_store(tmp_path) -> Store:
    store = Store(str(tmp_path / "cap.db"))
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

        meta = pm.plot_channel_meta()

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
        chans = {c["name"]: c for c in store.query_plot_channels()}
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
        assert store.query_plot_channels() == []
        # Definition arrives; decoding starts from here.
        await _feed(port, "!pd 0 a:s2 b:s2", "!ps 0 20 FC01,0200")
        chans = {c["name"]: c for c in store.query_plot_channels()}
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
        chans = {c["name"]: c for c in store.query_plot_channels()}
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
        chans = {c["name"]: c for c in store.query_plot_channels()}
        assert chans["a"]["count"] == 2  # both samples decoded across the "restart"
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


async def test_retention_cascade_removes_plot_points(tmp_path) -> None:
    store = await _fresh_store(tmp_path)
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board")
        await _feed(port, "!p 1 v=5")
        assert store.query_plot_channels()
        # Age the line past the retention window and sweep; the FK cascade drops points.
        store._retention_days = 0
        store._conn.execute("UPDATE lines SET ts = ts - 999999")
        store._conn.commit()
        await store._sweep_retention_async()
        assert store.query_plot_channels() == []
    finally:
        await store.stop()
