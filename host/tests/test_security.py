"""Security hardening for the unauthenticated localhost API (SPEC 3.4 threat model).

The daemon has no auth by design, relying on a loopback bind. These tests lock in the guards
that keep a LAN peer or a malicious web page in the operator's browser from turning that into
host access: a device-scheme allowlist (no spy://...?file= file-clobber / SSRF gadget), a
same-origin guard (no cross-site CSRF / WebSocket exfil / DNS rebinding), CSV formula-injection
neutralization, and a few resource caps.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from mcuscope.link import open_link
from mcuscope.serial_link import PortError, validate_device
from mcuscope.server import _csv_cell, _host_allowed, _origin_matches_host
from tests.support import Stack
from tests.test_e2e import poll


def client(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=5.0)


# -- device scheme allowlist (blocks the spy://...?file= write + odd serial_for_url gadgets) --


def test_validate_device_allows_real_and_supported_urls() -> None:
    for dev in (None, "/dev/ttyACM0", "COM7", "socket://127.0.0.1:9900", "rfc2217://host:2217"):
        validate_device(dev)  # must not raise


def test_the_sim_scheme_is_not_a_serial_for_url_gadget() -> None:
    # sim:// is allowlisted so a sim-backed port validates and reads as a remote transport,
    # but nothing serves it unless the app was given the simulator's link factory. Reaching
    # the default opener it must fail closed, the same as any unknown scheme would.
    validate_device("sim://board")
    with pytest.raises(ValueError, match="protocol 'sim' not known"):
        open_link("sim://board", 115200)


def test_validate_device_rejects_dangerous_schemes() -> None:
    for dev in ("spy://loop://?file=/tmp/x", "spy://COM1", "loop://", "hwgrep://x", "alt://x"):
        with pytest.raises(PortError):
            validate_device(dev)


def test_validate_device_rejects_query_options_and_control_chars() -> None:
    with pytest.raises(PortError):
        validate_device("socket://127.0.0.1:9900?logging=debug")  # ? carries the file= vector
    with pytest.raises(PortError):
        validate_device("/dev/ttyACM0\nrm -rf")
    with pytest.raises(PortError):
        validate_device("")


def test_attach_rejects_dangerous_device_over_api(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/ports", json={"alias": "evil", "device": "spy://loop://?file=/tmp/pwn"})
    assert r.status_code == 400
    assert "scheme" in r.json()["error"] or "query" in r.json()["error"]


def test_attach_denied_from_network_without_token(stack: Stack) -> None:
    # POST /ports is held to the config-write bar (SPEC 3.4): a device string can
    # name a network destination (socket://), so a tokenless network client could
    # point the daemon's serial traffic at a host of its choosing.
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=stack._server.config.app, client=("203.0.113.5", 4444)
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post(
                "/ports",
                json={"alias": "evil", "device": "socket://203.0.113.5:9"},
                headers={"host": "127.0.0.1"},
            )

    r = asyncio.run(go())
    assert r.status_code == 403
    assert "token" in r.json()["error"]


# -- same-origin guard (CSRF / cross-site WebSocket / DNS rebinding) ------------------------


def test_origin_matches_host_logic() -> None:
    assert _origin_matches_host(b"http://127.0.0.1:8558", b"127.0.0.1:8558")
    assert _origin_matches_host(b"https://192.168.1.5:8558", b"192.168.1.5:8558")
    assert not _origin_matches_host(b"http://evil.com", b"127.0.0.1:8558")
    assert not _origin_matches_host(b"null", b"127.0.0.1:8558")


def test_host_allowed_logic() -> None:
    """The actual rebinding defence: Origin-vs-Host cannot provide one.

    In a real attack the page's Origin *is* the attacker hostname and the Host header
    carries that same hostname, so they match. This case used to be asserted the other way
    round (Origin 127.0.0.1 against Host evil.com), which no browser ever sends, so the
    scenario went untested. Rebinding needs a DNS name; an IP literal cannot be rebound.
    """
    assert _host_allowed(b"127.0.0.1:8558", "127.0.0.1")
    assert _host_allowed(b"localhost:8558", "127.0.0.1")
    assert _host_allowed(b"192.168.1.5:8558", "0.0.0.0")
    assert _host_allowed(b"[::1]:8558", "127.0.0.1")
    assert _host_allowed(b"mcubox.local:8558", "mcubox.local")   # the configured bind name
    assert not _host_allowed(b"evil.example:8558", "127.0.0.1")  # the rebinding case
    assert not _host_allowed(b"evil.example", "127.0.0.1")
    assert not _host_allowed(b"", "127.0.0.1")


def test_rebound_host_refused_even_with_matching_origin(stack: Stack) -> None:
    """A rebound page sends Origin == Host, and often no Origin at all."""
    port = stack.base_url.rsplit(":", 1)[1]
    evil = f"evil.example:{port}"
    with client(stack) as c:
        matched = c.get("/status", headers={"Host": evil, "Origin": f"http://{evil}"})
        no_origin = c.get("/status", headers={"Host": evil})
        write_back = c.put(
            "/config/server", headers={"Host": evil, "Origin": f"http://{evil}"},
            json={"host": "0.0.0.0", "port": 8558},
        )
    assert matched.status_code == 403
    assert no_origin.status_code == 403
    assert write_back.status_code == 403


def test_cross_origin_request_refused(stack: Stack) -> None:
    host = stack.base_url.split("://", 1)[1]
    with client(stack) as c:
        cross = c.get("/status", headers={"Origin": "http://evil.example"})
        same = c.get("/status", headers={"Origin": f"http://{host}"})
        none = c.get("/status")  # non-browser client: no Origin
    assert cross.status_code == 403
    assert same.status_code == 200
    assert none.status_code == 200


def test_cross_origin_post_refused(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post(
            "/ports",
            json={"alias": "x", "device": "socket://127.0.0.1:9"},
            headers={"Origin": "http://attacker.test"},
        )
    assert r.status_code == 403


# -- CSV formula / structure injection ------------------------------------------------------


def test_csv_cell_neutralizes_formulas_and_delimiters() -> None:
    assert _csv_cell("=SUM(A1)") == "'=SUM(A1)"
    assert _csv_cell("+1") == "'+1"
    assert _csv_cell("@cmd") == "'@cmd"
    assert _csv_cell("a,b") == '"a,b"'
    assert _csv_cell('a"b') == '"a""b"'
    assert _csv_cell("safe_name") == "safe_name"
    assert _csv_cell(None) == ""


def test_csv_cell_matches_the_shared_fixture() -> None:
    """csv_cell_cases.json pins _csv_cell and the web UI's csvField to one rule set.

    The browser's CAN-table export (can.js csvField) re-implements this function in JS;
    both sides assert the same fixture, so a rule changed on one side fails the other.
    """
    import json
    import pathlib

    cases = json.loads(
        (pathlib.Path(__file__).parent / "csv_cell_cases.json").read_text(encoding="utf-8")
    )
    assert len(cases) >= 10   # the file went missing or was emptied, not "all passed"
    for value, expected in cases:
        assert _csv_cell(value) == expected, f"input {value!r}"


# -- resource caps --------------------------------------------------------------------------


def test_oversized_send_rejected(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/send", json={"line": "A" * 300})
    assert r.status_code == 400
    assert "limit" in r.json()["error"]


def test_overlong_match_rejected(stack: Stack) -> None:
    with client(stack) as c:
        r = c.get("/lines", params={"match": "a" * 201})
    assert r.status_code == 400


# -- ReDoS: match evaluation runs off the event loop (stdlib re, no daemon freeze) ----------


def test_match_query_returns_results(stack: Stack) -> None:
    # Exercises the off-loop query_lines_safe path (private read connection on the executor).
    token = "ReDoSProbeToken42"
    with client(stack) as c:
        c.post("/marker", json={"text": token})

        def _marker_visible() -> bool:
            resp = c.get("/lines", params={"match": token})
            return resp.status_code == 200 and any(
                token in ln["raw"] for ln in resp.json()["lines"]
            )

        assert poll(_marker_visible)
        r = c.get("/lines", params={"match": token})
    assert r.status_code == 200
    assert any(token in ln["raw"] for ln in r.json()["lines"])


def test_regexp_callback_matches() -> None:
    from mcuscope.store import _make_regexp

    rx = _make_regexp()
    assert rx("foo", "a foo b") is True
    assert rx("foo", "a bar b") is False
    assert rx("x", None) is False  # NULL raw column never matches


# -- the same-origin guard over WebSockets --------------------------------------------------


async def test_cross_origin_websocket_refused(stack: Stack) -> None:
    """A WebSocket handshake carrying a foreign Origin is refused before it streams anything.

    The HTTP siblings above cover the same guard, but a WebSocket handshake is not subject to
    CORS, so the browser will happily open one that it would never let a page read over HTTP.
    /ws streams the whole capture and the token guard exempts loopback, which is where the
    operator's browser is: this guard is the only thing in the way. It shipped with a
    dedicated close-1008 arm that no test drove.
    """
    import websockets
    from websockets.exceptions import InvalidStatus

    host = stack.base_url.split("://", 1)[1]
    url = "ws://" + host + "/ws"

    with pytest.raises(InvalidStatus) as excinfo:
        async with websockets.connect(url, additional_headers={"Origin": "http://evil.example"}):
            pass
    assert excinfo.value.response.status_code == 403

    # The same handshake from the page the daemon itself serves is accepted, so the assertion
    # above is about the Origin and not about WebSockets being broken here.
    async with websockets.connect(url, additional_headers={"Origin": "http://" + host}) as ws:
        assert await ws.recv() is not None


async def test_websocket_without_origin_is_accepted(stack: Stack) -> None:
    # A non-browser client sends no Origin and must still work, matching the HTTP rule. The
    # guard cannot simply refuse every WebSocket that does not prove its origin.
    import websockets

    host = stack.base_url.split("://", 1)[1]
    async with websockets.connect("ws://" + host + "/ws") as ws:
        assert await ws.recv() is not None
