"""`/plot/export` decode, changes and deadband, and the removal of the row cap (SPEC 9.2).

Definitions are fed line by line into the running daemon's port rather than taken from the
simulator: every assertion here is about a *particular* sequence of values (a redefinition
between two samples, a move that lands inside a deadband), which a free-running sim cannot
be asked for. The simulator's own enum/bits streams are covered by test_plot.py.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable

import httpx

from mcuscope.serial_link import SerialPort
from tests.support import Stack


def client(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=30.0)


def feed(stack: Stack, *lines: str) -> None:
    """Store `lines` as received on the stack's port, in order, before returning."""
    ports = stack.app.state.ports
    port = ports._ports[stack.alias]
    for line in lines:
        asyncio.run_coroutine_threadsafe(
            port._store_rx_line(time.time(), line), ports._loop
        ).result(10)


def csv_rows(text: str) -> list[list[str]]:
    return [line.split(",") for line in text.strip().splitlines()]


def export(c: httpx.Client, **params) -> httpx.Response:
    return c.get("/plot/export", params=params)


# A stream carrying one of each kind: enum, scaled analog, packed bits.
DEF = "!pd 3 mode:u1:=0=IDLE,1=ARMED,2=RUN volts:s2*0.01:V io:u1:/led,irq"
ALL_NAMES = "mode,volts,led,irq"


def sample(tick: int, mode: int, volts: int, io: int) -> str:
    return f"!ps 3 {tick:X} {mode:02X},{volts & 0xFFFF:04X},{io:02X}"


# -- refusals -------------------------------------------------------------------------


def test_changes_without_decode_is_refused(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack()
    feed(stack, DEF, sample(1, 0, 100, 1))
    with client(stack) as c:
        r = export(c, names=ALL_NAMES, changes=1)
    assert r.status_code == 400
    assert r.json()["error"] == "changes requires decode"


def test_deadband_without_changes_is_refused(make_stack: Callable[..., Stack]) -> None:
    # decode alone is not enough: a deadband decides what to *drop*, which only `changes`
    # is allowed to do.
    stack = make_stack()
    feed(stack, DEF, sample(1, 0, 100, 1))
    with client(stack) as c:
        r = export(c, names=ALL_NAMES, decode=1, deadband="volts=0.5")
    assert r.status_code == 400
    assert r.json()["error"] == "deadband requires changes"


def test_deadband_naming_an_unexported_channel_is_refused(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    feed(stack, DEF, sample(1, 0, 100, 1))
    with client(stack) as c:
        # `mode` exists in the capture but is not in this request's selection.
        r = export(c, names="volts", decode=1, changes=1, deadband="mode=1")
        missing = export(c, names="volts", decode=1, changes=1, deadband="nosuch=1")
        bare = export(c, names="volts", decode=1, changes=1, deadband="volts")
    assert r.status_code == 400
    assert r.json()["error"] == "deadband names no exported channel: mode=1"
    assert missing.status_code == 400
    assert missing.json()["error"] == "deadband names no exported channel: nosuch=1"
    # A missing `=` is its own fault: `volts` IS an exported channel, and the old shared
    # message blamed the name for the syntax.
    assert bare.status_code == 400
    assert bare.json()["error"] == "deadband needs name=value: volts"


def test_deadband_with_a_non_numeric_value_is_refused(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    feed(stack, DEF, sample(1, 0, 100, 1))
    with client(stack) as c:
        r = export(c, names="volts", decode=1, changes=1, deadband="volts=wide")
    assert r.status_code == 400
    assert r.json()["error"] == "deadband value is not a number: volts=wide"


def test_deadband_on_an_enum_field_is_refused(make_stack: Callable[..., Stack]) -> None:
    # An enum renders as a label, so "within 0.5 of the last one" has no meaning; the
    # refusal must name the field rather than silently ignoring the band.
    stack = make_stack()
    feed(stack, DEF, sample(1, 0, 100, 1))
    with client(stack) as c:
        r = export(c, names="mode,volts", decode=1, changes=1, deadband="mode=0.5")
    assert r.status_code == 400
    assert r.json()["error"] == (
        "deadband is numeric, but mode renders as a label (an enum or a decoded bit lane)"
    )


# -- decoding -------------------------------------------------------------------------


def test_decode_renders_labels_and_qualified_lanes_long(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    feed(stack, DEF, sample(1, 2, -250, 0b01))
    with client(stack) as c:
        plain = export(c, names=ALL_NAMES)
        decoded = export(c, names=ALL_NAMES, decode=1)
    # Rows within one sample are ordered by the stored channel name, not by the request.
    # Undecoded, an enum is its raw integer and a lane is its bare name.
    assert [(r[3], r[4]) for r in csv_rows(plain.text)[1:]] == [
        ("irq", "0.0"), ("led", "1.0"), ("mode", "2.0"), ("volts", "-2.5"),
    ]
    assert [(r[3], r[4]) for r in csv_rows(decoded.text)[1:]] == [
        ("io.irq", "0"), ("io.led", "1"), ("mode", "RUN"), ("volts", "-2.5"),
    ]


def test_decoded_wide_header_qualifies_lanes(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack()
    feed(stack, DEF, sample(1, 1, 100, 0b10))
    with client(stack) as c:
        r = export(c, names=ALL_NAMES, format="wide", decode=1)
    rows = csv_rows(r.text)
    assert rows[0] == ["ts", "tick_ms", "mode", "volts", "io.led", "io.irq"]
    assert rows[1][2:] == ["ARMED", "1.0", "0", "1"]


def test_an_enum_value_the_definition_does_not_name_stays_an_integer(
    make_stack: Callable[..., Stack],
) -> None:
    # A firmware that gains a state before its `!pd` does must not export a blank cell.
    stack = make_stack()
    feed(stack, DEF, sample(1, 7, 100, 0))
    with client(stack) as c:
        r = export(c, names="mode", decode=1)
    assert [row[4] for row in csv_rows(r.text)[1:]] == ["7"]


def test_a_stream_redefined_mid_window_decodes_each_half_with_its_own_labels(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    feed(
        stack,
        "!pd 3 mode:u1:=0=IDLE,1=ARMED",
        "!ps 3 1 00",
        "!ps 3 2 01",
        "!pd 3 mode:u1:=0=OFF,1=ON",
        "!ps 3 3 00",
        "!ps 3 4 01",
    )
    with client(stack) as c:
        r = export(c, names="mode", decode=1)
    assert [row[4] for row in csv_rows(r.text)[1:]] == ["IDLE", "ARMED", "OFF", "ON"]


def test_decode_is_scoped_to_one_port(make_stack: Callable[..., Stack]) -> None:
    # Two boards may declare the same sid with different labels (SPEC 9.2), so a definition
    # from the other port must not be the one a scoped export decodes against.
    stack = make_stack()
    ports = stack.app.state.ports
    second = SerialPort(stack.app.state.store, ports._loop, "second")
    ports._ports["second"] = second
    for line in ("!pd 3 mode:u1:=0=OTHER", "!ps 3 1 00"):
        asyncio.run_coroutine_threadsafe(
            second._store_rx_line(time.time(), line), ports._loop
        ).result(10)
    feed(stack, "!pd 3 mode:u1:=0=MINE", "!ps 3 2 00")
    with client(stack) as c:
        r = export(c, names="mode", decode=1, port=stack.alias)
    assert [row[4] for row in csv_rows(r.text)[1:]] == ["MINE"]


# -- changes and deadband -------------------------------------------------------------


def test_the_first_row_of_every_stream_always_emits(
    make_stack: Callable[..., Stack],
) -> None:
    # Two streams whose values never move: `changes` must still open each one, or the CSV
    # cannot say what the capture started at.
    stack = make_stack()
    feed(
        stack,
        "!pd 3 a:u1", "!pd 4 b:u1",
        "!ps 3 1 05", "!ps 4 1 09",
        "!ps 3 2 05", "!ps 4 2 09",
        "!ps 3 3 05", "!ps 4 3 09",
    )
    with client(stack) as c:
        r = export(c, names="a,b", decode=1, changes=1)
    assert [(row[2], row[3], row[4]) for row in csv_rows(r.text)[1:]] == [
        ("3", "a", "5.0"), ("4", "b", "9.0"),
    ]


def test_long_changes_are_per_field(make_stack: Callable[..., Stack]) -> None:
    # One channel moves every sample and one never does: the still one must emit once, not
    # be dragged along by its neighbour changing in the same sample.
    stack = make_stack()
    feed(
        stack,
        "!pd 3 still:u1 moving:u1",
        "!ps 3 1 07,01",
        "!ps 3 2 07,02",
        "!ps 3 3 07,03",
    )
    with client(stack) as c:
        r = export(c, names="still,moving", decode=1, changes=1)
    assert [(row[3], row[4]) for row in csv_rows(r.text)[1:]] == [
        ("moving", "1.0"), ("still", "7.0"), ("moving", "2.0"), ("moving", "3.0"),
    ]


def test_wide_changes_emit_a_sample_only_when_a_column_moved(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    feed(
        stack,
        "!pd 3 still:u1 moving:u1",
        "!ps 3 1 07,01",
        "!ps 3 2 07,01",
        "!ps 3 3 07,02",
        "!ps 3 4 07,02",
    )
    with client(stack) as c:
        r = export(c, names="still,moving", format="wide", decode=1, changes=1)
    rows = csv_rows(r.text)
    assert rows[0] == ["ts", "tick_ms", "still", "moving"]
    assert [(row[1], row[2], row[3]) for row in rows[1:]] == [
        ("1", "7.0", "1.0"), ("3", "7.0", "2.0"),
    ]


def test_a_move_inside_the_deadband_does_not_emit_and_one_outside_does(
    make_stack: Callable[..., Stack],
) -> None:
    # volts is s2 scaled by 0.01, so the raw counts below are 1.00, 1.03 and 1.10 V against
    # a 0.05 V band. 1.03 is inside it; 1.10 is outside it *relative to the last emitted*
    # value, which is still 1.00 - a band must not ratchet along with the samples it drops.
    stack = make_stack()
    feed(
        stack,
        "!pd 3 volts:s2*0.01:V",
        "!ps 3 1 0064",
        "!ps 3 2 0067",
        "!ps 3 3 006E",
    )
    with client(stack) as c:
        banded = export(c, names="volts", decode=1, changes=1, deadband="volts=0.05")
        unbanded = export(c, names="volts", decode=1, changes=1)
    assert [row[4] for row in csv_rows(banded.text)[1:]] == ["1.0", "1.1"]
    # Without the band every one of the three is a change, so the band is what dropped it.
    assert len(csv_rows(unbanded.text)[1:]) == 3


def test_a_deadband_does_not_ratchet_across_dropped_samples(
    make_stack: Callable[..., Stack],
) -> None:
    # Four samples each 0.03 V above the last: every step is inside a 0.05 V band, but the
    # total drift is 0.09 V. Comparing against the last *emitted* value is what catches it.
    stack = make_stack()
    feed(
        stack,
        "!pd 3 volts:s2*0.01:V",
        "!ps 3 1 0064", "!ps 3 2 0067", "!ps 3 3 006A", "!ps 3 4 006D",
    )
    with client(stack) as c:
        r = export(c, names="volts", decode=1, changes=1, deadband="volts=0.05")
    assert [row[4] for row in csv_rows(r.text)[1:]] == ["1.0", "1.06"]


# -- the row cap is gone --------------------------------------------------------------


def test_a_selection_past_the_old_row_cap_streams(
    make_stack: Callable[..., Stack],
) -> None:
    """1.2 million points used to be a 400. Nothing may cap or truncate it now.

    Written straight into the capture DB: 1.2M lines through the port would take minutes,
    and the endpoint's contract is about the stored selection, not how it got there.
    """
    rows_wanted = 1_200_000
    per_line = 10
    stack = make_stack()
    feed(stack, "!p 1 bulk=0")            # the channel must exist before the fill
    store = stack.app.state.store
    # A wide gap above the daemon's own ids: the writer allocates from an in-memory
    # sequence (Store.max_id), so rows written just above its current top collide with the
    # next line the still-running port stores.
    base = store.max_id() + 1_000_000

    now = time.time()
    conn = sqlite3.connect(store._db_path)
    try:
        conn.executemany(
            "INSERT INTO lines(id, ts, port, dir, chan, seq, raw) VALUES(?,?,?,?,?,?,?)",
            (
                (base + i, now, stack.alias, "rx", "event", None, "!p 1 bulk=0")
                for i in range(1, rows_wanted // per_line + 1)
            ),
        )
        conn.executemany(
            "INSERT INTO plot_points(line_id, tick_ms, sid, name, value) VALUES(?,?,?,?,?)",
            (
                (base + 1 + i // per_line, i, None, "bulk", float(i))
                for i in range(rows_wanted)
            ),
        )
        conn.commit()
        last_id = base + rows_wanted // per_line
    finally:
        conn.close()

    # id_to is explicit: the daemon's cached max_id has not seen rows written behind it.
    with client(stack) as c, c.stream(
        "GET", "/plot/export", params={"names": "bulk", "id_to": last_id}
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        body_lines = 0
        first = ""
        for chunk in r.iter_text():
            if not first:
                first = chunk.split("\n", 1)[0]
            body_lines += chunk.count("\n")
    assert first == "ts,tick_ms,sid,name,value"
    # Header plus every point: the old cap stopped at 1,000,000 without saying so.
    assert body_lines == rows_wanted + 2   # +1 header, +1 the seed !p point



# -- 2026-09-12 round: D7, C2, improvement 9, survivor V10 -----------------------------


def test_deadband_on_a_decoded_bit_lane_is_refused(make_stack: Callable[..., Stack]) -> None:
    """D7. A lane renders as a label exactly as an enum does, so a band on it is inert.

    `_decode_map` gives a lane the labels {0: "0", 1: "1"}, `_render` then sets `num=None`
    and `_changed` never consults the band: the same request with and without the deadband
    returned byte-for-byte the same file, with no error, where the enum spelling of the
    same mistake was a 400.
    """
    stack = make_stack()
    feed(stack, DEF, sample(1, 0, 100, 1), sample(2, 0, 100, 3))
    with client(stack) as c:
        r = export(c, names=ALL_NAMES, decode=1, changes=1, deadband="irq=0.5")
        both = export(c, names=ALL_NAMES, decode=1, changes=1, deadband="irq=0.5,mode=0.5")
        ok = export(c, names=ALL_NAMES, decode=1, changes=1, deadband="volts=0.5")
    assert r.status_code == 400
    assert r.json()["error"] == (
        "deadband is numeric, but irq renders as a label (an enum or a decoded bit lane)"
    )
    assert both.status_code == 400
    assert "irq, mode" in both.json()["error"], "every offending name is named"
    assert ok.status_code == 200, "a band on the analog channel is still accepted"


def test_deadband_value_must_be_a_finite_ascii_quantity(
    make_stack: Callable[..., Stack],
) -> None:
    """C2, class 22's second face: `float()` parses, and parsing is not validating.

    `volts=inf` collapsed the export to its first row at exit 0 - the header-only-at-exit-0
    artefact SPEC 9.2 forbids for this endpoint - and `٣` was silently taken as 3.
    """
    stack = make_stack()
    feed(stack, DEF, sample(1, 0, 100, 1), sample(2, 0, 500, 1))
    with client(stack) as c:
        for value in ("inf", "-inf", "Infinity", "nan", "٣", "1٣"):
            r = export(c, names=ALL_NAMES, decode=1, changes=1, deadband=f"volts={value}")
            assert r.status_code == 400, value
            assert r.json()["error"] == f"deadband value is not a number: volts={value}", value
        good = export(c, names=ALL_NAMES, decode=1, changes=1, deadband="volts=0.5")
    assert good.status_code == 200


def test_deadband_without_an_equals_names_the_syntax_not_the_channel(
    make_stack: Callable[..., Stack],
) -> None:
    """D8. One condition carried two messages' worth of meaning and picked the wrong one."""
    stack = make_stack()
    feed(stack, DEF, sample(1, 0, 100, 1))
    with client(stack) as c:
        r = export(c, names=ALL_NAMES, decode=1, changes=1, deadband="volts")
        unknown = export(c, names=ALL_NAMES, decode=1, changes=1, deadband="nosuch=1")
    assert r.status_code == 400
    assert r.json()["error"] == "deadband needs name=value: volts"
    assert unknown.json()["error"] == "deadband names no exported channel: nosuch=1"


def test_one_mistyped_name_is_refused_even_beside_a_good_one(
    make_stack: Callable[..., Stack],
) -> None:
    """Improvement 9. `names=volts,nosuch` exported volts at exit 0 and never said so."""
    stack = make_stack()
    feed(stack, DEF, sample(1, 0, 100, 1))
    with client(stack) as c:
        mixed = export(c, names="volts,nosuch", decode=1)
        both_bad = export(c, names="nosuch,alsonot")
        # A name that exists but has no point inside the window is still a known name:
        # refusing that would be the over-correction.
        empty = export(c, names="volts", until_ts=1.0)
    assert mixed.status_code == 400
    assert mixed.json()["error"] == "no such plot channel: nosuch; see /plot/channels"
    assert both_bad.status_code == 400
    assert "nosuch, alsonot" in both_bad.json()["error"]
    assert empty.status_code == 200
    assert empty.text.splitlines() == ["ts,tick_ms,sid,name,value"], (
        "an empty window is still a header-only CSV, not a refusal"
    )


def test_a_name_another_stream_declared_does_not_relabel_this_row(
    make_stack: Callable[..., Stack],
) -> None:
    """Survivor V10: `_render`'s sid-mismatch guard was asserted by nothing.

    `channel_meta` keys by name across streams (SPEC 2.5, last declaration wins), so
    without the guard stream 3's `mode` would be rendered through stream 4's labels - a
    cell that reads authoritative and names the wrong state.
    """
    stack = make_stack()
    other = "!pd 4 mode:u1:=0=OTHER_ZERO,1=OTHER_ONE"
    feed(stack, DEF, other, sample(1, 1, 100, 0), "!ps 4 2 00")
    with client(stack) as c:
        rows = csv_rows(export(c, names="mode", decode=1).text)
    labels = [r[-1] for r in rows[1:]]
    # The row whose sid owns the map entry is labelled; the other keeps its raw value
    # rather than being relabelled through a definition that is not its own. Without the
    # guard the second row reads `IDLE`, a state stream 4 never declared.
    assert labels == ["ARMED", "0.0"], rows
    assert "IDLE" not in labels and "OTHER_ZERO" not in labels, rows


def test_deadband_is_accepted_where_another_stream_declares_the_name_numeric(
    make_stack: Callable[..., Stack],
) -> None:
    """Fix-diff F4 (class 55). `_render` labels only the rows of the stream that declared
    last, so a name another stream declares as a number still has rows the band applies
    to; refusing on the last declaration's kind alone was a false 400."""
    stack = make_stack()
    feed(stack, DEF, "!pd 4 mode:u1", sample(1, 1, 100, 0), "!ps 4 2 00", "!ps 4 3 05")
    with client(stack) as c:
        r = export(c, names="mode", decode=1, changes=1, deadband="mode=0.5")
    assert r.status_code == 200, r.text
