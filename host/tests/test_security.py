"""Security hardening for the unauthenticated localhost API (SPEC 3.4 threat model).

The daemon has no auth by design, relying on a loopback bind. These tests lock in the guards
that keep a LAN peer or a malicious web page in the operator's browser from turning that into
host access: a device-scheme allowlist (no spy://...?file= file-clobber / SSRF gadget), a
same-origin guard (no cross-site CSRF / WebSocket exfil / DNS rebinding), CSV formula-injection
neutralization, and a few resource caps.
"""

from __future__ import annotations

import time

import httpx
import pytest

from mcuscope.serial_link import PortError, validate_device
from mcuscope.server import _csv_cell, _origin_matches_host
from tests.support import Stack


def client(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=5.0)


# -- device scheme allowlist (blocks the spy://...?file= write + odd serial_for_url gadgets) --


def test_validate_device_allows_real_and_supported_urls() -> None:
    for dev in (None, "/dev/ttyACM0", "COM7", "socket://127.0.0.1:9900", "rfc2217://host:2217"):
        validate_device(dev)  # must not raise


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


# -- same-origin guard (CSRF / cross-site WebSocket / DNS rebinding) ------------------------


def test_origin_matches_host_logic() -> None:
    assert _origin_matches_host(b"http://127.0.0.1:8765", b"127.0.0.1:8765")
    assert _origin_matches_host(b"https://192.168.1.5:8765", b"192.168.1.5:8765")
    assert not _origin_matches_host(b"http://evil.com", b"127.0.0.1:8765")
    assert not _origin_matches_host(b"http://127.0.0.1:8765", b"evil.com:8765")  # rebinding
    assert not _origin_matches_host(b"null", b"127.0.0.1:8765")


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
        time.sleep(0.2)
        r = c.get("/lines", params={"match": token})
    assert r.status_code == 200
    assert any(token in ln["raw"] for ln in r.json()["lines"])


def test_regexp_callback_matches() -> None:
    from mcuscope.store import _make_regexp

    rx = _make_regexp()
    assert rx("foo", "a foo b") is True
    assert rx("foo", "a bar b") is False
    assert rx("x", None) is False  # NULL raw column never matches
