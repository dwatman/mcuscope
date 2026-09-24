"""Decoding keyed per port: two boards may declare one sid with different fields (SPEC 2.5).

Covers the daemon's `/plot/export?decode` (with and without `port=`) and session bundles,
and the CLI's `!pd` priming, which read a newest-N across every port.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import zipfile
from collections.abc import Callable

import httpx
import pytest

from mcuscope import cli, serial_link
from mcuscope import server as server_mod
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
    # No decoder for b any more: its rows take b's own stored definition, never a's (C-3).
    assert labels(plot_channels(stack, port="b")) == [[[0, "B_OFF"], [1, "B_ON"]]]


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
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    rows_a = zf.read(f"plot_{a}_3.csv").decode().splitlines()
    rows_b = zf.read("plot_b_3.csv").decode().splitlines()
    assert [row.split(",")[2:] for row in rows_a[1:]] == [["A_RUN", "7.0"]]
    assert [row.split(",")[2:] for row in rows_b[1:]] == [["B_OFF", "8.0"]]


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


# A device that attaches and never connects: the port enters the manager without a
# simulator behind it storing rows of its own.
DEAD_DEVICE = "socket://127.0.0.1:9"


# -- FD2-3: the learned meta of a detached port is memoised ------------------------------


@pytest.fixture
def scans(monkeypatch) -> list[str]:
    """Every alias `/plot/channels` runs a stored-definition scan for, in order."""
    seen: list[str] = []
    real = server_mod.learn_stored_plot_defs

    async def spy(store, alias, decoder):
        seen.append(alias)
        await real(store, alias, decoder)

    monkeypatch.setattr(server_mod, "learn_stored_plot_defs", spy)
    return seen


def _detach(stack: Stack, alias: str) -> None:
    del stack.app.state.ports._ports[alias]


def test_a_detached_boards_definitions_are_learned_once(
    make_stack: Callable[..., Stack], scans: list[str]
) -> None:
    stack = make_stack()
    feed(stack, ("far", A_DEF), ("far", s3(1, 1, 5)))
    _detach(stack, "far")

    first = plot_channels(stack, port="far")
    assert scans == ["far"], "the first call must reach the scan (positive control)"
    second = plot_channels(stack, port="far")
    assert scans == ["far"], "the second call rescanned a board that cannot have changed"
    # The memoised answer is the same answer, not a cheaper wrong one.
    assert second == first
    labels = [ch for ch in second["channels"] if ch["name"] == "mode"][0]["labels"]
    assert labels == [[0, "A_IDLE"], [1, "A_RUN"]]


def test_replacing_the_capture_drops_the_learned_definitions(
    make_stack: Callable[..., Stack], scans: list[str]
) -> None:
    """A purge of the newest id mints a new capture: nothing learned from the old one holds."""
    stack = make_stack()
    feed(stack, ("far", A_DEF), ("far", s3(1, 1, 5)))
    _detach(stack, "far")
    assert plot_channels(stack, port="far")["channels"], "nothing to learn from"
    assert scans == ["far"]

    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        before = c.get("/status").json()["capture"]
        assert c.post("/purge", json={"all": True}).status_code == 200
        assert c.get("/status").json()["capture"] != before, "the purge kept the capture id"
    feed(stack, ("far", B_DEF), ("far", s3(1, 0, 5)))
    _detach(stack, "far")

    body = plot_channels(stack, port="far")
    assert scans == ["far", "far"], "the new capture was answered from the old scan"
    labels = [ch for ch in body["channels"] if ch["name"] == "mode"][0]["labels"]
    assert labels == [[0, "B_OFF"], [1, "B_ON"]], "the stale definitions outlived the purge"


def test_attaching_the_alias_drops_its_learned_definitions(
    make_stack: Callable[..., Stack],
) -> None:
    """Attached, the port stores new definitions; detached again, the old ones must be gone."""
    stack = make_stack()
    feed(stack, ("far", A_DEF), ("far", s3(1, 1, 5)))
    _detach(stack, "far")
    first = plot_channels(stack, port="far")
    assert [ch for ch in first["channels"] if ch["name"] == "mode"][0]["labels"][1] == \
        [1, "A_RUN"]

    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        r = c.post("/ports", json={"alias": "far", "device": DEAD_DEVICE, "baud": 115200})
        assert r.status_code == 200, r.text
        feed(stack, ("far", B_DEF), ("far", s3(2, 0, 5)))
        assert c.delete("/ports/far").status_code == 200

    body = plot_channels(stack, port="far")
    labels = [ch for ch in body["channels"] if ch["name"] == "mode"][0]["labels"]
    assert labels == [[0, "B_OFF"], [1, "B_ON"]], "served from the meta learned before attach"


# -- FD2-4: bundle members whose ports sanitise alike ------------------------------------


def test_two_ports_that_sanitise_alike_get_distinct_bundle_members(
    make_stack: Callable[..., Stack],
) -> None:
    """`a/b` and `a_b` both sanitise to `a_b`; one zip may not carry that name twice."""
    stack = make_stack()
    board(stack, "slash").alias = "a/b"      # stored rows are not held to the alias grammar
    board(stack, "under").alias = "a_b"
    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        sid = c.post("/sessions", json={"name": "collide"}).json()["session"]["id"]
        feed(stack, ("slash", A_DEF), ("slash", s3(1, 1, 7)),
             ("under", B_DEF), ("under", s3(1, 0, 8)))
        c.post("/sessions/stop")
        r = c.get(f"/sessions/{sid}/bundle")
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))

    names = zf.namelist()
    plots = [n for n in names if n.startswith("plot_")]
    assert sorted(plots) == ["plot_a_b-2_3.csv", "plot_a_b_3.csv"], plots
    assert len(names) == len(set(names)), f"a member name appears twice: {names}"
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["files"] == names, "the manifest must list exactly the zip's entries"
    # One board per member, and both boards survived: under the shared name the first
    # member's rows were lost and only the second board could be read back.
    rows = {n: zf.read(n).decode().splitlines()[1].split(",")[2:] for n in plots}
    assert sorted(rows.values()) == [["A_RUN", "7.0"], ["B_OFF", "8.0"]], rows


# -- C-3: /plot/channels for a board with no decoder --------------------------------------


def _meta(body: dict, port: str, name: str) -> dict:
    [ch] = [ch for ch in body["channels"] if ch["port"] == port and ch["name"] == name]
    return ch


def test_a_detached_board_keeps_its_own_definitions_beside_an_attached_one(
    make_stack: Callable[..., Stack],
) -> None:
    """Same channel names on both boards, different kinds and units: nothing may cross."""
    stack = make_stack()
    a = stack.alias
    far_def = "!pd 3 mode:u1:=0=OFF,1=ON v:u1*0.5:mA"
    feed(stack, ("far", far_def), ("far", s3(1, 1, 5)), (a, A_DEF), (a, s3(1, 1, 5)))
    assert _meta(plot_channels(stack, port="far"), "far", "v")["unit"] == "mA"
    del stack.app.state.ports._ports["far"]

    far = plot_channels(stack, port="far")
    mode, v = _meta(far, "far", "mode"), _meta(far, "far", "v")
    assert mode["kind"] == "enum" and mode["labels"] == [[0, "OFF"], [1, "ON"]]
    assert (v["unit"], v["scale"], v["type"]) == ("mA", 0.5, "u1")
    # The attached board is untouched by the lookup.
    assert _meta(plot_channels(stack, port=a), a, "mode")["labels"] == [[0, "A_IDLE"], [1, "A_RUN"]]


def test_two_detached_boards_sharing_names_each_get_their_own(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    feed(stack, ("x", A_DEF), ("x", s3(1, 1, 5)), ("y", B_DEF), ("y", s3(1, 1, 5)))
    for alias in ("x", "y"):
        del stack.app.state.ports._ports[alias]
    assert _meta(plot_channels(stack, port="x"), "x", "mode")["labels"][1] == [1, "A_RUN"]
    assert _meta(plot_channels(stack, port="y"), "y", "mode")["labels"][1] == [1, "B_ON"]


def test_a_detached_board_with_no_stored_definition_has_null_fields(
    make_stack: Callable[..., Stack],
) -> None:
    """The other board declares the name; the detached one never did within the lookback."""
    stack = make_stack()
    a = stack.alias
    feed(stack, (a, A_DEF), ("lost", A_DEF), ("lost", s3(1, 1, 5)))
    # Push lost's `!pd` below the lookback floor with rows of the stack's own port.
    raw_events(stack, a, ["filler"] * 50)
    old = serial_link.PLOT_DEF_LOOKBACK
    serial_link.PLOT_DEF_LOOKBACK = 10
    try:
        feed(stack, (a, s3(2, 1, 5)))
        del stack.app.state.ports._ports["lost"]
        mode = _meta(plot_channels(stack, port="lost"), "lost", "mode")
    finally:
        serial_link.PLOT_DEF_LOOKBACK = old
    assert (mode["type"], mode["unit"], mode["scale"], mode["labels"]) == (None,) * 4, mode
    assert (mode["group"], mode["bit"]) == (None, None), mode
    assert mode["kind"] == "analog", "the default for any channel with no known definition"


def test_a_detached_board_that_never_declared_a_stream_has_null_fields(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    a = stack.alias
    # a declares `mode` in a stream; adhoc only ever sent `!p mode=...`.
    feed(stack, (a, A_DEF), (a, s3(1, 1, 5)), ("adhoc", "!p 1 mode=1"))
    del stack.app.state.ports._ports["adhoc"]
    mode = _meta(plot_channels(stack, port="adhoc"), "adhoc", "mode")
    assert (mode["type"], mode["labels"], mode["sid"]) == (None, None, None), mode


def test_the_reconnect_window_takes_the_boards_own_stored_definitions(
    make_stack: Callable[..., Stack],
) -> None:
    """Mid-reconnect the alias is out of the manager, like a detach; another board's
    definition must not stand in for it."""
    stack = make_stack()
    a = stack.alias
    feed(stack, (a, A_DEF), (a, s3(1, 1, 5)), ("b", B_DEF), ("b", s3(1, 1, 5)))
    port = stack.app.state.ports._ports.pop(a)
    try:
        assert _meta(plot_channels(stack, port=a), a, "mode")["labels"][1] == [1, "A_RUN"]
    finally:
        stack.app.state.ports._ports[a] = port


def test_an_unfiltered_list_labels_each_row_from_its_row_port(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    a = stack.alias
    feed(stack, ("b", B_DEF), ("b", s3(1, 1, 5)), (a, A_DEF), (a, s3(1, 1, 5)))
    # b holds the newest sample on `mode` only.
    feed(stack, ("b", "!pd 4 solo:u1:=0=S0,1=S1"), ("b", "!ps 4 1 01"), ("b", s3(2, 1, 5)))
    feed(stack, (a, "!pd 5 aonly:u1*2:V"), (a, "!ps 5 1 01"))
    del stack.app.state.ports._ports["b"]
    body = plot_channels(stack)
    assert _meta(body, "b", "mode")["labels"][1] == [1, "B_ON"]
    assert _meta(body, "b", "solo")["labels"][1] == [1, "S1"]
    assert _meta(body, a, "aonly")["unit"] == "V"


def test_an_attached_board_does_not_rescan_the_store(
    make_stack: Callable[..., Stack], monkeypatch
) -> None:
    stack = make_stack()
    a = stack.alias
    feed(stack, (a, A_DEF), (a, s3(1, 1, 5)))
    calls: list[str] = []
    real = serial_link.learn_stored_plot_defs

    async def spy(store, alias, decoder):
        calls.append(alias)
        await real(store, alias, decoder)

    monkeypatch.setattr("mcuscope.server.learn_stored_plot_defs", spy)
    plot_channels(stack)
    assert calls == []
    # The positive control: the same spy does see the rescan a detached board needs.
    port = stack.app.state.ports._ports.pop(a)
    try:
        plot_channels(stack)
    finally:
        stack.app.state.ports._ports[a] = port
    assert calls == [a], "the spy is not on the path the rescan takes"


# -- negative deadbands ----------------------------------------------------------------------


def _export(stack: Stack, deadband: str) -> httpx.Response:
    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        return c.get("/plot/export", params={
            "names": "v", "decode": 1, "changes": 1, "deadband": deadband,
        })


def test_a_negative_deadband_is_refused_by_name(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack()
    a = stack.alias
    feed(stack, (a, A_DEF), (a, s3(1, 0, 5)), (a, s3(2, 0, 6)), (a, s3(3, 0, 9)))
    for bad in ("v=-1", "v=-0.5", "v=-1e-9"):
        r = _export(stack, bad)
        assert (r.status_code, r.json()) == (400, {"error": "deadband for v must be >= 0"}), bad
    # The grammar check still comes first for a malformed negative.
    r = _export(stack, "v=--1")
    assert r.json() == {"error": "deadband value is not a number: v=--1"}
    # Zero, signed or not, is accepted; a positive band still suppresses small moves.
    assert _export(stack, "v=-0").status_code == 200
    assert _export(stack, "v=0").status_code == 200
    rows = _export(stack, "v=1").text.strip().splitlines()[1:]
    assert [r.split(",")[4] for r in rows] == ["5.0", "9.0"]


# -- bundle members per port -----------------------------------------------------------------


def _bundle(stack: Stack, feed_items) -> zipfile.ZipFile:
    with httpx.Client(base_url=stack.base_url, timeout=30.0) as c:
        sid = c.post("/sessions", json={"name": "two"}).json()["session"]["id"]
        feed(stack, *feed_items)
        c.post("/sessions/stop")
        r = c.get(f"/sessions/{sid}/bundle")
    assert r.status_code == 200, r.text
    return zipfile.ZipFile(io.BytesIO(r.content))


def test_bundle_splits_one_sid_and_adhoc_per_port(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack()
    a = stack.alias
    zf = _bundle(stack, [
        (a, A_DEF), ("b", B_DEF), (a, s3(1, 1, 7)), ("b", s3(1, 0, 8)),
        (a, "!p 1 rpm=10"), ("b", "!p 1 rpm=20"),
    ])
    plots = sorted(n for n in zf.namelist() if n.startswith("plot_"))
    assert plots == sorted([f"plot_{a}_3.csv", "plot_b_3.csv", f"plot_{a}_adhoc.csv",
                            "plot_b_adhoc.csv"])
    assert "plot_3.csv" not in zf.namelist() and "plot_adhoc.csv" not in zf.namelist()
    assert zf.read(f"plot_{a}_3.csv").decode().splitlines()[1].split(",")[2:] == ["A_RUN", "7.0"]
    assert zf.read("plot_b_3.csv").decode().splitlines()[1].split(",")[2:] == ["B_OFF", "8.0"]
    a_adhoc = zf.read(f"plot_{a}_adhoc.csv").decode().splitlines()
    b_adhoc = zf.read("plot_b_adhoc.csv").decode().splitlines()
    assert len(a_adhoc) == 2 and a_adhoc[1].endswith(",rpm,10.0"), a_adhoc
    assert len(b_adhoc) == 2 and b_adhoc[1].endswith(",rpm,20.0"), b_adhoc


def test_bundle_member_names_sanitise_the_port(make_stack: Callable[..., Stack]) -> None:
    """Stored rows are not held to the alias grammar (an older daemon, a merged capture)."""
    stack = make_stack()
    board(stack, "odd").alias = "we ird/port"   # the rows land under this port string
    zf = _bundle(stack, [("odd", A_DEF), ("odd", s3(1, 1, 7))])
    assert "plot_we_ird_port_3.csv" in zf.namelist(), zf.namelist()
