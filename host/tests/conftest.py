"""Shared test fixtures / path setup.

Puts the repo `tools/` directory on sys.path so tests can import `mcu_sim` without
installing it (it is a development tool, not part of the mcuscope package), and
provides the sim+daemon `stack` fixtures shared by the e2e and CLI suites.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator

# No test may reach out to PyPI. The daemon's release check (SPEC 3.6) is on by default,
# and every app created here would otherwise fire one request per run: set the environment
# veto before anything imports the daemon, so the suite stays offline whatever a test's
# config says. Individual tests exercise the checker directly with a stubbed transport.
os.environ.setdefault("MCUSCOPE_UPDATE_CHECK", "0")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import pytest  # noqa: E402

from tests.support import Stack  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_user_dirs(tmp_path, monkeypatch):
    """No in-process test may touch the real platformdirs locations.

    Real instance 2026-08-09: a daemon.main() test with a default config wrote
    capture.db and its lock into the user's live data dir during a revert-verify run.
    """
    for fn in ("user_data_dir", "user_config_dir", "user_cache_dir"):
        monkeypatch.setattr(
            f"platformdirs.{fn}", lambda app, _fn=fn: str(tmp_path / "userdirs" / _fn / app)
        )


@pytest.fixture
def make_stack() -> Iterator[Callable[..., Stack]]:
    created: list[Stack] = []

    def _make(sim_args: list[str] | None = None) -> Stack:
        s = Stack(sim_args)
        created.append(s)
        return s

    yield _make
    for s in created:
        s.close()


@pytest.fixture
def stack(make_stack: Callable[..., Stack]) -> Stack:
    return make_stack()


@pytest.fixture(autouse=True)
def _isolate_report_key():
    """Restore `_stdio._report_key` around every test.

    In production one process is one daemon, so the key is set once at startup and stays.
    The suite runs many daemons per process, and `daemon.main()` sets it as a side effect,
    so without this a test that starts a daemon silently renames the startup and crash logs
    of every test collected after it - which is how test_stdio's two path assertions failed
    in the full run while passing in isolation (registry class 32).
    """
    from mcuscope import _stdio

    saved = _stdio._report_key
    yield
    _stdio._report_key = saved
