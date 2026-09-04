"""Serial break (`POST /break`, `mcu break`, `mcu sysrq`), driven at its edges.

A break is a side effect with no reply, so the assertions are the `sys` row it must
leave, the refusals it must give (out-of-range length, a port that is not connected)
and, for `sysrq`, that the character goes out bare and that a refused invocation does
not break the line anyway.
"""

from __future__ import annotations

import json
import subprocess

import httpx
import pytest

from tests.support import CHILD_TEXT, Stack
from tests.test_cli import MCU, run_mcu


def client(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=5.0)


def last_write(stack: Stack) -> bytes:
    """The most recent payload the port put on the wire (the connect-time ping precedes it)."""
    assert stack.sim.written, "nothing was ever written to the link"
    return stack.sim.written[-1]


# -- break -------------------------------------------------------------------------------


def test_break_logs_a_sys_row(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/break", json={"ms": 5})
        assert r.status_code == 200 and r.json() == {"ok": True}, r.text
        rows = c.get("/lines", params={"chan": "sys", "limit": 20}).json()["lines"]
    assert any(row["raw"] == f"port {stack.alias}: break 5 ms" for row in rows), rows


def test_break_defaults_to_250_ms(stack: Stack) -> None:
    with client(stack) as c:
        assert c.post("/break", json={}).status_code == 200
        rows = c.get("/lines", params={"chan": "sys", "limit": 20}).json()["lines"]
    assert any(row["raw"].endswith("break 250 ms") for row in rows), rows


@pytest.mark.parametrize("ms", [0, -1, 2001, 10**9])
def test_break_ms_out_of_range_is_422(stack: Stack, ms) -> None:
    with client(stack) as c:
        r = c.post("/break", json={"ms": ms})
    assert r.status_code == 422, f"ms={ms} was accepted: {r.text}"


def test_break_on_a_disconnected_port_is_400(stack: Stack) -> None:
    """The same refusal `/send` gives, and not a 500 from a None link."""
    with client(stack) as c:
        assert c.post(f"/ports/{stack.alias}/disconnect").status_code == 200
        assert stack.wait_connected(False)
        r = c.post("/break", json={"ms": 5})
        assert r.status_code == 400, r.text
        assert "not connected" in r.json()["error"]
        # And nothing was logged for a break that never happened.
        rows = c.get("/lines", params={"chan": "sys", "limit": 50}).json()["lines"]
    assert not any("break" in row["raw"] for row in rows), rows


def test_break_on_an_unknown_port_is_400(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/break", json={"port": "no-such-port", "ms": 5})
    assert r.status_code == 400, r.text


def test_cli_sysrq_refuses_more_than_one_character(stack: Stack) -> None:
    """Two characters would type the rest into the console as ordinary input."""
    r = run_mcu(stack, "sysrq", "bb")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "one character" in r.stdout + r.stderr
    with client(stack) as c:
        rows = c.get("/lines", params={"chan": "sys", "limit": 50}).json()["lines"]
    assert not any("break" in row["raw"] for row in rows), "a refused sysrq still broke the line"


def test_cli_sysrq_breaks_then_sends_one_bare_character(stack: Stack) -> None:
    r = run_mcu(stack, "sysrq", "b", "--ms", "5")
    assert r.returncode == 0, r.stdout + r.stderr
    assert last_write(stack) == b"b", "the character carried a terminator"
    with client(stack) as c:
        rows = c.get("/lines", params={"chan": "sys", "limit": 50}).json()["lines"]
    assert any(row["raw"].endswith("break 5 ms") for row in rows), rows


@pytest.mark.parametrize("ms", ["0", "2001", "-1"])
def test_cli_break_ms_out_of_range_is_bad_usage(stack: Stack, ms) -> None:
    r = run_mcu(stack, "break", "--ms", ms)
    assert r.returncode == 1, r.stdout + r.stderr


def test_cli_break_reports_the_length(stack: Stack) -> None:
    r = run_mcu(stack, "break", "--ms", "5")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "break 5 ms" in r.stdout


def test_cli_break_json_is_one_object(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "break", "--ms", "5")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout) == {"ok": True}


def test_cli_sysrq_json_is_one_object(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "sysrq", "t", "--ms", "5")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout) == {"ok": True, "char": "t", "ms": 5}


def test_ai_guide_documents_eol_and_sysrq() -> None:
    """The guide is what an agent reads; a CLI change that skips it is invisible."""
    guide = subprocess.run(
        [*MCU, "ai-guide"], capture_output=True, **CHILD_TEXT, timeout=60,
    ).stdout
    for needle in ("--eol", "mcu sysrq", "mcu break", "Ctrl-C"):
        assert needle in guide, f"ai-guide never mentions {needle}"
