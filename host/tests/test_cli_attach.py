"""`mcu attach` and `mcu detach`: what each posts, the refusals made before any request,
and the note a rebinding prints (SPEC 4)."""

from __future__ import annotations

import json

import httpx
import pytest

from mcuscope import cli
from tests.support import UNREACHABLE, record_params, record_requests, recorder
from tests.test_cli import run_mcu_canned


def _ports_then_ok(listed):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=listed)
        return httpx.Response(200, json={"port": {"alias": "board", "connected": False}})

    return handler


@pytest.mark.parametrize("listed", [
    [{"alias": "board", "device": "/dev/ttyACM0"}],     # a list, not an object
    {"ports": 5},                                        # `ports` not a list
])
def test_a_malformed_ports_probe_still_attaches(monkeypatch, capsys, listed) -> None:
    rc, out, err = run_mcu_canned(monkeypatch, capsys, _ports_then_ok(listed),
                                  "attach", "/dev/ttyACM1", "--alias", "board")
    assert rc == 0, err
    assert "note:" not in err


def _attach(monkeypatch, capsys, before: dict, *args: str) -> str:
    rc, _, err = run_mcu_canned(monkeypatch, capsys,
                                _ports_then_ok({"ports": [{"alias": "board", **before}]}),
                                "attach", *args, "--alias", "board")
    assert rc == 0, err
    return err


_CONNECTED_BY_SERIAL = {"device": "/dev/ttyACM0", "serial_number": "0672FF3"}
_WAITING_FOR_SERIAL = {"device": "0672FF3", "serial_number": "0672FF3"}


def test_rebinding_a_serial_port_to_its_resolved_device_is_noted(monkeypatch, capsys) -> None:
    """The port stops following the serial although the device string is the same."""
    err = _attach(monkeypatch, capsys, _CONNECTED_BY_SERIAL, "/dev/ttyACM0")
    assert "note: board was attached to serial 0672FF3; it now names /dev/ttyACM0" in err


def test_an_unconnected_serial_port_is_named_as_a_serial(monkeypatch, capsys) -> None:
    err = _attach(monkeypatch, capsys, _WAITING_FOR_SERIAL, "--serial", "11AA22")
    assert "note: board was attached to serial 0672FF3; it now names serial 11AA22" in err


@pytest.mark.parametrize("before", [_CONNECTED_BY_SERIAL, _WAITING_FOR_SERIAL])
def test_reattaching_the_same_serial_is_quiet(monkeypatch, capsys, before) -> None:
    assert "note:" not in _attach(monkeypatch, capsys, before, "--serial", "0672FF3")


# -- improvement 10: attach by serial number -------------------------------------------


ATTACHED = {"port": {"alias": "b", "connected": False}}


def test_attach_by_serial_posts_the_serial_number_alone(monkeypatch, capsys) -> None:
    seen = recorder(monkeypatch, ports=ATTACHED)
    rc = cli.main(["attach", "--serial", "0672FF3", "--alias", "b", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    body = json.loads(seen[-1].content)   # the POST; a GET /ports alias check precedes it
    assert body["serial_number"] == "0672FF3"
    assert "device" not in body, body


def test_attach_by_device_still_posts_the_device_alone(monkeypatch, capsys) -> None:
    seen = recorder(monkeypatch, ports=ATTACHED)
    rc = cli.main(["attach", "/dev/ttyACM0", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    body = json.loads(seen[-1].content)
    assert body["device"] == "/dev/ttyACM0"
    assert "serial_number" not in body, body


def test_attach_refuses_a_device_and_a_serial_together(capsys) -> None:
    rc = cli.main(["attach", "/dev/ttyACM0", "--serial", "X", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--serial" in err and "both" in err, err


def test_attach_refuses_neither(capsys) -> None:
    rc = cli.main(["attach", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--serial" in err and "mcu devices" in err, err


# -- fix-diff F5: an alias derived from a serial number stays inside the grammar --------


def test_attach_derives_an_alias_inside_the_grammar_from_any_serial(monkeypatch,
                                                                    capsys) -> None:
    """A serial outside the alias grammar maps into it rather than refusing `alias`."""
    seen = recorder(monkeypatch, ports=ATTACHED)
    rc = cli.main(["attach", "--serial", "AB:CD", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert json.loads(seen[-1].content)["alias"] == "AB-CD"


# -- FC-9: detach names the alias it refused ----------------------------------------------

def test_detach_says_it_refused_the_alias_rather_than_looked_it_up(monkeypatch, capsys) -> None:
    seen = record_params(monkeypatch,
                         lambda r: httpx.Response(404, json={"error": "no such port: a"}))
    rc = cli.main([*UNREACHABLE, "detach", "a/b"])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert seen == [], seen
    assert "invalid alias 'a/b'" in err
    assert "no such port" not in err, "the CLI claimed a lookup it never made"


def test_a_real_miss_still_reports_the_daemons_no_such_port(monkeypatch, capsys) -> None:
    """Positive control: the wording the refusal must not borrow does reach the user."""
    seen = record_params(monkeypatch,
                         lambda r: httpx.Response(404, json={"error": "no such port: ab"}))
    rc = cli.main([*UNREACHABLE, "detach", "ab"])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert seen, "the alias was refused before the request"
    assert "no such port: ab" in err


# -- B-17: a blank serial --------------------------------------------------------------------


@pytest.mark.parametrize("serial", ["", "   "])
def test_attach_refuses_a_blank_serial(capsys, serial) -> None:
    rc = cli.main(["attach", "--serial", serial, "--alias", "x", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--serial is blank" in err, err


def test_attach_strips_the_serial_it_posts(monkeypatch, capsys) -> None:
    seen = recorder(monkeypatch, ports={"port": {"alias": "b", "connected": False}})
    assert cli.main(["attach", "--serial", " 0672FF3 ", *UNREACHABLE]) == 0
    body = json.loads(seen[-1].content)   # the POST; a GET /ports alias check precedes it
    assert body["serial_number"] == "0672FF3" and body["alias"] == "0672FF3", body


def test_detach_quotes_the_alias_so_a_query_character_cannot_pick_another_port(
    monkeypatch, capsys,
) -> None:
    seen = record_requests(monkeypatch,
                           lambda r: httpx.Response(400, json={"error": "no such port"}))
    rc = cli.main(["detach", "board?x"])
    assert rc == 1
    assert [r.url.raw_path for r in seen] == [b"/ports/board%3Fx"]


def test_detach_refuses_a_slash_before_any_request(monkeypatch, capsys) -> None:
    """A 404 there would be read as a daemon too old for the route."""
    seen = record_requests(monkeypatch, lambda r: httpx.Response(404, json={"error": "Not Found"}))
    rc = cli.main(["detach", "a/b"])
    assert rc == 1 and seen == []
    assert "an alias cannot contain '/'" in capsys.readouterr().err
