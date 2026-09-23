"""Owner ruling 2026-09-15: `mcu daemon start`/`restart` refuse a named config that does not
exist (exit 1, `no such config file: <path>`), before anything is probed, stopped or spawned;
a missing default config still means defaults."""

from __future__ import annotations

import json

import pytest

from mcuscope import cli
from mcuscope.config import default_config_path

DEAD = "http://127.0.0.1:1"


class _Daemon:
    spawned: list[_Daemon] = []

    def __init__(self, args, **kwargs):
        self.pid, self.args = 4242, args
        _Daemon.spawned.append(self)

    def poll(self):
        return None


@pytest.fixture
def spawn(monkeypatch, tmp_path):
    """Fake Popen and /status; records probes and stops. Nothing answers until spawned."""
    log: dict[str, list] = {"probes": [], "stops": []}
    pid_path = tmp_path / "mcuscoped-127.0.0.1-1.pid"
    monkeypatch.setattr(cli, "_pid_file", lambda s: str(pid_path))
    monkeypatch.setattr(_Daemon, "spawned", [])
    monkeypatch.setattr(cli.subprocess, "Popen", _Daemon)
    log["running"] = None      # a /status body answered before the spawn, for restart

    def stop(s, restarting=False):
        log["stops"].append(s)
        log["running"] = None

    monkeypatch.setattr(cli, "_stop_daemon", stop)
    monkeypatch.setattr(cli.Client, "probe", lambda self, m, path: {"ports": []})
    monkeypatch.delenv("MCUSCOPED_CONFIG", raising=False)

    def status_body(s, timeout=2.0):
        log["probes"].append(1)
        if _Daemon.spawned:
            return {"version": "0", "uptime_s": 0.0, "ports": [], "pid": 4242}
        return log["running"]

    monkeypatch.setattr(cli, "_status_body", status_body)
    monkeypatch.setattr(cli, "_status_or_refusal",
                        lambda s, timeout=2.0: (status_body(s, timeout), None))
    log["pid_path"] = pid_path
    return log


def _refused(capsys, argv, path) -> None:
    rc = cli.main([*argv, "--url", DEAD])
    out, err = capsys.readouterr()
    assert rc == 1, err
    assert f"no such config file: {path}" in err, err
    assert _Daemon.spawned == [], "refused before the spawn"


def _config_arg() -> str | None:
    args = _Daemon.spawned[-1].args
    return args[args.index("--config") + 1] if "--config" in args else None


def test_a_missing_relative_config_is_refused_as_its_resolved_path(spawn, tmp_path, monkeypatch,
                                                                   capsys) -> None:
    monkeypatch.chdir(tmp_path)
    _refused(capsys, ["daemon", "start", "-c", "typo.toml"], tmp_path / "typo.toml")
    assert spawn["probes"] == [], "refused before even probing for a running daemon"
    assert not spawn["pid_path"].exists()


def test_a_missing_config_under_home_is_refused_expanded(spawn, tmp_path, monkeypatch,
                                                         capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    _refused(capsys, ["daemon", "start", "-c", "~/typo.toml"], tmp_path / "typo.toml")


def test_a_refusal_in_json_mode_is_the_one_json_object(spawn, tmp_path, capsys) -> None:
    missing = tmp_path / "typo.toml"
    rc = cli.main(["--json", "daemon", "start", "-c", str(missing), "--url", DEAD])
    out, err = capsys.readouterr()
    assert rc == 1
    assert json.loads(out) == {"error": f"no such config file: {missing}", "exit_code": 1}


def test_a_directory_is_not_a_config_file(spawn, tmp_path, capsys) -> None:
    _refused(capsys, ["daemon", "start", "-c", str(tmp_path)], tmp_path)


def test_a_missing_file_named_by_the_env_var_is_refused(spawn, tmp_path, monkeypatch,
                                                        capsys) -> None:
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(tmp_path / "env-typo.toml"))
    _refused(capsys, ["daemon", "start"], tmp_path / "env-typo.toml")


def test_the_flag_wins_over_a_missing_env_file(spawn, tmp_path, monkeypatch, capsys) -> None:
    good = tmp_path / "good.toml"
    good.write_text("", encoding="utf-8")
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(tmp_path / "env-typo.toml"))
    assert cli.main(["daemon", "start", "-c", str(good), "--url", DEAD]) == 0, \
        capsys.readouterr().err
    assert _config_arg() == str(good)


def test_an_existing_relative_or_home_config_is_forwarded_resolved(spawn, tmp_path,
                                                                   monkeypatch, capsys) -> None:
    """The daemon does not expand `~`, and a restart from another directory re-reads
    config_path: the CLI forwards the path it checked."""
    (tmp_path / "rel.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["daemon", "start", "-c", "rel.toml", "--url", DEAD]) == 0
    assert _config_arg() == str(tmp_path / "rel.toml")

    _Daemon.spawned.clear()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("MCUSCOPED_CONFIG", "~/rel.toml")
    assert cli.main(["daemon", "start", "--url", DEAD]) == 0, capsys.readouterr().err
    assert _config_arg() == str(tmp_path / "rel.toml"), "the env file, forwarded expanded"


def test_a_missing_default_config_still_starts_on_defaults(spawn, capsys) -> None:
    assert not default_config_path().exists()
    assert cli.main(["daemon", "start", "--url", DEAD]) == 0, capsys.readouterr().err
    assert _config_arg() is None
    assert "no such config file" not in capsys.readouterr().err


def test_restart_with_a_missing_config_refuses_before_stopping(spawn, tmp_path, capsys) -> None:
    spawn["running"] = {"version": "0", "uptime_s": 0.0, "ports": [], "pid": 1}
    _refused(capsys, ["daemon", "restart", "-c", str(tmp_path / "typo.toml")],
             tmp_path / "typo.toml")
    assert spawn["stops"] == [], "the running daemon was stopped for a start that cannot run"


def test_restart_refuses_when_the_running_daemons_config_is_gone(spawn, tmp_path,
                                                                capsys) -> None:
    gone = tmp_path / "deleted.toml"
    spawn["running"] = {"version": "0", "uptime_s": 0.0, "ports": [], "pid": 1,
                        "config_path": str(gone)}
    _refused(capsys, ["daemon", "restart"], gone)
    assert spawn["stops"] == []


def test_restart_of_a_daemon_on_the_missing_default_config_is_not_refused(spawn,
                                                                         capsys) -> None:
    """A daemon started without a config reports the default path as config_path; carrying
    it as --config turned "missing default means defaults" into a refusal."""
    assert not default_config_path().exists()
    spawn["running"] = {"version": "0", "uptime_s": 0.0, "ports": [], "pid": 1,
                        "config_path": str(default_config_path())}
    rc = cli.main(["daemon", "restart", "--url", DEAD])
    err = capsys.readouterr().err
    assert rc == 0, err
    assert "no such config file" not in err
    assert len(spawn["stops"]) == 1
    assert _config_arg() is None


def test_the_ai_guide_and_help_do_not_promise_a_warning() -> None:
    assert "warns if missing" not in cli.AI_GUIDE
    assert "no such config file" in cli.AI_GUIDE
