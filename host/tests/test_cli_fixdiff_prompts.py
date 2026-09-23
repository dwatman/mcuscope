"""Declining at a real confirmation prompt (stdin a terminal): `cancelled`, exit 1, nothing
deleted, stdout empty. The piped-stdin tests are refused before the read, so they never
reach the answer check."""

from __future__ import annotations

import io
import json
import sys

import httpx
import pytest

from mcuscope import cli_output
from tests.test_cli import run_mcu_canned

_ANSWERS = pytest.mark.parametrize("answer", ["n\n", "", "yess\n"])   # no, EOF, not a yes


def _at_a_terminal(monkeypatch, answer: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(answer))
    monkeypatch.setattr(cli_output, "_stdin_is_interactive", lambda: True)


def _purge(sent: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json={"deleted": 5, "id_from": 1, "id_to": 5})
    return handler


@_ANSWERS
def test_declining_a_purge_deletes_nothing(monkeypatch, capsys, answer) -> None:
    sent: list[dict] = []
    _at_a_terminal(monkeypatch, answer)
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _purge(sent), "purge", "--all")
    assert rc == 1
    assert "[y/N]" in err and "cancelled" in err
    assert [b["dry_run"] for b in sent] == [True]
    assert out == ""


def _session(seen: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        if request.method == "DELETE":
            return httpx.Response(200, json={"lines_deleted": 7})
        return httpx.Response(200, json={"sessions": [{"id": 3, "name": "run", "lines": 7}]})
    return handler


@_ANSWERS
def test_declining_a_session_delete_deletes_nothing(monkeypatch, capsys, answer) -> None:
    seen: list[str] = []
    _at_a_terminal(monkeypatch, answer)
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _session(seen),
                                  "session", "delete", "run", "--data")
    assert rc == 1
    assert "delete session run and its 7 captured lines? [y/N]" in err and "cancelled" in err
    assert "DELETE" not in seen and seen
    assert out == ""


def test_yes_at_the_session_prompt_deletes(monkeypatch, capsys) -> None:
    """Positive control for the one above."""
    seen: list[str] = []
    _at_a_terminal(monkeypatch, "y\n")
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _session(seen),
                                  "session", "delete", "run", "--data")
    assert rc == 0, err
    assert seen[-1] == "DELETE"
    assert "deleted session run (7 lines)" in out
