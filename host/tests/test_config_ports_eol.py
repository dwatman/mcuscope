"""`PUT /config/ports` must not drop a saved `eol` the settings dialog never offered.

The dialog collects `{alias, autoconnect, device?, serial_number?, baud}`, so every Save
from the web UI omits `eol` and `identify`. A default that is a real value rather than
"keep what is saved" turns any unrelated save into a silent `crlf` -> `lf` reset, and a
CRLF-only target stops answering with nothing in the log naming the cause.
"""

from __future__ import annotations

import httpx

from mcuscope.config import load_config
from tests.support import Stack

# What settings.js collectPorts() builds: no eol, no identify.
DIALOG_BODY = {"ports": [{
    "alias": "board", "device": "sim://board", "baud": 115200, "autoconnect": True,
}]}


def client(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=10.0)


def _write_config(stack: Stack, body: str) -> None:
    with open(stack.config_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)


def test_a_ui_shaped_save_keeps_a_hand_written_eol(stack: Stack) -> None:
    _write_config(stack, (
        "[[ports]]\n"
        'alias = "board"\n'
        'device = "sim://board"\n'
        "baud = 115200\n"
        'eol = "crlf"\n'
        "identify = false\n"
    ))
    with client(stack) as c:
        assert c.put("/config/ports", json=DIALOG_BODY).status_code == 200
        assert c.get("/config").json()["ports"][0]["eol"] == "crlf"
    saved = load_config(stack.config_path)
    assert saved.ports[0].eol == "crlf"
    # identify is the sibling this mirrors; a regression in the lookup would take both.
    assert saved.ports[0].identify is False


def test_an_explicit_eol_still_wins(stack: Stack) -> None:
    _write_config(stack, (
        "[[ports]]\n"
        'alias = "board"\n'
        'device = "sim://board"\n'
        'eol = "crlf"\n'
    ))
    body = {"ports": [dict(DIALOG_BODY["ports"][0], eol="none")]}
    with client(stack) as c:
        assert c.put("/config/ports", json=body).status_code == 200
    assert load_config(stack.config_path).ports[0].eol == "none"


def test_a_new_alias_takes_the_default_eol(stack: Stack) -> None:
    _write_config(stack, "")
    with client(stack) as c:
        assert c.put("/config/ports", json=DIALOG_BODY).status_code == 200
    assert load_config(stack.config_path).ports[0].eol == "lf"
