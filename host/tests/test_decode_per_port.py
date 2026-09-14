"""Decoding keyed per port: two boards may declare one sid with different fields (SPEC 2.5).

Covers the daemon's `/plot/export?decode` (with and without `port=`) and session bundles,
and the CLI's `!pd` priming, which read a newest-N across every port.
"""

from __future__ import annotations

import asyncio
import io
import time
import zipfile
from collections.abc import Callable

import httpx

from mcuscope import cli
from mcuscope.serial_link import SerialPort
from tests.support import Stack


def board(stack: Stack, alias: str) -> SerialPort:
    """The stack's own port, or a second one sharing its store (no link attached)."""
    ports = stack.app.state.ports
    if alias not in ports._ports:
        ports._ports[alias] = SerialPort(stack.app.state.store, ports._loop, alias)
    return ports._ports[alias]


def feed(stack: Stack, *items: tuple[str, str]) -> None:
    """Store `(alias, line)` pairs through each port's ingest, in order."""
    loop = stack.app.state.ports._loop
    for alias, line in items:
        port = board(stack, alias)
        asyncio.run_coroutine_threadsafe(port._store_rx_line(time.time(), line), loop).result(10)


def raw_events(stack: Stack, alias: str, raws: list[str]) -> None:
    """Store event rows without ingest decoding (cheap bulk `!pd` rebroadcasts)."""
    store = stack.app.state.store

    async def go() -> None:
        for raw in raws:
            await store.add_line(ts=time.time(), port=alias, dir="rx", chan="event", seq=None,
                                 raw=raw)

    asyncio.run_coroutine_threadsafe(go(), stack.app.state.ports._loop).result(60)


def export(stack: Stack, **params) -> list[list[str]]:
    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        r = c.get("/plot/export", params=params)
    assert r.status_code == 200, r.text
    return [line.split(",") for line in r.text.strip().splitlines()]


A_DEF = "!pd 3 mode:u1:=0=A_IDLE,1=A_RUN v:u1"
B_DEF = "!pd 3 mode:u1:=0=B_OFF,1=B_ON v:u1"


def s3(tick: int, mode: int, v: int) -> str:
    return f"!ps 3 {tick:X} {mode:02X},{v:02X}"


# -- /plot/channels -------------------------------------------------------------------------


def plot_channels(stack: Stack, **params) -> dict:
    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        r = c.get("/plot/channels", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def labels(body: dict, name: str = "mode") -> list:
    return [ch["labels"] for ch in body["channels"] if ch["name"] == name]


def test_channels_label_each_attached_board_from_its_own_definition(
    make_stack: Callable[..., Stack],
) -> None:
    # B declares last and sends the newest sample, so a name-merged lookup labels A as B.
    stack = make_stack()
    a = stack.alias
    feed(stack, (a, A_DEF), ("b", B_DEF), (a, s3(1, 1, 5)), ("b", s3(1, 1, 5)))
    assert labels(plot_channels(stack, port=a)) == [[[0, "A_IDLE"], [1, "A_RUN"]]]
    assert labels(plot_channels(stack, port="b")) == [[[0, "B_OFF"], [1, "B_ON"]]]
    body = plot_channels(stack)
    assert [ch["port"] for ch in body["channels"] if ch["name"] == "mode"] == ["b"]
    assert labels(body) == [[[0, "B_OFF"], [1, "B_ON"]]]
    assert body["ports"] == sorted([a, "b"])


def test_channels_list_a_board_shadowed_on_every_name_and_label_it_once_detached(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    a = stack.alias
    feed(stack, ("b", B_DEF), ("b", s3(1, 1, 5)), (a, A_DEF), (a, s3(1, 1, 5)))
    del stack.app.state.ports._ports["b"]   # detached: its decoder is gone, its rows stay
    body = plot_channels(stack)
    assert {ch["port"] for ch in body["channels"]} == {a}, "a's samples are newest on every name"
    assert body["ports"] == sorted([a, "b"]), "the shadowed board must still be discoverable"
    # No decoder for b any more: its rows take the name's newest definition from any port.
    assert labels(plot_channels(stack, port="b")) == [[[0, "A_IDLE"], [1, "A_RUN"]]]


# -- /plot/export without port= -----------------------------------------------------------


def test_long_decode_labels_each_board_from_its_own_definition(
    make_stack: Callable[..., Stack],
) -> None:
    # B declares last, so one decoder primed over both ports gave B's labels to A's rows.
    stack = make_stack()
    a = stack.alias
    feed(stack, (a, A_DEF), ("b", B_DEF), (a, s3(1, 1, 5)), ("b", s3(1, 1, 5)))
    rows = export(stack, names="mode", decode=1)
    assert [r[4] for r in rows[1:]] == ["A_RUN", "B_ON"]


def test_long_changes_keep_one_baseline_per_board(make_stack: Callable[..., Stack]) -> None:
    # (sid, name) baselines: B's first 5 read as "unchanged" after A's 5, and A's second 5
    # read as "changed" after B's 9.
    stack = make_stack()
    a = stack.alias
    feed(stack, (a, A_DEF), ("b", B_DEF),
         (a, s3(1, 0, 5)), ("b", s3(1, 0, 5)), ("b", s3(2, 0, 9)), (a, s3(2, 0, 5)))
    rows = export(stack, names="v", decode=1, changes=1)
    assert [r[4] for r in rows[1:]] == ["5.0", "5.0", "9.0"]


def test_wide_decode_and_changes_are_per_board(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack()
    a = stack.alias
    feed(stack, (a, A_DEF), ("b", B_DEF),
         (a, s3(1, 0, 5)), ("b", s3(1, 0, 5)), ("b", s3(2, 1, 5)), (a, s3(2, 0, 5)))
    rows = export(stack, names="mode,v", format="wide", decode=1, changes=1)
    # A's second sample equals A's first, so it is dropped although B's differs from it.
    assert [r[2:] for r in rows[1:]] == [["A_IDLE", "5.0"], ["B_OFF", "5.0"], ["B_ON", "5.0"]]


def test_a_redefinition_on_one_board_does_not_reach_the_other(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    a = stack.alias
    feed(stack, (a, A_DEF), ("b", B_DEF), (a, s3(1, 1, 0)), ("b", s3(1, 1, 0)),
         (a, "!pd 3 mode:u1:=0=A2_LOW,1=A2_HIGH v:u1"),
         (a, s3(2, 1, 0)), ("b", s3(2, 1, 0)))
    rows = export(stack, names="mode", decode=1)
    assert [r[4] for r in rows[1:]] == ["A_RUN", "B_ON", "A2_HIGH", "B_ON"]


def test_a_board_whose_only_definition_is_older_than_a_page_of_rebroadcasts(
    make_stack: Callable[..., Stack],
) -> None:
    # Priming read the newest 1000 !pd rows over every port: A's rebroadcasts filled them,
    # so B's samples exported with no labels at all.
    stack = make_stack()
    a = stack.alias
    feed(stack, ("b", B_DEF), (a, A_DEF))
    raw_events(stack, a, [A_DEF] * 1001)
    feed(stack, ("b", s3(1, 1, 0)), (a, s3(1, 0, 0)))
    rows = export(stack, names="mode", decode=1)
    assert [r[4] for r in rows[1:]] == ["B_ON", "A_IDLE"]


def test_port_scoped_decode_ignores_the_other_boards_later_definition(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    a = stack.alias
    feed(stack, (a, A_DEF), (a, s3(1, 1, 0)), ("b", B_DEF), (a, s3(2, 0, 0)))
    rows = export(stack, names="mode", decode=1, port=a)
    assert [r[4] for r in rows[1:]] == ["A_RUN", "A_IDLE"]


def test_session_bundle_decodes_each_board_from_its_own_definition(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    a = stack.alias
    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        feed(stack, (a, A_DEF), ("b", B_DEF))   # before the session: priming must reach back
        sid = c.post("/sessions", json={"name": "two-boards"}).json()["session"]["id"]
        feed(stack, (a, s3(1, 1, 7)), ("b", s3(1, 0, 8)))
        c.post("/sessions/stop")
        r = c.get(f"/sessions/{sid}/bundle")
    assert r.status_code == 200, r.text
    rows = zipfile.ZipFile(io.BytesIO(r.content)).read("plot_3.csv").decode().splitlines()
    assert [row.split(",")[2:] for row in rows[1:]] == [["A_RUN", "7.0"], ["B_OFF", "8.0"]]


# -- CLI priming ------------------------------------------------------------------------


def run(stack: Stack, capsys, *args: str) -> list[str]:
    rc = cli.main([*args, "--url", stack.base_url])
    out = capsys.readouterr()
    assert rc == 0, out.err
    return [line.split("| ", 1)[1] for line in out.out.splitlines()]


def test_cli_priming_reaches_a_board_behind_many_rebroadcasts_of_another(
    stack: Stack, capsys,
) -> None:
    # The old priming read the newest 40 !pd rows across ports; board a's 60 rebroadcasts
    # filled them and board b's sample printed raw.
    raw_events(stack, "b", ["!pd 9 bx:u1:mV"])
    raw_events(stack, "a", ["!pd 7 ax:u1"] * 60)
    raw_events(stack, "b", ["!ps 9 1 05"])
    raw_events(stack, "a", ["!ps 7 1 06"])
    out = run(stack, capsys, "lines", "--match", "^!ps [79] ", "--decode")
    assert out == ["s9 bx=5mV", "s7 ax=6"]


def test_cli_priming_covers_more_boards_than_the_old_cap(stack: Stack, capsys) -> None:
    # 50 boards declaring sid 4 with different fields, all older than every sample: a
    # newest-40 cap across ports left ten boards undecoded.
    boards = [f"p{i:02d}" for i in range(50)]
    for i, alias in enumerate(boards):
        raw_events(stack, alias, [f"!pd 4 f{i}:u1"])
    for alias in boards:
        raw_events(stack, alias, ["!ps 4 1 02"])
    out = run(stack, capsys, "lines", "--match", "^!ps 4 ", "--decode")
    assert out == [f"s4 f{i}=2" for i in range(50)]


def test_cli_session_window_primes_from_before_the_session(stack: Stack, capsys) -> None:
    raw_events(stack, "c", ["!pd 6 early:u1:degC"])
    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        c.post("/sessions", json={"name": "late-start"})
        raw_events(stack, "c", ["!ps 6 1 21"])
        c.post("/sessions/stop")
    out = run(stack, capsys, "lines", "--session", "late-start", "--match", "^!ps 6 ",
              "--decode")
    assert out == ["s6 early=33degC"]
