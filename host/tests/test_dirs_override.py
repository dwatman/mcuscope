"""MCUSCOPE_DATA_DIR, MCUSCOPE_CONFIG_DIR and MCUSCOPE_CACHE_DIR (SPEC 3.3).

platformdirs reads the Windows shell API through ctypes, so no environment variable it
consults moves a spawned child's dirs there (class 33, finding FD2-5). These do, on both
platforms, and `support.child_env` sets them for every child a test spawns.

Each resolver is driven three ways: with the variable set, with it set to the empty string
(which must count as unset), and with it absent, where the platformdirs default is the
positive control - conftest's isolation puts that default under `tmp_path/userdirs`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from mcuscope import config, dirs, pidfile, update_check
from tests.support import CHILD_TEXT, child_env


def _platformdirs_default(tmp_path, fn: str) -> Path:
    """What conftest's isolate_user_dirs makes `platformdirs.<fn>("mcuscope")` return."""
    return tmp_path / "userdirs" / fn / "mcuscope"


# -- the three resolvers ---------------------------------------------------------------


def test_the_config_dir_override_moves_config_toml(tmp_path, monkeypatch) -> None:
    assert config.default_config_path() == _platformdirs_default(
        tmp_path, "user_config_dir") / "config.toml"
    monkeypatch.setenv("MCUSCOPE_CONFIG_DIR", str(tmp_path / "cfgdir"))
    assert config.default_config_path() == tmp_path / "cfgdir" / "config.toml"
    monkeypatch.setenv("MCUSCOPE_CONFIG_DIR", "")
    assert config.default_config_path() == _platformdirs_default(
        tmp_path, "user_config_dir") / "config.toml"


def test_the_data_dir_override_moves_the_default_capture_db(tmp_path, monkeypatch) -> None:
    default = str(_platformdirs_default(tmp_path, "user_data_dir") / "capture.db")
    assert config.resolve_db_path(config.Config()) == default
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path / "datadir"))
    assert config.resolve_db_path(config.Config()) == str(tmp_path / "datadir" / "capture.db")
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", "")
    assert config.resolve_db_path(config.Config()) == default


def test_a_configured_db_path_still_wins_over_the_data_dir_override(tmp_path, monkeypatch) -> None:
    """The override is the *default*'s directory, not an override of an explicit db_path."""
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path / "datadir"))
    cfg = config.Config()
    cfg.storage.db_path = str(tmp_path / "named.db")
    assert config.resolve_db_path(cfg) == str(tmp_path / "named.db")


def test_the_cache_dir_override_moves_the_update_cache(tmp_path, monkeypatch) -> None:
    default = _platformdirs_default(tmp_path, "user_cache_dir") / "update.json"
    assert update_check.cache_path() == default
    monkeypatch.setenv("MCUSCOPE_CACHE_DIR", str(tmp_path / "cachedir"))
    assert update_check.cache_path() == tmp_path / "cachedir" / "update.json"
    monkeypatch.setenv("MCUSCOPE_CACHE_DIR", "")
    assert update_check.cache_path() == default


def test_one_override_does_not_move_the_other_two(tmp_path, monkeypatch) -> None:
    """A shared helper keyed by the wrong string would move all three together."""
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path / "only-data"))
    assert dirs.user_dir("data") == str(tmp_path / "only-data")
    assert dirs.user_dir("config") == str(_platformdirs_default(tmp_path, "user_config_dir"))
    assert dirs.user_dir("cache") == str(_platformdirs_default(tmp_path, "user_cache_dir"))


def test_the_override_is_used_as_given(tmp_path, monkeypatch) -> None:
    """No expanduser, no app-name suffix: the value names the directory itself."""
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path / "as-given"))
    assert dirs.user_dir("data") == str(tmp_path / "as-given")


def test_the_conftest_isolation_clears_an_ambient_override(tmp_path, monkeypatch) -> None:
    """An override exported in the developer's own shell would beat the platformdirs patch
    and put an in-process test back on a real directory (class 33)."""
    from tests.conftest import isolate_user_dirs

    for kind in ("DATA", "CONFIG", "CACHE"):
        monkeypatch.setenv(f"MCUSCOPE_{kind}_DIR", str(tmp_path / "ambient"))
    isolate_user_dirs(monkeypatch, tmp_path)
    for kind, fn in (("data", "user_data_dir"), ("config", "user_config_dir"),
                     ("cache", "user_cache_dir")):
        assert dirs.user_dir(kind) == str(_platformdirs_default(tmp_path, fn))


# -- the pid record --------------------------------------------------------------------


def test_the_pid_record_follows_the_data_dir_override(tmp_path, monkeypatch) -> None:
    default = pidfile.pid_file_path("127.0.0.1", 8558)
    assert default == str(_platformdirs_default(tmp_path, "user_data_dir")
                          / "mcuscoped-127.0.0.1-8558.pid")
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path / "piddir"))
    path = pidfile.pid_file_path("127.0.0.1", 8558)
    assert path == str(tmp_path / "piddir" / "mcuscoped-127.0.0.1-8558.pid")
    assert os.path.isdir(tmp_path / "piddir"), "the resolver creates the dir it names"


# -- a spawned child ---------------------------------------------------------------------

# The crash path of the real console entry, with nothing patched inside the child: the
# environment alone must decide where the log lands.
CHILD = """
from mcuscope import _stdio
def main():
    raise RuntimeError("dirs-override-crash-sentinel")
raise SystemExit(_stdio.console_entry(main, "mcu"))
"""


def test_a_child_crash_log_lands_in_the_childs_data_dir_override(tmp_path) -> None:
    """child_env's MCUSCOPE_DATA_DIR takes the child's crash log, on Windows too.

    The override names a directory the XDG home does not, so the file's own path is what
    pins which mechanism placed it.
    """
    override = tmp_path / "override"
    env = child_env(str(tmp_path / "home"), MCUSCOPE_DATA_DIR=str(override))
    p = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True,
                       timeout=60, **CHILD_TEXT)
    assert p.returncode == 1, p.stderr
    log = override / "mcu-crash.log"
    assert log.is_file(), f"no crash log under the override: {sorted(tmp_path.iterdir())}"
    assert "dirs-override-crash-sentinel" in log.read_text(encoding="utf-8")
    assert not (tmp_path / "home" / "mcuscope" / "mcu-crash.log").exists()


def test_child_env_names_all_three_dirs_under_its_own_home() -> None:
    """The variables `mcu` and `mcuscoped` read, not just the XDG ones (FD2-5)."""
    env = child_env()
    for kind in ("DATA", "CONFIG", "CACHE"):
        value = env[f"MCUSCOPE_{kind}_DIR"]
        assert value.startswith(env[f"XDG_{kind}_HOME"]), value
        assert os.path.basename(value) == "mcuscope", value
