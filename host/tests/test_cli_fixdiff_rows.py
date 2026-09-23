"""Device text in CLI output: a `raw` that is not a string, and line boundaries in the
`assert` check lines."""

from __future__ import annotations

import json

import httpx
import pytest
import typer
import websockets

from mcuscope import cli
from mcuscope.cli_client import Settings
from mcuscope.cli_output import LineDecoder
from tests.test_cli import _ScriptedWS, run_mcu_canned

_S = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
_NULL_RAW = json.dumps([{"ts": 1.0, "chan": "debug", "raw": None, "port": "p", "id": 1}])
_KEPT = json.dumps([{"ts": 1.0, "chan": "debug", "raw": "kept", "port": "p", "id": 2}])


def _follow(monkeypatch, capsys, dec=None) -> tuple[str, str]:
    monkeypatch.setattr(websockets, "connect", lambda url, **kw: _ScriptedWS([_NULL_RAW, _KEPT]))
    with pytest.raises(typer.Exit) as ei:
        cli._follow_ws(_S, None, None, dec=dec)
    out, err = capsys.readouterr()
    assert ei.value.exit_code == 3, err      # the scripted stream's own end
    return out, err


def test_a_null_raw_prints_and_the_follow_goes_on(monkeypatch, capsys) -> None:
    out, err = _follow(monkeypatch, capsys)
    assert " debug| None\n" in out and "kept" in out
    assert "skipping bad frame" not in err


def test_a_null_raw_under_decode_is_a_skipped_row(monkeypatch, capsys) -> None:
    out, err = _follow(monkeypatch, capsys, dec=LineDecoder())
    assert "kept" in out
    assert "skipping bad frame" in err


def test_assert_check_lines_keep_each_device_line_on_one_line(monkeypatch, capsys) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/assert":
            return httpx.Response(200, json={
                "status": "fail", "checked_lines": 2, "elapsed_ms": 1,
                "expect": [{"pattern": "a", "matched": True, "line": {"raw": "a b"}}],
                "forbid": [{"pattern": "x", "matched": True, "line": {"raw": "x\x0by"}}],
            })
        return httpx.Response(200, json={"version": "9.9.9", "uptime_s": 1, "ports": []})

    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler,
                                  "assert", "--expect", "a", "--forbid", "x", "--last-ms", "1000")
    assert rc == 1
    assert "  ok      expect 'a': a\\u2028b\n" in out
    assert "  FAILED  forbid 'x': x\\x0by\n" in err
