"""The decoded timeline: paging past the /lines cap, wall-clock bounds, --decode/--changes.

A bench day judged from the capture needs every row of a run (not the newest 1000), a
window named by clock time, and plot samples readable as fields. Each test drives the
way that failed on the bench: a --limit above the cap, a --to inside a run, a sample
whose definition was declared before the window.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import time

import pytest
import typer

from mcuscope.cli_output import LineDecoder, fmt_age, parse_clock
from tests.support import Stack
from tests.test_cli import run_mcu

PD = "!pd 7 state:u1:=0=IDLE,1=CHARGING vbat:u2*0.01:V io:u1:/robot,relay,charger,bat"


@pytest.fixture(scope="module")
def stack():
    st = Stack()
    yield st
    st.close()


def _add_lines(stack: Stack, rows: list[tuple[str, str]], gap_s: float = 0.0) -> list[float]:
    """Store (chan, raw) rows on the daemon's own loop; returns their timestamps."""
    store = stack.app.state.store
    stamps: list[float] = []

    async def go() -> None:
        for chan, raw in rows:
            ts = time.time()
            stamps.append(ts)
            await store.add_line(ts=ts, port=stack.alias, dir="rx", chan=chan, seq=None, raw=raw)
            if gap_s:
                await asyncio.sleep(gap_s)

    asyncio.run_coroutine_threadsafe(go(), stack.app.state.ports._loop).result(60)
    return stamps


# -- paging ------------------------------------------------------------------------------


def test_lines_limit_above_the_cap_is_honoured(stack, tmp_path) -> None:
    _add_lines(stack, [("debug", f"bulk{i:04d}") for i in range(1200)])

    r = run_mcu(stack, "lines", "--match", "^bulk", "--limit", "1150")
    assert r.returncode == 0, r.stderr
    out = r.stdout.splitlines()
    assert len(out) == 1150
    assert out[0].endswith("bulk0050") and out[-1].endswith("bulk1199"), "newest 1150, oldest first"
    assert "truncated at 1150 rows; older matches exist (raise --limit or use --since-id)" \
        in r.stderr, "50 older rows exist and the note must say so"

    r = run_mcu(stack, "lines", "--match", "^bulk", "--limit", "5000")
    assert len(r.stdout.splitlines()) == 1200
    assert "truncated" not in r.stderr

    j = run_mcu(stack, "--json", "lines", "--match", "^bulk", "--limit", "1150")
    body = json.loads(j.stdout)
    assert len(body["lines"]) == 1150 and body["truncated"] is True
    assert body["lines"][0]["raw"] == "bulk1199", "--json keeps the endpoint's newest-first order"

    # An export is complete by default, and -o reports the true count.
    out_file = tmp_path / "bulk.txt"
    r = run_mcu(stack, "log", "export", "--match", "^bulk", "-o", str(out_file))
    assert r.stdout.strip() == f"wrote 1200 lines to {out_file}", r.stdout
    assert len(out_file.read_text().splitlines()) == 1200
    r = run_mcu(stack, "log", "export", "--match", "^bulk", "--limit", "10")
    assert len(r.stdout.splitlines()) == 10 and r.stdout.splitlines()[-1].endswith("bulk1199")

    r = run_mcu(stack, "tail", "-n", "1100", "--match", "^bulk")
    assert len(r.stdout.splitlines()) == 1100


# -- wall-clock bounds -------------------------------------------------------------------


def _iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).isoformat(timespec="milliseconds")


def test_from_and_to_select_by_clock_time(stack) -> None:
    stamps = _add_lines(stack, [("debug", f"clk-{c}") for c in "abc"], gap_s=0.05)
    mid_ab, mid_bc = (stamps[0] + stamps[1]) / 2, (stamps[1] + stamps[2]) / 2

    r = run_mcu(stack, "lines", "--match", "^clk-", "--from", _iso(mid_ab), "--to", _iso(mid_bc))
    assert r.returncode == 0, r.stderr
    assert [line[-5:] for line in r.stdout.splitlines()] == ["clk-b"]

    r = run_mcu(stack, "lines", "--match", "^clk-", "--from", _iso(mid_bc))
    assert [line[-5:] for line in r.stdout.splitlines()] == ["clk-c"]

    r = run_mcu(stack, "lines", "--match", "^clk-", "--to", _iso(mid_ab))
    assert [line[-5:] for line in r.stdout.splitlines()] == ["clk-a"]

    # --to past everything captured is no bound at all, not an empty answer.
    r = run_mcu(stack, "lines", "--match", "^clk-", "--to", _iso(time.time() + 3600))
    assert len(r.stdout.splitlines()) == 3


def test_a_malformed_clock_is_a_usage_error(stack) -> None:
    r = run_mcu(stack, "lines", "--from", "25:99")
    assert r.returncode == 1, "usage errors exit 1 (SPEC 4)"
    assert "expected HH:MM[:SS[.mmm]]" in r.stderr


class _FixedDate(datetime.date):
    @classmethod
    def today(cls) -> datetime.date:
        return cls(2026, 9, 1)


def test_parse_clock_forms(monkeypatch) -> None:
    from mcuscope import cli_output

    monkeypatch.setattr(cli_output.datetime, "date", _FixedDate)
    today = datetime.date(2026, 9, 1)
    assert parse_clock("19:53:35.250") == datetime.datetime.combine(
        today, datetime.time(19, 53, 35, 250_000)
    ).timestamp()
    at_1953 = datetime.datetime.combine(today, datetime.time(19, 53))
    assert parse_clock("19:53") == at_1953.timestamp()
    full = datetime.datetime(2026, 9, 1, 19, 53, 35)
    assert parse_clock("2026-09-01T19:53:35") == full.timestamp()


# -- decode ------------------------------------------------------------------------------


SAMPLES = [
    ("event", PD),
    ("event", "!ps 7 1B59 01,09FA,0F"),
    ("event", "!ps 7 1B5A 01,09FA,0F"),   # unchanged
    ("event", "!ps 7 1B5B 00,09F0,03"),   # state, vbat and two lanes changed
]


def test_decode_renders_fields_and_changes_filters(stack) -> None:
    _add_lines(stack, SAMPLES)
    full = "s7 state=CHARGING vbat=25.54V io=robot|relay|charger|bat"
    changed = "s7 state=IDLE vbat=25.44V io=robot|relay"

    r = run_mcu(stack, "lines", "--match", "^!p[ds] 7 ", "--decode")
    assert r.returncode == 0, r.stderr
    rendered = [line.split("| ", 1)[1] for line in r.stdout.splitlines()]
    assert rendered == [full, full, changed], "the !pd row is dropped, every sample rendered"

    r = run_mcu(stack, "lines", "--match", "^!p[ds] 7 ", "--decode", "--changes")
    assert [line.split("| ", 1)[1] for line in r.stdout.splitlines()] == [full, changed]

    r = run_mcu(stack, "lines", "--match", "^!p[ds] 7 ", "--names", "vbat")
    assert [line.split("| ", 1)[1] for line in r.stdout.splitlines()] == [
        "s7 vbat=25.54V", "s7 vbat=25.54V", "s7 vbat=25.44V",
    ]
    r = run_mcu(stack, "lines", "--match", "^!p[ds] 7 ", "--names", "relay", "--changes")
    assert [line.split("| ", 1)[1] for line in r.stdout.splitlines()] == [
        "s7 io=robot|relay|charger|bat", "s7 io=robot|relay",
    ], "a lane name selects its group"

    j = run_mcu(stack, "--json", "lines", "--match", "^!ps 7 ", "--decode", "--limit", "1")
    row = json.loads(j.stdout)["lines"][0]
    assert row["decoded"] == changed and row["raw"] == changed

    # --changes keeps the first sample of a run, not the last: the moment the change landed.
    ids = [r["id"] for r in json.loads(run_mcu(
        stack, "--json", "lines", "--match", "^!ps 7 ").stdout)["lines"]]   # newest first
    kept = [r["id"] for r in json.loads(run_mcu(
        stack, "--json", "lines", "--match", "^!ps 7 ", "--changes").stdout)["lines"]]
    assert kept == [ids[0], ids[2]], (ids, kept)

    r = run_mcu(stack, "tail", "-n", "3", "--match", "^!ps 7 ", "--decode", "--changes")
    assert [line.split("| ", 1)[1] for line in r.stdout.splitlines()] == [full, changed]

    r = run_mcu(stack, "log", "export", "--match", "^!p[ds] 7 ", "--decode", "--changes")
    assert [line.split("| ", 1)[1] for line in r.stdout.splitlines()] == [full, changed]


def test_decode_primes_from_a_definition_outside_the_window(stack) -> None:
    """The !pd was declared before the queried window: the sample must still decode. A
    redefinition *after* the window (a reflash) must not be the one used for it."""
    _add_lines(stack, [("event", "!pd 6 mode:u1:=0=OFF,1=ON"), ("event", "!ps 6 0A 01")])
    r = run_mcu(stack, "lines", "--match", "^!ps 6 0A", "--decode")
    assert r.stdout.strip().endswith("s6 mode=ON"), (r.stdout, r.stderr)

    _add_lines(stack, [("event", "!pd 6 later:u4"), ("event", "!ps 6 0B 00000009")])
    r = run_mcu(stack, "lines", "--match", "^!ps 6 0A", "--decode")
    assert r.stdout.strip().endswith("s6 mode=ON"), (r.stdout, r.stderr)
    r = run_mcu(stack, "lines", "--match", "^!ps 6 0B", "--decode")
    assert r.stdout.strip().endswith("s6 later=9"), (r.stdout, r.stderr)


def test_line_decoder_edges() -> None:
    dec = LineDecoder()
    assert dec.decode("!ps 7 1B59 01,09FA,0F") == "!ps 7 1B59 01,09FA,0F", "ahead of its def: raw"
    assert dec.decode("hello") == "hello"
    assert dec.decode("!can 100 - 100 DEADBEEF") == "!can 100 - 100 DEADBEEF"
    assert dec.decode(PD) is None
    assert dec.decode("!ps 7 1B59 02,0000,00") == "s7 state=2 vbat=0V io=-", \
        "an enum value with no label shows the number; no lane set shows -"
    assert dec.decode("!p 5 ax=1.5 ay=-2") == "p:ax,ay ax=1.5 ay=-2"

    # --changes tracks streams independently.
    dec = LineDecoder(changes=True)
    dec.decode(PD)
    dec.decode("!pd 8 t:u1")
    assert dec.decode("!ps 7 1 00,0000,00") is not None
    assert dec.decode("!ps 8 1 05") == "s8 t=5"
    assert dec.decode("!ps 7 2 00,0000,00") is None
    assert dec.decode("!ps 8 2 05") is None
    assert dec.decode("!ps 8 3 06") == "s8 t=6"

    # Priming is newest-first: the first definition seen for a sid wins.
    dec = LineDecoder()
    dec.prime(["!pd 7 new:u1", PD])
    assert dec.decode("!ps 7 1 09") == "s7 new=9"

    # --names with nothing left drops the sample; other lines still pass.
    dec = LineDecoder(names=["nothere"])
    dec.decode(PD)
    assert dec.decode("!ps 7 1 00,0000,00") is None
    assert dec.decode("debug text") == "debug text"


def test_fmt_age() -> None:
    assert [fmt_age(s) for s in (0, 59, 60, 3599, 3600, 86399, 86400 * 3.5, -5)] == [
        "0s", "59s", "1m", "59m", "1h", "23h", "3d", "0s",
    ]


def test_decode_uses_the_definition_in_force_at_each_point_of_the_window(stack) -> None:
    """A same-width redefinition inside the window renames the channel silently if the
    decoder is primed from the window's end; each half must decode with its own def.
    With --match hiding the !pd rows, the in-window definitions must still be learned."""
    _add_lines(stack, [
        ("event", "!pd 5 a:u2"), ("event", "!ps 5 01 0064"),
        ("event", "!pd 5 b:u2:mV"), ("event", "!ps 5 02 0064"),
    ])
    r = run_mcu(stack, "lines", "--match", "^!p[ds] 5 ", "--decode")
    assert [line.split("| ", 1)[1] for line in r.stdout.splitlines()] == ["s5 a=100", "s5 b=100mV"]
    # The filter excludes every !pd row from the fetched window.
    r = run_mcu(stack, "lines", "--match", "^!ps 5 ", "--decode")
    assert [line.split("| ", 1)[1] for line in r.stdout.splitlines()] == ["s5 a=100", "s5 b=100mV"]
    r = run_mcu(stack, "log", "export", "--match", "^!ps 5 ", "--decode")
    assert [line.split("| ", 1)[1] for line in r.stdout.splitlines()] == ["s5 a=100", "s5 b=100mV"]
    r = run_mcu(stack, "tail", "-n", "2", "--match", "^!ps 5 ", "--decode")
    assert [line.split("| ", 1)[1] for line in r.stdout.splitlines()] == ["s5 a=100", "s5 b=100mV"]


def test_from_after_to_is_a_usage_error(stack) -> None:
    r = run_mcu(stack, "lines", "--from", "19:00", "--to", "18:00")
    assert r.returncode == 1 and "--from 19:00 is after --to 18:00" in r.stderr


def test_parse_clock_grammar_is_explicit(monkeypatch) -> None:
    from mcuscope import cli_output

    monkeypatch.setattr(cli_output.datetime, "date", _FixedDate)
    today = datetime.date(2026, 9, 1)
    assert parse_clock("19:53:35.25") == datetime.datetime.combine(
        today, datetime.time(19, 53, 35, 250_000)
    ).timestamp(), "two fraction digits are fine on every supported Python"
    assert parse_clock("2026-09-01 07:05") == datetime.datetime(2026, 9, 1, 7, 5).timestamp()
    # The Arabic-Indic form is the \d instance: re matches every Unicode decimal digit,
    # and int() then converted it, so the window opened at 12:30.
    for bad in ("20260901", "19", "19:53:35+09:00", "2026-09-01T12:00:00Z", "24:00", "7:05",
                "\u0661\u0662:\u0663\u0660"):
        with pytest.raises(typer.BadParameter):
            parse_clock(bad)


def test_export_streams_every_row_oldest_first(stack, tmp_path) -> None:
    """Unlimited export pages ascending and writes as it goes; the file holds every row in
    capture order and the count is the true count (paging past 1000)."""
    _add_lines(stack, [("debug", f"strm{i:04d}") for i in range(2300)])
    out_file = tmp_path / "strm.txt"
    r = run_mcu(stack, "--json", "log", "export", "--match", "^strm", "-o", str(out_file))
    body = json.loads(r.stdout)
    lines = out_file.read_text(encoding="utf-8").splitlines()
    assert body["lines"] == 2300 == len(lines) and body["truncated"] is False
    assert [json.loads(x)["raw"] for x in lines[:2]] == ["strm0000", "strm0001"]
    assert json.loads(lines[-1])["raw"] == "strm2299"
    assert body["bytes"] == out_file.stat().st_size
