"""`--last-ms` on the CLI takes the daemon's ceiling as well as its floor, from one anchor
(SPEC 3.4): rows stamped ahead of it (a clock stepped back since) are outside the window on
`mcu lines`, `mcu log export` and `mcu can dump`, as they are for `mcu assert`."""

from __future__ import annotations

import csv
import io
import json
import time

import pytest

from mcuscope import cli, store
from tests.support import UNREACHABLE, on_loop
from tests.test_export_lines_can import _can
from tests.test_flow_cli_windows import _add, _cli_json, _forward, _raws, tc  # noqa: F401

AHEAD_S = 3600.0   # far past the 10 s slack


def _ahead_and_now(tc) -> None:  # noqa: F811
    """`ahead` stamped an hour from now, written first; `now` stamped now, written last."""
    _add(tc, time.time() + AHEAD_S, "ahead")
    _can(tc, time.time() + AHEAD_S, 0x1AA)
    _add(tc, time.time(), "now")
    _can(tc, time.time(), 0x100)


LINE_FORMS = [
    ["lines", "--limit", "100"],
    ["log", "export"],                      # /lines/export, one request
    ["log", "export", "--limit", "100"],    # paged /lines
    ["log", "export", "--names", "v"],      # the ascending walk
]


@pytest.mark.parametrize("form", LINE_FORMS, ids=" ".join)
def test_last_ms_on_the_cli_leaves_out_rows_stamped_ahead_of_now(tc, monkeypatch, capsys,  # noqa: F811
                                                                 form) -> None:
    _ahead_and_now(tc)
    _forward(tc, monkeypatch)
    truth = _raws(tc.get("/lines", params={"last_ms": 60000, "chan": "debug"}).json()["lines"])
    assert truth == ["now"], truth
    got = _raws(_cli_json(capsys, [*form, "--chan", "debug", "--last-ms", "60000"]))
    assert got == ["now"], got
    # Positive control: the ahead row is there to be left out.
    assert "ahead" in _raws(_cli_json(capsys, [*form, "--chan", "debug"]))


@pytest.mark.parametrize("form", [["--json", "-n", "100"], ["--csv"]], ids=" ".join)
def test_can_dump_last_ms_leaves_out_frames_stamped_ahead_of_now(tc, monkeypatch, capsys,  # noqa: F811
                                                                 form) -> None:
    _ahead_and_now(tc)
    _forward(tc, monkeypatch)

    def can_ids(argv: list[str]) -> list[int]:
        rc = cli.main([*UNREACHABLE, "can", "dump", *form, *argv])
        out = capsys.readouterr()
        assert rc == 0, out.err
        if "--csv" in form:
            return sorted(int(r["can_id"]) for r in csv.DictReader(io.StringIO(out.out)))
        return sorted(json.loads(ln)["can_id"] for ln in out.out.splitlines() if ln.strip())

    assert can_ids([]) == [0x100, 0x1AA]   # positive control
    assert can_ids(["--last-ms", "60000"]) == [0x100]


def test_an_ended_sessions_last_ms_ceiling_counts_from_its_newest_line(tc, monkeypatch,  # noqa: F811
                                                                      capsys) -> None:
    """The anchor is the session's newest line by id, so its ceiling is that line plus 10 s."""
    s = tc.app.state.store
    on_loop(tc, s.start_session("old", ""))
    # Ended an hour ago, its newest line (the end marker) included: a ceiling counted
    # from now instead would let `between` through.
    old = time.time() - AHEAD_S
    _add(tc, old + 30, "between")
    _add(tc, old, "newest")

    class _OldClock:
        def __getattr__(self, name):
            return getattr(time, name)

        def time(self) -> float:
            return old + 1

    with monkeypatch.context() as m:
        m.setattr(store, "time", _OldClock())
        on_loop(tc, s.stop_session())
    _forward(tc, monkeypatch)
    window = {"session": "old", "last_ms": 60000, "chan": "debug"}
    assert _raws(tc.get("/lines", params=window).json()["lines"]) == ["newest"]
    for form in LINE_FORMS:
        got = _raws(_cli_json(capsys, [*form, "--session", "old", "--chan", "debug",
                                       "--last-ms", "60000"]))
        assert got == ["newest"], (form, got)


def test_a_later_to_does_not_lift_the_last_ms_ceiling(tc, monkeypatch, capsys) -> None:  # noqa: F811
    """`--to` intersects: a bound past the ceiling leaves the ceiling in force."""
    _ahead_and_now(tc)
    _forward(tc, monkeypatch)
    got = _raws(_cli_json(capsys, ["lines", "--chan", "debug", "--last-ms", "60000",
                                   "--to", "2099-01-01T00:00"]))
    assert got == ["now"], got


def test_an_earlier_to_still_bounds_a_last_ms_window(tc, monkeypatch, capsys) -> None:  # noqa: F811
    """`--to` inside the window is not widened to the ceiling."""
    _add(tc, time.time() - 30, "old")
    _add(tc, time.time(), "now")
    _forward(tc, monkeypatch)
    to = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 15))
    got = _raws(_cli_json(capsys, ["lines", "--chan", "debug", "--last-ms", "60000",
                                   "--to", to]))
    assert got == ["old"], got


def test_the_cli_slack_is_the_daemons() -> None:
    assert cli.WINDOW_TS_SLACK_S == store.WINDOW_TS_SLACK_S


def _clock(offset_s: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + offset_s))


@pytest.mark.parametrize("bound", [["--from", 120.0], ["--to", -120.0]], ids=lambda b: b[0])
def test_a_bound_outside_the_last_ms_window_gives_an_empty_answer(tc, monkeypatch, capsys,  # noqa: F811
                                                                   bound) -> None:
    """A --from past the ceiling, or a --to before the floor, intersects to nothing: exit 0
    and no rows on every windowed command, not a refusal naming a bound nobody typed."""
    for offset, raw in ((-600.0, "old"), (0.0, "now"), (300.0, "soon")):   # in id order
        _add(tc, time.time() + offset, raw)
        _can(tc, time.time() + offset, 0x100)
    _forward(tc, monkeypatch)
    opt, offset = bound
    argv = [opt, _clock(offset), "--last-ms", "60000"]
    # Positive control: the bound alone selects rows, so the empty answer is the window's.
    assert _raws(_cli_json(capsys, ["lines", "--chan", "debug", opt, _clock(offset)]))
    assert _cli_json(capsys, ["can", "dump", "-n", "100", opt, _clock(offset)])
    for form in LINE_FORMS:
        assert _raws(_cli_json(capsys, [*form, "--chan", "debug", *argv])) == [], form
    assert _cli_json(capsys, ["can", "dump", "-n", "100", *argv]) == []
