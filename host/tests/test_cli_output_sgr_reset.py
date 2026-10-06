"""A colour a row keeps on a terminal cannot outlive the row (SPEC 4 text output)."""

from __future__ import annotations

import io

from mcuscope import cli_output
from mcuscope.cli_output import visible

RED = "\x1b[31m"
RESET = "\x1b[0m"


def test_a_kept_sgr_is_reset_before_its_line_feed() -> None:
    assert visible(f"{RED}hot\nnext") == f"{RED}hot{RESET}\nnext"


def test_a_kept_sgr_is_reset_at_the_end_of_a_write() -> None:
    assert visible(f"a\n{RED}hot") == f"a\n{RED}hot{RESET}"


def test_a_row_that_resets_itself_gets_no_second_reset() -> None:
    assert visible(f"{RED}hot{RESET}\n") == f"{RED}hot{RESET}\n"
    assert visible(f"{RED}hot\x1b[m") == f"{RED}hot\x1b[m"


def test_a_text_ending_in_a_line_feed_gets_one_reset_not_two() -> None:
    assert visible(f"{RED}hot\n") == f"{RED}hot{RESET}\n"


def test_an_sgr_after_the_rows_own_reset_still_gets_one() -> None:
    # The device's own reset may be followed by another SGR on the same row.
    assert visible(f"{RED}a{RESET}{RED}b") == f"{RED}a{RESET}{RED}b{RESET}"


def test_text_with_no_sgr_is_left_alone() -> None:
    # Positive control for the two above: no SGR, no reset added, controls still escaped.
    assert visible("plain\nrow\x07") == "plain\nrow\\x07"


def test_an_escaped_control_is_not_an_sgr_to_reset() -> None:
    # A cursor move is shown escaped, so nothing styled the terminal and nothing is reset.
    assert visible("\x1b[2J") == "\\x1b[2J"


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_a_printed_row_on_a_terminal_ends_unstyled(monkeypatch) -> None:
    """print() writes the row and its newline separately; each write is closed off."""
    tty = _Tty()
    monkeypatch.setattr(cli_output, "_JSON_MODE", False)
    out = cli_output._GuardedStdout(tty)
    print(f"12:00:00.000  debug| {RED}FAULT", file=out)
    assert tty.getvalue() == f"12:00:00.000  debug| {RED}FAULT{RESET}\n"


def test_stderr_on_a_terminal_is_reset_too(monkeypatch) -> None:
    tty = _Tty()
    monkeypatch.setattr("sys.stderr", tty)
    cli_output.err(f"error: {RED}bad")
    assert tty.getvalue() == f"error: {RED}bad{RESET}\n"
