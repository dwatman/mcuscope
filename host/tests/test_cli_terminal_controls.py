"""Control bytes a board sends never act on the operator's terminal (SPEC 4).

On a terminal, human output keeps SGR colour and shows every other control byte escaped;
--json, pipes and files carry the bytes as captured.
"""

from __future__ import annotations

import io

from mcuscope import cli_output
from mcuscope.cli_output import _GuardedStdout, visible

OSC52 = "\x1b]52;c;ZWNobyBwd25lZA==\x07"
ERASE = "\x1b[1A\x1b[2K"
SGR = "\x1b[31mred\x1b[0m"


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_visible_keeps_colour_and_escapes_every_other_control() -> None:
    out = visible(f"a{OSC52}b{ERASE}c{SGR}\td\x00\x7f\x9b")
    assert SGR in out and "\t" in out
    for raw in ("\x1b]", "\x07", "\x1b[1A", "\x1b[2K", "\x00", "\x7f", "\x9b"):
        assert raw not in out, repr(raw)
    assert "\\x1b]52;c;" in out and "\\x07" in out and "\\x1b[2K" in out
    assert "\\x00" in out and "\\x7f" in out and "\\x9b" in out


def test_a_terminal_stdout_gets_escaped_text(monkeypatch) -> None:
    monkeypatch.setattr(cli_output, "_JSON_MODE", False)
    tty = _Tty()
    _GuardedStdout(tty).write(f"row {OSC52}{SGR}\n")
    assert tty.getvalue() == f"row \\x1b]52;c;ZWNobyBwd25lZA==\\x07{SGR}\n"


def test_a_pipe_and_json_mode_stay_faithful(monkeypatch) -> None:
    """The negative control for the two exemptions: same bytes, no terminal or --json."""
    monkeypatch.setattr(cli_output, "_JSON_MODE", False)
    pipe = io.StringIO()
    _GuardedStdout(pipe).write(f"row {OSC52}\n")
    assert pipe.getvalue() == f"row {OSC52}\n"
    monkeypatch.setattr(cli_output, "_JSON_MODE", True)
    tty = _Tty()
    _GuardedStdout(tty).write(f"row {OSC52}\n")
    assert tty.getvalue() == f"row {OSC52}\n"


def test_a_terminal_stderr_gets_escaped_text(monkeypatch) -> None:
    """assert's FAILED lines quote device text on stderr."""
    tty = _Tty()
    monkeypatch.setattr("sys.stderr", tty)
    cli_output.err(f"  FAILED  forbid 'x': {OSC52}")
    assert "\x07" not in tty.getvalue() and "\\x07" in tty.getvalue()
    pipe = io.StringIO()
    monkeypatch.setattr("sys.stderr", pipe)
    cli_output.err(f"x {OSC52}")
    assert OSC52 in pipe.getvalue()
