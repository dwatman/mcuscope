"""Tests for mcuscope._stdio: null std stream repair and the crash-file backstop.

Regression coverage for the Windows silent-exit bug: an interpreter starting with
sys.stdout/stderr/stdin set to None (KiCad's bundled Python does this under a real
console) made every print() a no-op and crashed uvicorn's colour autodetection,
so mcuscoped exited 1 with no output anywhere.
"""

from __future__ import annotations

import sys

import pytest

from mcuscope import _stdio


def test_repair_is_noop_when_streams_are_present():
    assert _stdio.repair_std_streams() == []
    # And the streams are untouched.
    assert sys.stdout is not None and sys.stderr is not None


def test_repair_null_streams(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdin", None)

    repaired = _stdio.repair_std_streams()

    assert repaired == ["stdout", "stderr", "stdin"]
    assert sys.stdout is not None and sys.stderr is not None and sys.stdin is not None
    # The exact call uvicorn's ColourizedFormatter makes must not raise.
    assert sys.stdout.isatty() in (True, False)
    # print() must be a real write, not a silent no-op.
    print("survives", flush=True)
    for stream in (sys.stdout, sys.stderr):
        stream.close()


def test_interpreter_report_names_the_interpreter():
    report = _stdio.interpreter_report()
    assert sys.executable in report
    assert sys.version.split()[0] in report
    assert sys.version.split()[0] in _stdio.python_line()


def test_console_entry_passes_through_return_value():
    assert _stdio.console_entry(lambda: 3, "prog") == 3


def test_console_entry_writes_crash_log(monkeypatch, tmp_path):
    monkeypatch.setattr(_stdio, "_crash_dir", lambda: str(tmp_path))

    def boom() -> int:
        raise ValueError("startup exploded")

    with pytest.raises(ValueError):
        _stdio.console_entry(boom, "prog")

    crash = tmp_path / "prog-crash.log"
    text = crash.read_text(encoding="utf-8")
    assert "startup exploded" in text
    assert sys.executable in text


def test_console_entry_does_not_log_normal_exits(monkeypatch, tmp_path):
    monkeypatch.setattr(_stdio, "_crash_dir", lambda: str(tmp_path))

    def interrupted() -> int:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _stdio.console_entry(interrupted, "prog")

    assert not (tmp_path / "prog-crash.log").exists()
