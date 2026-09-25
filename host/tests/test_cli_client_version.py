"""The CLI refuses a daemon older than itself, or a server that is not one (SPEC 4).

The daemon sends `X-Mcuscope-Version` on every response and the /ws handshake; the CLI
checks it before reading anything, since an older daemon drops what it does not declare.
"""

from __future__ import annotations

import json

import httpx
import pytest
import typer
import websockets

from mcuscope import __version__, cli, cli_client
from mcuscope.cli_client import VERSION_HEADER, Client, Settings
from tests.support import _REAL_OPEN, STATUS, UNREACHABLE, ScriptedWS

MISSING = "is not an mcuscope daemon (no version header)"
OLDER = f"is mcuscope 0.4.0, this mcu needs >= {__version__}"


def _serve(monkeypatch, headers: dict, body=None) -> list[str]:
    """Every Client on a transport answering `body` with exactly `headers`; the paths asked."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(200, json=body if body is not None else STATUS, headers=headers)

    transport = httpx.MockTransport(handler)

    def open_(self):
        self._transport = transport
        return _REAL_OPEN(self)

    monkeypatch.setattr(Client, "open", open_)
    return asked


@pytest.mark.parametrize("headers, msg", [({}, MISSING), ({VERSION_HEADER: "0.4.0"}, OLDER)])
def test_a_rest_answer_from_an_older_or_foreign_server_is_exit_1(monkeypatch, capsys, headers,
                                                                  msg) -> None:
    _serve(monkeypatch, headers)
    rc = cli.main([*UNREACHABLE, "status"])
    out, err = capsys.readouterr()
    assert rc == 1, err
    assert msg in err, err
    assert "uptime" not in out   # nothing of the answer was used


@pytest.mark.parametrize("mine, daemon", [
    (__version__, __version__), ("0.5.0", "99.0.0"), ("0.5.0", "0.5"), ("0.5.0", "0.5.1rc1"),
    ("0.6.0rc1", "0.6.0"), ("0.6.0rc1", "0.6.0rc2"), ("0.6.0a2", "0.6.0b1"),
    ("0.5.0.dev3+g1234", "0.5.0.dev3+g1234"),
])
def test_a_current_or_newer_daemon_is_let_through(monkeypatch, capsys, mine, daemon) -> None:
    monkeypatch.setattr(cli_client, "DAEMON_MIN_VERSION", mine)
    _serve(monkeypatch, {VERSION_HEADER: daemon})
    rc = cli.main([*UNREACHABLE, "status"])
    assert rc == 0, capsys.readouterr().err


@pytest.mark.parametrize("mine, daemon", [
    ("0.6.0rc1", "0.5.0"),              # X-cli-2: a pre-release mcu took any older daemon
    ("0.5.0", "0.5.0rc1"),              # and a release its own pre-release
    ("0.6.0rc2", "0.6.0rc1"), ("0.6.0b1", "0.6.0a9"),
    ("0.5.0", "0.5.0.dev3+g1234"),      # no order: must equal
    ("0.5.0.dev3+g1234", "0.6.0"),
    ("0.5.0", "9" * 5000),              # int() would raise past 4300 digits
])
def test_an_older_or_unorderable_daemon_is_refused(monkeypatch, capsys, mine, daemon) -> None:
    monkeypatch.setattr(cli_client, "DAEMON_MIN_VERSION", mine)
    _serve(monkeypatch, {VERSION_HEADER: daemon})
    rc = cli.main([*UNREACHABLE, "status"])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert f"this mcu needs >= {mine}" in err, err


def test_the_refusal_is_one_json_object_in_json_mode(monkeypatch, capsys) -> None:
    _serve(monkeypatch, {VERSION_HEADER: "0.4.0"})
    rc = cli.main([*UNREACHABLE, "--json", "status"])
    obj = json.loads(capsys.readouterr().out)
    assert rc == 1 and obj["exit_code"] == 1 and OLDER in obj["error"], obj


def test_a_download_from_an_older_daemon_writes_no_file(monkeypatch, capsys, tmp_path) -> None:
    """Checked on the stream's headers, before its body is written anywhere."""
    out_file = tmp_path / "run.db"
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path.endswith("/export"):   # only the stream itself is old
            return httpx.Response(200, content=b"SQLite format 3\0",
                                  headers={VERSION_HEADER: "0.4.0"})
        return httpx.Response(200, json={"sessions": [{"id": 1, "name": "run"}]},
                              headers={VERSION_HEADER: __version__})

    transport = httpx.MockTransport(handler)

    def open_(self):
        self._transport = transport
        return _REAL_OPEN(self)

    monkeypatch.setattr(Client, "open", open_)
    rc = cli.main([*UNREACHABLE, "session", "export", "run", "-o", str(out_file)])
    err = capsys.readouterr().err
    assert rc == 1 and OLDER in err, err
    assert any(p.endswith("/export") for p in asked), asked   # the refusal was the stream's
    assert not out_file.exists()


def test_daemon_status_still_answers_for_an_older_daemon(monkeypatch, capsys) -> None:
    """`probe` is exempt: `mcu daemon stop` must reach the daemon an upgrade replaces."""
    _serve(monkeypatch, {}, body={**STATUS, "version": "0.4.0", "pid": 4242})
    rc = cli.main([*UNREACHABLE, "daemon", "status"])
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert MISSING not in err


@pytest.mark.parametrize("headers, msg", [({}, MISSING), ({VERSION_HEADER: "0.4.0"}, OLDER)])
def test_a_follow_handshake_without_a_current_version_is_exit_1(monkeypatch, capsys, headers,
                                                                msg) -> None:
    row = {"id": 1, "ts": 1.0, "port": "b", "dir": "rx", "chan": "debug", "seq": None,
           "raw": "streamed"}
    _serve(monkeypatch, {VERSION_HEADER: __version__}, body={"lines": [], "ports": []})
    monkeypatch.setattr(websockets, "connect",
                        lambda *a, **kw: ScriptedWS([json.dumps([row])], headers=headers))
    rc = cli.main([*UNREACHABLE, "tail", "-f", "-n", "0"])
    out, err = capsys.readouterr()
    assert rc == 1, err
    assert msg in err and "streamed" not in out, out + err
    # Positive control: the same stream from a current daemon prints the row.
    monkeypatch.setattr(websockets, "connect",
                        lambda *a, **kw: ScriptedWS([json.dumps([row])]))
    cli.main([*UNREACHABLE, "tail", "-f", "-n", "0"])
    assert "streamed" in capsys.readouterr().out


def test_a_can_follow_poll_from_an_older_daemon_ends_it_not_retried(monkeypatch,
                                                                    capsys) -> None:
    """Not a failed poll to count and retry: no later poll changes the daemon's version."""
    import time

    monkeypatch.setattr(time, "sleep", lambda sec: None)
    polls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        polls.append(request.url.path)
        return httpx.Response(200, json={"frames": [], "capture": "c1"},
                              headers={VERSION_HEADER: "0.4.0"})

    s = Settings(url="http://127.0.0.1:1", json_out=False, port=None)
    client = Client(s, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(client, "get", lambda path, **kw: {"frames": []})
    with pytest.raises(typer.Exit) as ei:
        cli._dump_follow(client, s, None)
    err = capsys.readouterr().err
    assert ei.value.exit_code == 1, err
    assert OLDER in err and "skipping bad update" not in err, err
    assert polls.count("/can/frames") == 1, polls
