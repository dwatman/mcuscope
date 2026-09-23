"""A POSIX stdout closed at start gets one cause, not a "no console" warning beside it."""

from __future__ import annotations

from mcuscope import cli, cli_output
from tests import conftest
from tests.test_cli_closed_stdio import _run, posix_only


@posix_only
def test_a_closed_stdout_is_reported_once_with_its_real_cause() -> None:
    r = _run(">&-", "ai-guide")
    assert r.returncode == 1
    assert "closed when mcu started" in r.stderr
    assert "WARNING" not in r.stderr and "no console" not in r.stderr


@posix_only
def test_another_closed_stream_still_gets_the_repair_warning() -> None:
    """Positive control: the warning still reaches stderr for a stream mcu does not report."""
    r = _run("<&-", "ai-guide")
    assert r.returncode == 0, r.stderr
    assert "WARNING: this interpreter started with stdin set to None" in r.stderr


def test_the_isolation_fixture_clears_a_json_mode_main_left_set(monkeypatch, capsys) -> None:
    """main() resets the mode per call, but leaves it set on return; a later test that calls
    internals directly must not inherit it. Drives the fixture itself, so no test order is
    needed to see the leak."""
    assert cli.main(["--json", "ai-guide"]) == 0
    capsys.readouterr()
    assert cli_output.json_mode() is True          # the leak the fixture exists for
    conftest._isolate_output_state.__wrapped__(monkeypatch)
    assert cli_output.json_mode() is False
