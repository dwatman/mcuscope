"""The CLI's streamed exports: `/lines/export`, CAN CSV, decoded plot CSV, session bundle.

Driven in process through `cli.main` (not the subprocess wrapper) because every case here
is about one refusal or one file's bytes, and the exit-code contract is the same either
way. The refusals that must not reach the network are driven against an unreachable URL:
exit 1 there proves the CLI judged the request itself, where exit 3 would prove it did not.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import time
import zipfile

import pytest

from mcuscope import cli
from tests.support import Stack
from tests.test_plot_export_decode import DEF, feed, sample

UNREACHABLE = ["--url", "http://127.0.0.1:1"]


def run(stack: Stack | None, *args: str) -> int:
    url = ["--url", stack.base_url] if stack is not None else UNREACHABLE
    return cli.main([*args, *url])


def iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).isoformat(timespec="milliseconds")


def add_lines(stack: Stack, *raws: str, gap_s: float = 0.0) -> list[float]:
    """Store `raws` as debug lines on the daemon's loop; returns their timestamps."""
    import asyncio

    store = stack.app.state.store
    stamps: list[float] = []

    async def go() -> None:
        for raw in raws:
            ts = time.time()
            stamps.append(ts)
            await store.add_line(
                ts=ts, port=stack.alias, dir="rx", chan="debug", seq=None, raw=raw
            )
            if gap_s:
                await asyncio.sleep(gap_s)

    asyncio.run_coroutine_threadsafe(go(), stack.app.state.ports._loop).result(60)
    return stamps


# -- bounds ----------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [
    ["lines"],
    ["log", "export"],
    ["plot", "export", "--names", "vbat"],
    ["can", "dump"],
])
def test_inverted_clock_bounds_are_refused_without_a_daemon(capsys, argv) -> None:
    """Every command taking a window refuses the backwards one before any request."""
    rc = run(None, *argv, "--from", "19:00", "--to", "18:00")
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--from 19:00 is after --to 18:00" in err


@pytest.mark.parametrize("argv", [
    ["plot", "export", "--names", "vbat"],
    ["can", "dump"],
])
def test_from_and_last_ms_are_two_lower_bounds(capsys, argv) -> None:
    """Both become `last_ms` on these endpoints, so the second would silently win."""
    rc = run(None, *argv, "--from", "19:00", "--last-ms", "5000")
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "both lower bounds" in err


def test_to_in_the_past_excludes_newer_rows(stack: Stack, tmp_path, capsys) -> None:
    stamps = add_lines(stack, "bound-a", "bound-b", gap_s=0.05)
    out = tmp_path / "run.txt"
    rc = run(stack, "log", "export", "--match", "^bound-",
             "--to", iso((stamps[0] + stamps[1]) / 2), "-o", str(out))
    assert rc == 0, capsys.readouterr().err
    text = out.read_text(encoding="utf-8")
    assert "bound-a" in text and "bound-b" not in text
    assert "wrote 1 lines" in capsys.readouterr().out


def test_a_window_with_nothing_in_it_exports_an_empty_file(stack: Stack, tmp_path,
                                                           capsys) -> None:
    """An empty window is a real answer, not a refusal and not a missing file."""
    out = tmp_path / "none.txt"
    rc = run(stack, "log", "export", "--from", iso(time.time() - 20),
             "--to", iso(time.time() - 10), "--match", "^nothing-matches-this",
             "-o", str(out))
    assert rc == 0, capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == ""
    assert "wrote 0 lines" in capsys.readouterr().out


# -- log export formats -----------------------------------------------------------------


def test_log_export_csv_and_json_refuse_each_other(capsys) -> None:
    rc = run(None, "--json", "log", "export", "--csv")
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "two output formats" in err


@pytest.mark.parametrize("extra", [["--limit", "5"], ["--decode"], ["--changes"],
                                   ["--names", "vbat"]])
def test_log_export_csv_refuses_the_paged_options(capsys, extra) -> None:
    """CSV comes from /lines/export, which has neither a limit nor a decoder."""
    rc = run(None, "log", "export", "--csv", *extra)
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "does not take --limit or --decode" in err


def test_log_export_csv_round_trips_a_comma_and_a_quote(stack: Stack, tmp_path,
                                                        capsys) -> None:
    """The raw line is a captured byte string, so quoting it is the daemon's job, not luck."""
    raw = 'csvtest a,b "quoted", tail'
    add_lines(stack, raw)
    out = tmp_path / "run.csv"
    rc = run(stack, "log", "export", "--csv", "--match", "^csvtest", "-o", str(out))
    assert rc == 0, capsys.readouterr().err
    rows = list(csv.DictReader(io.StringIO(out.read_text(encoding="utf-8"))))
    assert [r["raw"] for r in rows] == [raw]
    assert "wrote 1 lines" in capsys.readouterr().out, "the header is not a captured line"


def test_log_export_json_is_jsonl_and_counts_its_rows(stack: Stack, tmp_path,
                                                      capsys) -> None:
    add_lines(stack, "jsonl-one", "jsonl-two")
    out = tmp_path / "run.jsonl"
    rc = run(stack, "--json", "log", "export", "--match", "^jsonl-", "-o", str(out))
    assert rc == 0, capsys.readouterr().err
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [r["raw"] for r in rows] == ["jsonl-one", "jsonl-two"], "oldest first"
    result = json.loads(capsys.readouterr().out)
    assert result["file"] == str(out) and result["lines"] == 2
    assert result["bytes"] == out.stat().st_size


def test_log_export_decode_still_takes_the_paged_path(stack: Stack, capsys) -> None:
    """--decode renders client-side, so its output must survive the /lines/export move."""
    feed(stack, DEF, sample(1, 1, 100, 1))
    rc = run(stack, "log", "export", "--decode", "--names", "mode")
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert "mode=ARMED" in out.out


def test_log_export_limit_keeps_the_newest(stack: Stack, capsys) -> None:
    add_lines(stack, "lim-a", "lim-b", "lim-c")
    rc = run(stack, "log", "export", "--match", "^lim-", "--limit", "1")
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert "lim-c" in out.out and "lim-a" not in out.out


# -- plot export ------------------------------------------------------------------------


def test_plot_export_changes_without_decode_is_refused_client_side(capsys) -> None:
    rc = run(None, "plot", "export", "--names", "mode", "--changes")
    err = capsys.readouterr().err
    assert rc == 1, "the daemon's own refusal, without the round trip"
    assert "changes requires decode" in err


def test_plot_export_deadband_without_changes_is_refused_client_side(capsys) -> None:
    rc = run(None, "plot", "export", "--names", "volts", "--deadband", "volts=0.05")
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "deadband requires changes" in err


def test_plot_export_decode_names_the_enum_label(stack: Stack, capsys) -> None:
    feed(stack, DEF, sample(1, 2, 100, 3))
    rc = run(stack, "plot", "export", "--names", "mode", "--decode")
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert "RUN" in out.out, "the raw 2 is what an undecoded export prints"


def test_plot_export_decode_names_bit_lanes_in_the_wide_header(stack: Stack, tmp_path,
                                                               capsys) -> None:
    feed(stack, DEF, sample(1, 0, 100, 1))
    out = tmp_path / "wide.csv"
    rc = run(stack, "plot", "export", "--names", "led,irq", "--wide", "--decode",
             "-o", str(out))
    assert rc == 0, capsys.readouterr().err
    header = out.read_text(encoding="utf-8").splitlines()[0]
    assert "io.led" in header and "io.irq" in header


def test_plot_export_deadband_drops_a_move_inside_the_band(stack: Stack, capsys) -> None:
    feed(stack, DEF, sample(1, 0, 100, 0), sample(2, 0, 101, 0), sample(3, 0, 400, 0))
    rc = run(stack, "plot", "export", "--names", "volts", "--decode", "--changes",
             "--deadband", "volts=0.05")
    out = capsys.readouterr()
    assert rc == 0, out.err
    values = [line.split(",")[-1] for line in out.out.strip().splitlines()[1:]]
    assert values == ["1.0", "4.0"], "1.01 V is inside the 0.05 V band; 4 V is not"


def test_plot_export_deadband_naming_nothing_is_the_daemons_refusal(stack: Stack,
                                                                    capsys) -> None:
    """A name the export does not carry is a typo, and the daemon says which one."""
    feed(stack, DEF, sample(1, 0, 100, 0))
    rc = run(stack, "plot", "export", "--names", "volts", "--decode", "--changes",
             "--deadband", "nosuch=1")
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "nosuch" in err


# -- can dump ---------------------------------------------------------------------------


def can_line(tick: int, can_id: int, data: str) -> str:
    return f"!can {tick} - {can_id:X} {data}"


def test_can_dump_csv_does_not_follow(capsys) -> None:
    rc = run(None, "can", "dump", "--csv", "-f")
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--csv does not follow" in err


def test_can_dump_out_file_implies_csv_and_still_refuses_to_follow(tmp_path,
                                                                   capsys) -> None:
    rc = run(None, "can", "dump", "-o", str(tmp_path / "f.csv"), "-f")
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--csv does not follow" in err


def test_can_dump_repeated_id_selects_every_one(stack: Stack, tmp_path, capsys) -> None:
    feed(stack, can_line(1, 0x201, "AA"), can_line(2, 0x202, "BB"),
         can_line(3, 0x203, "CC"))
    out = tmp_path / "frames.csv"
    rc = run(stack, "can", "dump", "-i", "201", "--id", "203", "--csv", "-o", str(out))
    assert rc == 0, capsys.readouterr().err
    rows = list(csv.DictReader(io.StringIO(out.read_text(encoding="utf-8"))))
    assert sorted(r["can_id"] for r in rows) == [str(0x201), str(0x203)]
    assert "wrote 2 frames" in capsys.readouterr().out


def test_can_dump_csv_ignores_the_n_limit(stack: Stack, capsys) -> None:
    """`-n` bounds the printed backfill; the CSV export is the whole window (SPEC 3.4)."""
    feed(stack, *[can_line(t, 0x301, "AA") for t in range(5)])
    rc = run(stack, "can", "dump", "-i", "301", "--csv", "-n", "1")
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert len(out.out.strip().splitlines()) == 6, "header plus five frames"


def test_can_dump_csv_and_json_refuse_each_other(capsys) -> None:
    rc = run(None, "--json", "can", "dump", "--csv")
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "two output formats" in err


def test_can_dump_still_prints_frames_without_csv(stack: Stack, capsys) -> None:
    feed(stack, can_line(9, 0x401, "AA"))
    rc = run(stack, "can", "dump", "-i", "401", "-n", "5")
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert "401" in out.out


# -- session bundle ----------------------------------------------------------------------


def test_session_export_bundle_refuses_a_db_name(tmp_path, capsys) -> None:
    """A zip written to run.db is the kind of file nobody can open twice."""
    rc = run(None, "session", "export", "run-1", "--bundle", "-o", str(tmp_path / "run.db"))
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--bundle writes a zip, not a .db" in err


def test_session_export_without_bundle_still_writes_the_db(stack: Stack, tmp_path,
                                                           capsys) -> None:
    assert run(stack, "session", "start", "bundle-run") == 0
    add_lines(stack, "in-the-run")
    out = tmp_path / "run.db"
    rc = run(stack, "session", "export", "bundle-run", "-o", str(out))
    assert rc == 0, capsys.readouterr().err
    assert out.read_bytes()[:15] == b"SQLite format 3"


@pytest.mark.xfail(strict=False, reason="bundle endpoint lands on another branch")
def test_session_export_bundle_writes_a_zip(stack: Stack, tmp_path, capsys) -> None:
    """The contract: a zip of the db, the lines, the plot and CAN CSVs and a manifest."""
    assert run(stack, "session", "start", "zip-run") == 0
    feed(stack, DEF, sample(1, 1, 100, 1))
    add_lines(stack, "zipped-line")
    out = tmp_path / "run.zip"
    rc = run(stack, "session", "export", "zip-run", "--bundle", "-o", str(out))
    assert rc == 0, capsys.readouterr().err
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read("manifest.json"))
    assert {"capture.db", "lines.txt", "manifest.json"} <= names
    assert manifest["session"] == "zip-run"


@pytest.mark.xfail(strict=False, reason="bundle endpoint lands on another branch")
def test_session_export_bundle_adds_the_zip_extension(stack: Stack, tmp_path,
                                                      capsys) -> None:
    assert run(stack, "session", "start", "ext-run") == 0
    out = tmp_path / "run"
    rc = run(stack, "session", "export", "ext-run", "--bundle", "-o", str(out))
    assert rc == 0, capsys.readouterr().err
    assert not out.exists() and out.with_suffix(".zip").exists()


def test_a_bundle_the_daemon_refuses_leaves_no_file(stack: Stack, tmp_path,
                                                    capsys) -> None:
    """Whether or not the endpoint exists, a failed download must not leave wreckage."""
    out = tmp_path / "missing.zip"
    rc = run(stack, "session", "export", "no-such-session", "--bundle", "-o", str(out))
    assert rc == 1, capsys.readouterr().err
    assert not out.exists()


# -- the shared stream guard --------------------------------------------------------------


def test_a_refused_export_leaves_no_partial_file(stack: Stack, tmp_path, capsys) -> None:
    """The daemon refuses the format after the file is open; the file must not survive."""
    out = tmp_path / "bad.csv"
    rc = run(stack, "plot", "export", "--names", "no-such-channel", "-o", str(out))
    assert rc == 1, capsys.readouterr().err
    assert not out.exists(), "an empty CSV reads exactly like an empty window"


def test_an_export_keeps_a_file_it_could_not_open(stack: Stack, tmp_path, capsys) -> None:
    """The removal guard is armed only after the open succeeds (R1, for the new path)."""
    target = tmp_path / "dir-in-the-way"
    target.mkdir()
    rc = run(stack, "log", "export", "--csv", "-o", str(target))
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "cannot write" in err
    assert target.is_dir(), "a path this command never opened must survive"
