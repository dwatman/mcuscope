"""Global-option hoisting in `mcu`'s argv (`cli_argv.py`), and the `--token` value it
carries (SPEC 4)."""

from __future__ import annotations

import json

from mcuscope.cli import _split_global_opts


def test_hoist_token_equals_form() -> None:
    from mcuscope.cli import _split_global_opts

    assert _split_global_opts(["status", "--token=abc"]) == (["--token=abc"], ["status"])
    assert _split_global_opts(["status", "--token", "abc"]) == (["--token", "abc"], ["status"])


def hoist(argv: list[str]) -> list[str]:
    head, rest = _split_global_opts(argv)
    return head + rest


# -- cli argv hoisting ----------------------------------------------------------------


def test_hoisting_leaves_a_global_looking_option_value_alone() -> None:
    """`mcu lines --match -p --limit 5` meant the regex "-p", and returned wrong data.

    Hoisting ran before parsing, so it treated the value of --match as the global port
    option and consumed --limit as that option's value, leaving the regex as "5". Exit 0,
    no warning, wrong answer.
    """
    assert hoist(["lines", "--match", "-p", "--limit", "5"]) == \
        ["lines", "--match", "-p", "--limit", "5"]
    assert hoist(["wait", "--match", "ERR", "--send", "-p"]) == \
        ["wait", "--match", "ERR", "--send", "-p"]
    assert hoist(["session", "start", "--note", "--json", "run1"]) == \
        ["session", "start", "--note", "--json", "run1"]


def test_hoisting_still_moves_real_global_options() -> None:
    assert hoist(["i2c", "rd", "48", "2", "--json"]) == ["--json", "i2c", "rd", "48", "2"]
    assert hoist(["lines", "--port", "sim", "--limit", "5"]) == \
        ["--port", "sim", "lines", "--limit", "5"]
    assert hoist(["tail", "-f", "--json"]) == ["--json", "tail", "-f"]


def test_hoisting_handles_the_attached_short_form() -> None:
    """`mcu lines -psim` was a usage error while `mcu -psim lines` worked."""
    assert hoist(["lines", "-psim", "--limit", "1"]) == ["-psim", "lines", "--limit", "1"]


def test_hoisting_respects_end_of_options() -> None:
    assert hoist(["send", "--", "-p test marker"]) == ["send", "--", "-p test marker"]


def test_hoisting_resolves_the_subcommand_past_a_leading_global_value() -> None:
    """A leading `-p board` made the subcommand walk stop, disabling the guard above.

    _value_taking_opts skipped tokens starting with "-" but not the *value* that follows a
    global option, so it looked up a command named "board", gave up, and fell back to the
    root group's options. Every protection for subcommand option values was then off:
    `mcu -p board lines --match -p --limit 5` became --port=--limit with the regex "5".
    """
    assert hoist(["-p", "board", "lines", "--match", "-p", "--limit", "5"]) == \
        ["-p", "board", "lines", "--match", "-p", "--limit", "5"]
    assert hoist(["--url", "http://x", "lines", "--match", "-p"]) == \
        ["--url", "http://x", "lines", "--match", "-p"]
    assert hoist(["-p", "board", "lines", "--match", "--json"]) == \
        ["-p", "board", "lines", "--match", "--json"]
    # The global still hoists when it really is one, from after the subcommand.
    assert hoist(["-p", "board", "lines", "--limit", "5", "--json"]) == \
        ["-p", "board", "--json", "lines", "--limit", "5"]


# -- F8: hoisting tracks value consumption ---------------------------------------------


def test_hoisting_sees_a_global_after_a_value_that_looks_like_an_option() -> None:
    """`mcu lines --match --limit --json`: --limit is the regex, so --json is still global.

    The old guard compared the literal previous token, so the *value* --limit read as an
    option awaiting a value and --json stayed behind the subcommand, where click rejects
    it - and a --json consumer got exit 1 with an empty stdout.
    """
    from mcuscope.cli import _split_global_opts as split

    assert split(["lines", "--match", "--limit", "--json"]) == \
        (["--json"], ["lines", "--match", "--limit"])
    # The value's own value is still not hoisted: --limit here is data, not an option.
    assert split(["lines", "--match", "--limit", "5"]) == ([], ["lines", "--match", "--limit", "5"])


# -- RG-F6: a token that cannot go on the wire is a refusal, not a traceback -------------


def test_non_ascii_token_is_refused_as_bad_usage(monkeypatch, capsys) -> None:
    from mcuscope import cli

    rc = cli.main(["--token", "tökén", "status", "--url", "http://127.0.0.1:1"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "token must be ASCII" in err


def test_non_ascii_token_from_the_environment_is_refused(monkeypatch, capsys) -> None:
    from mcuscope import cli

    monkeypatch.setenv("MCUSCOPE_TOKEN", "tökén")
    rc = cli.main(["--json", "status", "--url", "http://127.0.0.1:1"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "token must be ASCII" in err
    assert json.loads(out)["exit_code"] == 1
