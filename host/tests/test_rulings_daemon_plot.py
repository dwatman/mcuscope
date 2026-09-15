"""Owner rulings 2026-09-15, plot side: C-3 detached definitions, negative deadbands,
per-port bundle members (SPEC 9.2, 3.4)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable

import httpx

from mcuscope import serial_link
from tests.support import Stack
from tests.test_decode_per_port import A_DEF, B_DEF, board, feed, plot_channels, raw_events, s3

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
