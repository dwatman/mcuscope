"""`mcu status`, `mcu devices` and `mcu plot channels` output (SPEC 4)."""

from __future__ import annotations

import json

import pytest

from mcuscope import cli
from tests.support import STATUS, UNREACHABLE, recorder

# -- improvement 6: trimmed lines in `mcu status` --------------------------------------


def test_status_reports_trimmed_lines_when_there_are_some(monkeypatch, capsys) -> None:
    recorder(monkeypatch, status={**STATUS, "lines_trimmed": 796693})
    assert cli.main(["status", *UNREACHABLE]) == 0
    assert "trimmed=796693" in capsys.readouterr().out


@pytest.mark.parametrize("body", [{"lines_trimmed": 0}, {}])
def test_status_stays_quiet_about_trimming_when_there_is_none(monkeypatch, capsys,
                                                              body) -> None:
    """Quiet when zero, and `.get` for a daemon too old to send the counter."""
    recorder(monkeypatch, status={**STATUS, **body})
    assert cli.main(["status", *UNREACHABLE]) == 0
    assert "trimmed" not in capsys.readouterr().out


# -- improvement 8: plot channel values ------------------------------------------------


CHANNELS = {"channels": [
    {"name": "vbat", "sid": 0, "type": "f4", "unit": "V", "last_value": 0.14090123772621155,
     "last_ts": None, "count": 3},
    {"name": "lane", "sid": 0, "type": "u1", "unit": None, "last_value": 1.0,
     "last_ts": None, "count": 3},
]}


def test_plot_channels_renders_the_last_value_readably(monkeypatch, capsys) -> None:
    recorder(monkeypatch, plot_channels=CHANNELS)
    assert cli.main(["plot", "channels", *UNREACHABLE]) == 0
    out = capsys.readouterr().out
    assert "last=0.140901 V" in out, out
    assert "last=1 " in out, out
    assert "0.14090123772621155" not in out


def test_plot_channels_json_keeps_the_full_precision(monkeypatch, capsys) -> None:
    """The fix belongs on the text side of the --json branch, not before it."""
    recorder(monkeypatch, plot_channels=CHANNELS)
    assert cli.main(["--json", "plot", "channels", *UNREACHABLE]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["channels"][0]["last_value"] == 0.14090123772621155


# -- improvement 13: `mcu devices` columns ---------------------------------------------


def test_devices_labels_its_columns(monkeypatch, capsys) -> None:
    recorder(monkeypatch, devices={"devices": [
        {"device": "/dev/ttyACM0", "description": "STLINK-V3", "vid_pid": "0483:374f",
         "serial_number": "0672FF3"},
    ]})
    assert cli.main(["devices", *UNREACHABLE]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == ["device", "description", "vid:pid", "serial"], out
    assert "0672FF3" in out


def test_devices_with_none_attached_prints_only_its_message(monkeypatch, capsys) -> None:
    """A header over "no serial devices found" reads as a table that failed to load."""
    recorder(monkeypatch, devices={"devices": []})
    assert cli.main(["devices", *UNREACHABLE]) == 0
    assert capsys.readouterr().out == "no serial devices found\n"
