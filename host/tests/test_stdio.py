"""Tests for mcuscope._stdio: null std stream repair and the crash-file backstop.

Regression coverage for the Windows silent-failure bugs: a GUI-subsystem
interpreter (pythonw.exe, which uv can pick as a tool venv's base) starts with
sys.stdout/stderr/stdin set to None, making every print() a no-op and crashing
uvicorn's colour autodetection - and when no console exists at all, the devnull
fallback must be reported distinctly, because output is then discarded and
Ctrl-C can never arrive.
"""

from __future__ import annotations

import sys

import pytest

from mcuscope import _stdio


def test_repair_is_noop_when_streams_are_present():
    assert _stdio.repair_std_streams() == ([], True)
    # And the streams are untouched.
    assert sys.stdout is not None and sys.stderr is not None


def test_repair_null_streams(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdin", None)

    repaired, console = _stdio.repair_std_streams()

    assert repaired == ["stdout", "stderr", "stdin"]
    # Off Windows there is no CONOUT$ to reattach to, so a null stream can only be
    # devnulled and the no-console outcome must be reported; on Windows CI a real
    # console exists and the repair reattaches to it.
    if sys.platform != "win32":
        assert console is False
    assert sys.stdout is not None and sys.stderr is not None and sys.stdin is not None
    # The exact call uvicorn's ColourizedFormatter makes must not raise.
    assert sys.stdout.isatty() in (True, False)
    # print() must be a real write, not a silent no-op.
    print("survives", flush=True)
    for stream in (sys.stdout, sys.stderr):
        stream.close()


def test_no_console_is_reported_not_silently_devnulled(monkeypatch):
    """The pythonw.exe case: CONOUT$ unopenable, streams land on devnull - the
    caller must be told, because nothing printed will ever be seen."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(_stdio, "_ensure_console", lambda: False)

    repaired, console = _stdio.repair_std_streams()

    assert repaired == ["stdout", "stderr"]
    if sys.platform == "win32":
        # On Windows CI a real console exists, so even with the probe forced False
        # the CONOUT$ attempt succeeds and must correct the flag back to True: a
        # wrong probe may never cause a devnull fallback when a console exists.
        assert console is True
    else:
        # Off Windows the CONOUT$ attempt must not run at all, so a null stream
        # can only land on devnull and the no-console outcome is reported.
        assert console is False
    assert sys.stdout.isatty() in (True, False)  # must still not raise
    for stream in (sys.stdout, sys.stderr):
        stream.close()


def test_console_ctrl_handler_is_windows_only():
    if sys.platform == "win32":
        assert _stdio.install_console_ctrl_handler() is True
    else:
        assert _stdio.install_console_ctrl_handler() is False
        assert _stdio.have_console() is True


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


def test_write_startup_log(monkeypatch, tmp_path):
    monkeypatch.setattr(_stdio, "_crash_dir", lambda: str(tmp_path))

    path = _stdio.write_startup_log("mcuscoped", "web UI: http://x\n")

    assert path == str(tmp_path / "mcuscoped-startup.log")
    assert "web UI" in (tmp_path / "mcuscoped-startup.log").read_text(encoding="utf-8")
