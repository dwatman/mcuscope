"""Which config file `mcuscoped` runs on: `--config`, MCUSCOPED_CONFIG, the default, and
a named file that is missing (SPEC 3.3)."""

from __future__ import annotations

import os

import pytest

from mcuscope import daemon as daemon_mod
from mcuscope import pidfile
from tests.support import free_port

# -- a named config must exist -------------------------------------------------------------


@pytest.fixture
def run_main(tmp_path, monkeypatch):
    """Runs `daemon.main` without serving, reporting what each run reached.

    `served` and `claims` are cleared per run: a list that only ever grows makes the
    second run of a test a constant, which is what `bool(served)` used to be. The claim
    spy is the observation point for "refused before the pid claim": the record itself
    is released in main()'s finally, so the data dir is empty on every path.
    """
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    monkeypatch.setattr("platformdirs.user_config_dir", lambda app: str(tmp_path / "cfgdir"))
    monkeypatch.delenv("MCUSCOPED_CONFIG", raising=False)
    served: list = []
    claims: list[str | None] = []
    monkeypatch.setattr(daemon_mod, "_serve", lambda app, **kw: served.append(app))
    real_claim = pidfile.claim

    def spy_claim(host: str, port: int) -> str | None:
        path = real_claim(host, port)
        claims.append(path)
        return path

    monkeypatch.setattr(pidfile, "claim", spy_claim)

    def run(*argv: str) -> tuple[int | None, bool]:
        served.clear()
        claims.clear()
        return daemon_mod.main([*argv, "--port", str(free_port())]), bool(served)

    run.claims = claims
    return run


def test_a_relative_named_config_is_reported_absolute(tmp_path, run_main, monkeypatch) -> None:
    """`mcu daemon restart` from another directory checks the file this daemon runs on."""
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "rel.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    given: list = []
    real_create_app = daemon_mod.create_app

    def spy_create_app(*args, **kwargs):
        given.append(kwargs.get("config_path"))   # what /status reports (server.py)
        return real_create_app(*args, **kwargs)

    monkeypatch.setattr(daemon_mod, "create_app", spy_create_app)
    assert run_main("-c", "etc/rel.toml") == (0, True)
    assert given == [str(tmp_path / "etc" / "rel.toml")], given


def test_a_named_config_that_does_not_exist_is_refused(tmp_path, run_main, capsys) -> None:
    missing = tmp_path / "typo.toml"
    assert run_main("-c", str(missing)) == (1, False)
    cap = capsys.readouterr()
    assert cap.err == f"mcuscoped: no such config file: {missing}\n"
    assert cap.out == "", "refused before the files notice"
    assert not missing.exists(), "the refusal must not create the file"
    assert run_main.claims == [], "refused before the pid claim"


def test_a_start_that_gets_past_the_config_does_claim(tmp_path, run_main) -> None:
    """Positive control for the claim spy above: the served path reaches the claim."""
    present = tmp_path / "present.toml"
    present.write_bytes(b"")
    assert run_main("-c", str(present)) == (0, True)
    assert len(run_main.claims) == 1 and run_main.claims[0], run_main.claims
    assert run_main.claims[0].endswith(".pid")
    # Claimed, then released by main()'s finally: the data dir cannot tell the two paths
    # apart, which is why the refusal above is asserted on the spy and not on a glob.
    assert not list((tmp_path / "data").glob("*.pid"))


def test_the_env_variable_names_a_config_too(tmp_path, run_main, monkeypatch, capsys) -> None:
    missing = tmp_path / "env-typo.toml"
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(missing))
    assert run_main() == (1, False)
    assert capsys.readouterr().err == f"mcuscoped: no such config file: {missing}\n"


def test_the_flag_wins_over_the_variable_both_ways(tmp_path, run_main, monkeypatch, capsys):
    present = tmp_path / "present.toml"
    present.write_bytes(b"")
    missing = tmp_path / "missing.toml"
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(missing))
    assert run_main("-c", str(present)) == (0, True)
    capsys.readouterr()
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(present))
    assert run_main("-c", str(missing)) == (1, False)  # the flag refuses; nothing served
    assert capsys.readouterr().err == f"mcuscoped: no such config file: {missing}\n"


def test_a_missing_default_config_still_means_defaults(tmp_path, run_main, capsys) -> None:
    default = tmp_path / "cfgdir" / "config.toml"
    assert run_main() == (0, True)
    assert f"config: {default} not found, using defaults\n" in capsys.readouterr().out
    assert not default.exists()


def test_an_empty_variable_means_the_default(tmp_path, run_main, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MCUSCOPED_CONFIG", "")
    assert run_main() == (0, True)


def test_a_named_directory_is_unreadable_not_missing(tmp_path, run_main, capsys) -> None:
    d = tmp_path / "adir.toml"
    d.mkdir()
    assert run_main("-c", str(d)) == (1, False)
    err = capsys.readouterr().err
    assert "cannot read" in err and "no such config file" not in err, err


def test_a_dangling_symlink_is_missing(tmp_path, run_main, capsys) -> None:
    link = tmp_path / "link.toml"
    try:
        os.symlink(tmp_path / "gone.toml", link)
    except (OSError, NotImplementedError):
        pytest.skip("no symlink support")
    assert run_main("-c", str(link)) == (1, False)
    assert capsys.readouterr().err == f"mcuscoped: no such config file: {link}\n"
