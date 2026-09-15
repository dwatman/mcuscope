"""Fix-diff round 2, daemon batch: FD2-3, FD2-4 and FD2-7.

FD2-3 `/plot/channels` re-learned a detached board's stored `!pd` rows on every request.
FD2-4 two stored ports that sanitise to one bundle stem wrote one zip member twice.
FD2-7 the WS handshake refusal (SPEC 3.1) was only ever asserted through Starlette's
TestClient, which surfaces the ASGI close instead of the status that goes on the wire.
"""

from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from collections.abc import Callable

import httpx
import pytest
import uvicorn
import websockets

from mcuscope import daemon as daemon_mod
from mcuscope import server as server_mod
from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app
from tests.support import Stack, free_port
from tests.test_decode_per_port import A_DEF, B_DEF, board, feed, plot_channels, s3

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


# -- FD2-7: the WS handshake refusal on the wire -----------------------------------------

TOKEN = "sesame-open-123"


@pytest.fixture
def token_ws_url(tmp_path, monkeypatch) -> str:
    """A real uvicorn server with a token set, reached as a non-loopback client would be.

    The handshake refusal is a pre-accept ASGI close, which only uvicorn turns into the
    HTTP status SPEC 3.1 names; Starlette's TestClient surfaces the close code instead.
    Every client here connects from 127.0.0.1, so the loopback exemption is lifted rather
    than the test binding a routable address.
    """
    monkeypatch.setattr(server_mod, "_LOOPBACK_CLIENTS", frozenset())
    port = free_port()
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=port, token=TOKEN),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), retention_days=7),
        ports=[],
    )
    app = create_app(config, config_path=tmp_path / "config.toml")
    server = daemon_mod.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not getattr(server, "started", False):
        time.sleep(0.02)
    assert getattr(server, "started", False), "the token server never started"
    try:
        yield f"ws://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=8.0)


async def test_a_ws_handshake_without_the_token_is_refused_with_http_403(token_ws_url) -> None:
    for url, why in ((f"{token_ws_url}/ws", "no token"),
                     (f"{token_ws_url}/ws?token=wrong", "a wrong token")):
        with pytest.raises(websockets.exceptions.InvalidStatus) as refused:
            async with websockets.connect(url):
                pass
        # SPEC 3.1: the handshake itself is refused, which is what lets the page fall back
        # to the /status 401 prompt (a browser can only report this as close 1006).
        assert refused.value.response.status_code == 403, why
    # Positive control: the same handshake carrying the token is answered 101, so the 403
    # above is the refusal and not this endpoint's answer to every client.
    async with websockets.connect(f"{token_ws_url}/ws?token={TOKEN}") as ws:
        assert ws.response.status_code == 101
