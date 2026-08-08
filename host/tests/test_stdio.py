"""Tests for mcuscope._stdio: null std stream repair and the crash-file backstop.

Regression coverage for the Windows silent-failure bugs: a GUI-subsystem
interpreter (pythonw.exe, which uv can pick as a tool venv's base) starts with
sys.stdout/stderr/stdin set to None, making every print() a no-op and crashing
uvicorn's colour autodetection - and when no console exists at all, the devnull
fallback must be reported distinctly, because output is then discarded and
Ctrl-C can never arrive.
"""

from __future__ import annotations

import io
import os
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


def test_widen_stdout_encoding_replaces_a_narrow_codec(monkeypatch):
    """Redirected to a pipe or file, Windows gives stdout the ANSI code page with
    errors="strict"; attached to a console it writes through the console API and is
    already safe. Simulated with cp1252 so it is exercised on any platform."""
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252",
                                                        errors="strict"))

    _stdio.widen_stdout_encoding()

    assert sys.stdout.encoding.lower().replace("-", "") == "utf8"
    assert sys.stdout.errors == "replace"
    print("十")  # a value a TOML config may legitimately contain
    sys.stdout.flush()
    assert "十".encode() in raw.getvalue()


def test_console_entry_widens_a_redirected_stdout(monkeypatch, tmp_path):
    """`mcuscoped > startup.log`: config.py quotes the offending TOML value verbatim in
    its error, so a non-ASCII value turned the daemon's own diagnostic into a
    UnicodeEncodeError. The widening belongs in the shared entry point, not in one CLI."""
    monkeypatch.setattr(_stdio, "_crash_dir", lambda: str(tmp_path))
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252",
                                                        errors="strict"))

    def main() -> int:
        print("mcuscoped: config.toml: invalid value: invalid literal for int(): '十'")
        return 0

    assert _stdio.console_entry(main, "mcuscoped") == 0
    sys.stdout.flush()
    assert not (tmp_path / "mcuscoped-crash.log").exists()
    assert "十".encode() in raw.getvalue()


def test_write_startup_log(monkeypatch, tmp_path):
    monkeypatch.setattr(_stdio, "_crash_dir", lambda: str(tmp_path))

    path = _stdio.write_startup_log("mcuscoped", "web UI: http://x\n")

    assert path == str(tmp_path / "mcuscoped-startup.log")
    assert "web UI" in (tmp_path / "mcuscoped-startup.log").read_text(encoding="utf-8")


def test_report_key_is_per_daemon(monkeypatch, tmp_path):
    """Two daemons must not share one startup log, nor one crash log.

    The same keying defect the pid record was fixed for, left in place for the artifact
    that exists *because* a windowless start leaves no other trace: with a single shared
    path, the second daemon's log named only its own port and told the user to kill that
    pid, while the first was still running with its trace overwritten.
    """
    monkeypatch.setattr(_stdio, "_crash_dir", lambda: str(tmp_path))
    monkeypatch.setattr(_stdio, "_report_key", "")

    written = []
    for port in (8794, 8795):
        _stdio.set_report_key(f"127.0.0.1-{port}")
        written.append(_stdio.write_startup_log("mcuscoped", f"pid for port {port}\n"))
        with pytest.raises(ValueError):
            _stdio.console_entry(_explode, "mcuscoped")

    assert written == [
        str(tmp_path / "mcuscoped-127.0.0.1-8794-startup.log"),
        str(tmp_path / "mcuscoped-127.0.0.1-8795-startup.log"),
    ]
    for port in (8794, 8795):
        base = tmp_path / f"mcuscoped-127.0.0.1-{port}"
        assert f"port {port}" in base.with_name(
            base.name + "-startup.log").read_text(encoding="utf-8")
        assert base.with_name(base.name + "-crash.log").exists()
    # An IPv6 literal keys a file too: no colon reaches the filename.
    _stdio.set_report_key("::1-8796")
    path = _stdio.write_startup_log("mcuscoped", "v6\n")
    assert path is not None and ":" not in os.path.basename(path)


def test_a_crash_is_still_raised_when_the_crash_log_cannot_be_written(monkeypatch, tmp_path):
    """An unwritable data dir is the one path that could make a failure truly invisible.

    The report is given up on rather than raising an OSError of its own over the top of
    the real one, and the original exception still leaves console_entry.
    """
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8", newline="")   # a file where a directory must go
    monkeypatch.setattr(_stdio, "_crash_dir", lambda: str(blocker / "mcuscope"))

    assert _stdio.write_startup_log("mcuscoped", "invisible\n") is None
    with pytest.raises(ValueError):
        _stdio.console_entry(_explode, "mcuscoped")


def _explode() -> int:
    raise ValueError("startup exploded")
