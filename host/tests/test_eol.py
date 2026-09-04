"""Configurable line ending (`eol`) and serial break, driven at every entry point.

The interesting paths here are the refusals and the overrides, not the happy send: a
wrong `eol` must be refused identically by the config loader, the attach body, the
request body and the CLI, and an override must beat the port default rather than be
quietly dropped. Wire bytes are asserted against `stack.sim.written`, because nothing
downstream of the simulator's parser can tell `lf` from `crlf` from `none`.
"""

from __future__ import annotations

import json

import httpx
import pytest

from mcuscope.config import PortConfig, load_config, save_ports
from tests.support import Stack
from tests.test_cli import run_mcu


def client(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=5.0)


def last_write(stack: Stack) -> bytes:
    """The most recent payload the port put on the wire.

    Indexed from the end because the connect-time `ping` (SPEC 3.2 identify) and any
    earlier traffic sit in front of it.
    """
    assert stack.sim.written, "nothing was ever written to the link"
    return stack.sim.written[-1]


def reattach(stack: Stack, **extra) -> dict:
    """Replace the stack's port with one carrying `extra` (this is how eol is changed)."""
    with client(stack) as c:
        body = {"alias": stack.alias, "device": "sim://board", "baud": 115200, **extra}
        r = c.post("/ports", json=body)
        assert r.status_code == 200, r.text
    assert stack.wait_connected(True), "the replaced port never reconnected"
    return r.json()["port"]


# -- config loader ---------------------------------------------------------------------


def _write_port_config(tmp_path, eol_line: str) -> str:
    path = tmp_path / "config.toml"
    path.write_text(
        '[[ports]]\nalias = "board"\ndevice = "/dev/ttyACM0"\nbaud = 9600\n' + eol_line,
        encoding="utf-8", newline="\n",
    )
    return str(path)


def test_loader_takes_a_valid_eol(tmp_path) -> None:
    cfg = load_config(_write_port_config(tmp_path, 'eol = "crlf"\n'))
    assert cfg.ports[0].eol == "crlf"


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ('eol = "CRLF"\n', "wrong case is a wrong value, not a synonym"),
        ('eol = "\\r\\n"\n', "the terminator itself is not one of the names"),
        ('eol = ""\n', "empty is not 'no ending'; that is spelled none"),
        ("eol = 5\n", "wrong type"),
        ("eol = true\n", "a bool is not a name either"),
    ],
)
def test_loader_warns_and_defaults_on_a_bad_eol(tmp_path, caplog, line, why) -> None:
    """Wrong value keeps the default and keeps the port, like every other bad port key.

    Skipping the entry would be worse: a port that silently vanishes over a line-ending
    typo takes the whole bench down, where defaulting to `lf` still talks to a monitor.
    """
    with caplog.at_level("WARNING"):
        cfg = load_config(_write_port_config(tmp_path, line))
    assert len(cfg.ports) == 1, f"the entry was skipped instead of defaulted ({why})"
    assert cfg.ports[0].eol == "lf"
    assert cfg.ports[0].baud == 9600, "the rest of the entry was discarded with the bad key"
    assert any("eol" in r.message for r in caplog.records), "the bad key was defaulted in silence"


def test_save_ports_round_trips_eol(tmp_path) -> None:
    path = tmp_path / "config.toml"
    save_ports(path, [
        PortConfig(alias="a", device="/dev/ttyACM0", eol="crlf"),
        PortConfig(alias="b", device="/dev/ttyACM1", eol="none"),
        PortConfig(alias="c", device="/dev/ttyACM2"),
    ])
    text = path.read_text(encoding="utf-8")
    assert 'eol = "crlf"' in text and 'eol = "none"' in text
    assert text.count("eol") == 2, "the default was written out instead of left implicit"
    saved = {pc.alias: pc.eol for pc in load_config(str(path)).ports}
    assert saved == {"a": "crlf", "b": "none", "c": "lf"}


# -- attach and the port's own default -------------------------------------------------


def test_attach_rejects_an_unknown_eol(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/ports", json={"alias": "x", "device": "sim://board", "eol": "cr"})
    assert r.status_code == 422, r.text


def test_config_ports_write_back_rejects_an_unknown_eol(stack: Stack) -> None:
    with client(stack) as c:
        r = c.put("/config/ports", json={
            "ports": [{"alias": "board", "device": "sim://board", "eol": "CRLF"}]
        })
    assert r.status_code == 422, r.text


def test_config_ports_write_back_persists_eol(stack: Stack) -> None:
    with client(stack) as c:
        r = c.put("/config/ports", json={
            "ports": [{"alias": "board", "device": "sim://board", "eol": "crlf"}]
        })
        assert r.status_code == 200, r.text
        assert c.get("/config").json()["ports"][0]["eol"] == "crlf"
    assert load_config(stack.config_path).ports[0].eol == "crlf"


def test_status_reports_the_ports_eol(stack: Stack) -> None:
    with client(stack) as c:
        assert c.get("/status").json()["ports"][0]["eol"] == "lf"
    assert reattach(stack, eol="crlf")["eol"] == "crlf"
    with client(stack) as c:
        assert c.get("/status").json()["ports"][0]["eol"] == "crlf"


def test_reconnect_keeps_the_ports_eol(stack: Stack) -> None:
    """Reconnect re-attaches with the port's own parameters; eol is one of them."""
    reattach(stack, eol="crlf")
    with client(stack) as c:
        r = c.post(f"/ports/{stack.alias}/reconnect")
        assert r.status_code == 200, r.text
        assert r.json()["port"]["eol"] == "crlf", "reconnect reset the port to the default"


# -- the wire ---------------------------------------------------------------------------


def test_send_uses_the_ports_default_ending(stack: Stack) -> None:
    with client(stack) as c:
        assert c.post("/send", json={"line": "hello"}).status_code == 200
    assert last_write(stack) == b"hello\n"


def test_crlf_reaches_the_wire(stack: Stack) -> None:
    with client(stack) as c:
        assert c.post("/send", json={"line": "hello", "eol": "crlf"}).status_code == 200
    assert last_write(stack) == b"hello\r\n"


def test_a_ports_crlf_default_reaches_the_wire(stack: Stack) -> None:
    reattach(stack, eol="crlf")
    with client(stack) as c:
        assert c.post("/send", json={"line": "hello"}).status_code == 200
    assert last_write(stack) == b"hello\r\n"


def test_a_request_eol_beats_the_ports_default(stack: Stack) -> None:
    """Both directions: the override must win whichever way it disagrees."""
    reattach(stack, eol="crlf")
    with client(stack) as c:
        assert c.post("/send", json={"line": "a", "eol": "lf"}).status_code == 200
        assert last_write(stack) == b"a\n"
        assert c.post("/send", json={"line": "b", "eol": "none"}).status_code == 200
        assert last_write(stack) == b"b"
        assert c.post("/send", json={"line": "c"}).status_code == 200
        assert last_write(stack) == b"c\r\n", "omitting eol stopped meaning 'port default'"


def test_eol_none_sends_a_bare_control_character(stack: Stack) -> None:
    """Ctrl-C is 7-bit ASCII and not CR or LF, so it passes the body validation."""
    with client(stack) as c:
        assert c.post("/send", json={"line": "\x03", "eol": "none"}).status_code == 200
    assert last_write(stack) == b"\x03"


def test_the_stored_row_never_carries_the_terminator(stack: Stack) -> None:
    with client(stack) as c:
        for eol in ("none", "lf", "crlf"):
            assert c.post("/send", json={"line": f"row-{eol}", "eol": eol}).status_code == 200
        rows = c.get("/lines", params={"chan": "cmd", "limit": 20}).json()["lines"]
    raws = [r["raw"] for r in rows]
    for eol in ("none", "lf", "crlf"):
        assert f"row-{eol}" in raws, f"the {eol} send was not logged"
    assert not any("\r" in r or "\n" in r for r in raws), "a terminator leaked into the row"


def test_cmd_honours_a_request_eol(stack: Stack) -> None:
    """A command carries seq framing; the ending is appended after it, not instead of it."""
    with client(stack) as c:
        r = c.post("/cmd", json={"cmd": "ping", "eol": "crlf", "timeout_ms": 2000})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ok", r.text
    assert last_write(stack).endswith(b"\r\n")
    assert b"ping" in last_write(stack)


def test_wait_send_honours_eol(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/wait", json={
            "match": "nothing-will-match-this", "timeout_ms": 200,
            "send": "hello", "send_mode": "raw", "eol": "crlf",
        })
        assert r.status_code == 200, r.text
    assert last_write(stack) == b"hello\r\n", "/wait sent with the port default, not the override"


def test_assert_send_honours_eol(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/assert", json={
            "forbid": ["nothing-will-match-this"], "timeout_ms": 200,
            "send": "hello", "send_mode": "raw", "eol": "crlf",
        })
        assert r.status_code == 200, r.text
    assert last_write(stack) == b"hello\r\n", "/assert sent with the port default"


@pytest.mark.parametrize("path,body", [
    ("/send", {"line": "x"}),
    ("/cmd", {"cmd": "ping"}),
    ("/wait", {"match": "x", "timeout_ms": 100, "send": "y"}),
    ("/assert", {"forbid": ["x"], "timeout_ms": 100, "send": "y"}),
])
@pytest.mark.parametrize("bad", ["cr", "LF", "\r\n", "", "lf ", 1])
def test_an_unknown_request_eol_is_422(stack: Stack, path, body, bad) -> None:
    """Every entry point refuses the same set, so no path silently falls back to LF."""
    with client(stack) as c:
        r = c.post(path, json={**body, "eol": bad})
    assert r.status_code == 422, f"{path} accepted eol={bad!r}: {r.text}"


def test_an_embedded_newline_is_still_refused_with_eol_none(stack: Stack) -> None:
    """`none` relaxes the terminator, never the body rule: two wire lines in one row."""
    with client(stack) as c:
        r = c.post("/send", json={"line": "a\nb", "eol": "none"})
        assert r.status_code == 400, r.text
        assert "newline" in r.json()["error"]
        r = c.post("/send", json={"line": "a\rb", "eol": "none"})
        assert r.status_code == 400, r.text


def test_non_ascii_is_still_refused_with_eol_none(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/send", json={"line": "é", "eol": "none"})
    assert r.status_code == 400, r.text
    assert "ASCII" in r.json()["error"]


# -- CLI ----------------------------------------------------------------------------------


@pytest.mark.parametrize("args", [
    ["send", "--eol", "cr", "x"],
    ["cmd", "--eol", "CRLF", "ping"],
    ["wait", "--match", "x", "--eol", "bogus"],
    ["assert", "--forbid", "x", "--eol", "bogus"],
    ["attach", "sim://board", "--eol", "bogus"],
])
def test_cli_refuses_an_unknown_eol(stack: Stack, args) -> None:
    """Bad usage is exit 1 (SPEC 4), and the message names the option, not the field."""
    r = run_mcu(stack, *args)
    assert r.returncode == 1, r.stdout + r.stderr
    # The exact wording, not just "--eol": an unknown-option error names the option too,
    # and would satisfy a looser assertion on a command that never gained the flag.
    assert "none, lf, crlf" in r.stdout + r.stderr


def test_cli_send_eol_none_reaches_the_wire(stack: Stack) -> None:
    r = run_mcu(stack, "send", "--eol", "none", "\x03")
    assert r.returncode == 0, r.stdout + r.stderr
    assert last_write(stack) == b"\x03"


def test_cli_send_without_eol_uses_the_port_default(stack: Stack) -> None:
    reattach(stack, eol="crlf")
    assert run_mcu(stack, "send", "hi").returncode == 0
    assert last_write(stack) == b"hi\r\n"


def test_cli_attach_sets_the_ports_eol(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "attach", "sim://board", "--alias", "board", "--eol", "crlf")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["port"]["eol"] == "crlf"


