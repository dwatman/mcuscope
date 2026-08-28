"""Review round 2, batch C2: config loading, the capture lock's holder record, the
atomic writers' temp names, and mcuscoped's startup refusals.

Each test drives a refusal or a corrupt input, not a happy path: these are the paths that
only run once something has already gone wrong, which is where they were all broken.
"""

from __future__ import annotations

import json
import os
import sys
import threading

import pytest

from mcuscope import config as config_mod
from mcuscope import daemon as daemon_mod
from mcuscope import lockfile as lockfile_mod
from mcuscope.config import ConfigError, load_config
from mcuscope.lockfile import CaptureLock, LockError

# -- F4: an unreadable config is a startup failure, not a traceback ---------------------


def test_a_config_path_that_is_a_directory_names_the_file(tmp_path) -> None:
    """`exists()` then `read_text()`: a directory (a typo'd MCUSCOPED_CONFIG, or the path
    of a config dir) raised IsADirectoryError straight out of load_config."""
    d = tmp_path / "config.toml"
    d.mkdir()
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(str(d))
    assert str(d) in str(pytest.raises(ConfigError, load_config, str(d)).value)


@pytest.mark.skipif(sys.platform == "win32", reason="chmod 000 does not deny reads on Windows")
def test_an_unreadable_config_file_names_the_file(tmp_path) -> None:
    """A config owned by root on a shared bench: PermissionError, not a refusal."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("[server]\nport = 9000\n", encoding="utf-8", newline="\n")
    cfg.chmod(0o000)
    if os.access(cfg, os.R_OK):   # running as root: the mode is not enforced
        pytest.skip("root reads a 000 file, so the OSError cannot be provoked this way")
    try:
        with pytest.raises(ConfigError, match="cannot read"):
            load_config(str(cfg))
    finally:
        cfg.chmod(0o600)


# -- F5: a corrupt holder record must not replace the refusal with a traceback ----------


def test_a_corrupt_started_still_refuses_the_second_daemon(tmp_path) -> None:
    """1e300 out of a hand-edited .lock raised OverflowError inside LockError.__init__,
    inside CaptureLock.acquire's except arm, so daemon.main never saw a LockError at all."""
    db = str(tmp_path / "capture.db")
    held = CaptureLock(db)
    held.acquire()
    try:
        # Rewrite the holder metadata the way a hand edit (or a truncated write) would.
        record = json.dumps({"pid": 4242, "host": "bench", "started": 1e300, "db": db})
        with open(held.path, "r+b") as fh:
            fh.seek(1)
            fh.write(record.encode("utf-8"))
            fh.truncate()
        with pytest.raises(LockError) as exc:
            CaptureLock(db).acquire(timeout=0)
    finally:
        held.release()
    msg = str(exc.value)
    assert "already in use" in msg and "--ignore-capture-lock" in msg
    assert "an unknown time" in msg, "an unusable timestamp must degrade, not be invented"
    assert "pid 4242" in msg, "the rest of the record is still worth reporting"


@pytest.mark.parametrize(
    "since", [1e300, -1e300, float("nan"), float("inf"), "yesterday", None, True, -1]
)
def test_every_unusable_started_formats_as_unknown(since) -> None:
    assert lockfile_mod._format_started(since) == "an unknown time"


def test_a_real_started_is_still_formatted() -> None:
    assert lockfile_mod._format_started(1_700_000_000).startswith("20")


# -- F10: the atomic writers use per-process temp names --------------------------------


def test_the_config_writer_does_not_use_a_fixed_temp_sibling(tmp_path, monkeypatch) -> None:
    """Two daemons on one --config file both wrote <config>.toml.tmp, so one replaced the
    other's half-written bytes."""
    cfg = tmp_path / "config.toml"
    seen: list[str] = []
    real_replace = config_mod.replace_atomic
    monkeypatch.setattr(
        config_mod, "replace_atomic",
        lambda src, dst, **kw: (seen.append(os.path.basename(str(src))), real_replace(src, dst))[1],
    )
    config_mod.save_update(cfg, check=False)
    assert seen and seen[0] != "config.toml.tmp", "the temp name is still shared per user"
    assert str(os.getpid()) in seen[0]
    assert "[update]" in cfg.read_text(encoding="utf-8"), "the atomic replace did not land"
    assert list(tmp_path.glob("*.tmp")) == [], "the temp file outlived the write"


def test_the_update_cache_writer_does_not_use_a_fixed_temp_sibling(tmp_path, monkeypatch) -> None:
    """Two daemons for one user share user_cache_dir, so both wrote update.json.tmp."""
    from mcuscope import update_check as uc

    cache = tmp_path / "update.json"
    seen: list[str] = []
    real_replace = uc.replace_atomic
    monkeypatch.setattr(
        uc, "replace_atomic",
        lambda src, dst, **kw: (seen.append(os.path.basename(str(src))),
                                real_replace(src, dst))[1],
    )
    checker = uc.UpdateChecker(enabled=True, path=cache)
    checker.latest = "9.9.9"
    checker.checked_at = 1_700_000_000.0
    checker._save_cache()
    assert seen and seen[0] != "update.json.tmp", "the temp name is still shared per user"
    assert str(os.getpid()) in seen[0]
    assert json.loads(cache.read_text(encoding="utf-8"))["latest"] == "9.9.9"
    assert list(tmp_path.glob("*.tmp")) == [], "the temp file outlived the write"


# -- F11: startup refusals go to stderr ------------------------------------------------


def test_a_config_refusal_prints_to_stderr(tmp_path, capsys) -> None:
    """`mcuscoped >/dev/null` (a wrapper script, a unit file) discarded every refusal,
    leaving a bare exit 1."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[server]\nport = "abc"\n', encoding="utf-8", newline="\n")
    assert daemon_mod.main(["-c", str(cfg)]) == 1
    cap = capsys.readouterr()
    assert "mcuscoped:" in cap.err and "port" in cap.err
    assert "mcuscoped:" not in cap.out


def test_a_port_conflict_prints_to_stderr(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    monkeypatch.setattr(daemon_mod, "_port_conflict", lambda host, port: "127.0.0.1:1 is in use")
    assert daemon_mod.main(["-c", str(tmp_path / "absent.toml"), "--port", "1"]) == 1
    cap = capsys.readouterr()
    assert "is in use" in cap.err
    assert "is in use" not in cap.out


# -- F14: the three one-liners ---------------------------------------------------------


def test_the_start_timeout_is_read_per_call(monkeypatch) -> None:
    """MCUSCOPE_START_TIMEOUT was read once at import, so it could not be varied between
    two runs in one interpreter - which is exactly how an environment variable is used."""
    from mcuscope import cli_daemonctl

    assert callable(cli_daemonctl.DAEMON_START_TIMEOUT_S), \
        "an import-time float freezes the environment variable"
    monkeypatch.setenv("MCUSCOPE_START_TIMEOUT", "7.5")
    assert cli_daemonctl.DAEMON_START_TIMEOUT_S() == 7.5
    monkeypatch.setenv("MCUSCOPE_START_TIMEOUT", "31")
    assert cli_daemonctl.DAEMON_START_TIMEOUT_S() == 31.0


def test_an_empty_host_override_is_refused(tmp_path, monkeypatch, capsys) -> None:
    """`if args.host:` swallowed `--host ""`, while `--port 0` is refused: same flag pair,
    two rules."""
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    # The refusal has to happen before anything binds: without it the daemon started on the
    # configured host instead, which is the silent half of the defect.
    monkeypatch.setattr(
        daemon_mod.uvicorn, "run",
        lambda *a, **kw: pytest.fail("an empty --host was taken as no override"),
    )
    for bad in ("", "   "):
        assert daemon_mod.main(["-c", str(tmp_path / "absent.toml"), "--host", bad]) == 1
        assert "--host must be" in capsys.readouterr().err


def test_signal_registration_off_the_main_thread_is_survivable(capsys, monkeypatch) -> None:
    """signal.signal raises ValueError off the main thread, between the pid claim and the
    try that owns the release: an embedder calling main() lost the record."""
    import signal

    # A previous test in the same process may have left a handler installed, which would
    # skip the registration this test exists to drive.
    monkeypatch.setattr(signal, "getsignal", lambda sig: signal.SIG_DFL)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            daemon_mod._release_pid_on_terminating_signal(None)
        except BaseException as exc:   # noqa: BLE001 - the point of the test
            errors.append(exc)

    t = threading.Thread(target=run)
    t.start()
    t.join()
    assert errors == [], f"signal registration escaped off the main thread: {errors}"
    assert "main thread" in capsys.readouterr().err


# -- RG-F17: the loader's baud ceiling matches the API's --------------------------------


def test_a_baud_the_api_refuses_is_not_loaded(tmp_path, caplog) -> None:
    """The loader took baud=999999999 while ConfigPortEntry refuses it, so the settings
    dialog's ports save 422'd on an entry the daemon had started with."""
    import logging

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[[ports]]\nalias = "fast"\ndevice = "COM7"\nbaud = 999999999\n'
        '[[ports]]\nalias = "good"\ndevice = "COM8"\nbaud = 9600\n',
        encoding="utf-8", newline="\n",
    )
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        ports = load_config(str(cfg)).ports
    assert [p.alias for p in ports] == ["good"], "the unsaveable port was loaded anyway"
    assert any("skipping" in r.message and "fast" in r.message for r in caplog.records)
