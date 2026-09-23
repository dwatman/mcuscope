"""Shared test fixtures / path setup.

Puts the repo `tools/` directory on sys.path so tests can import `mcu_sim` (the source
checkout's shim over `mcuscope.sim`), and provides the sim+daemon `stack` fixtures shared
by the e2e and CLI suites.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

# No test may reach out to PyPI. The daemon's release check (SPEC 3.6) is on by default,
# and every app created here would otherwise fire one request per run: set the environment
# veto before anything imports the daemon, so the suite stays offline whatever a test's
# config says. Individual tests exercise the checker directly with a stubbed transport.
os.environ["MCUSCOPE_UPDATE_CHECK"] = "0"   # assign, not setdefault: an exported 1 must lose

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import pytest  # noqa: E402

from tests import support  # noqa: E402
from tests.support import Stack  # noqa: E402


def isolate_user_dirs(monkeypatch: pytest.MonkeyPatch, base) -> None:
    """Point every platformdirs function the package calls under `base`.

    The MCUSCOPE_*_DIR overrides are cleared with them: one set in the developer's own
    shell would win over the patch and put this process back on a real directory.
    """
    for fn in ("user_data_dir", "user_config_dir", "user_cache_dir"):
        monkeypatch.setattr(
            f"platformdirs.{fn}", lambda app, _fn=fn: str(base / "userdirs" / _fn / app)
        )
    for kind in ("DATA", "CONFIG", "CACHE"):
        monkeypatch.delenv(f"MCUSCOPE_{kind}_DIR", raising=False)


@pytest.fixture(autouse=True)
def _isolated_user_dirs(tmp_path, monkeypatch):
    """No in-process test may touch the real platformdirs locations.

    Real instance 2026-08-09: a daemon.main() test with a default config wrote
    capture.db and its lock into the user's live data dir during a revert-verify run.
    A fixture wider than a function is set up before this one: it calls isolate_user_dirs
    itself (test_timeline's module stack read the user's update cache, 2026-09-15).
    """
    isolate_user_dirs(monkeypatch, tmp_path)


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", "child_crash_expected: the test crashes a spawned child on purpose"
    )


@pytest.fixture(autouse=True)
def _no_child_crashed(request):
    """Fail a test whose spawned `mcu` or `mcuscoped` crashed.

    A crash still exits 1, and the traceback's source excerpt can quote the very message
    the test asserts on stderr, so neither check notices. console_entry writes
    `<prog>-crash.log` into the child's data dir, which child_env records.
    """
    yield
    dirs = set(support.CHILD_DATA_DIRS)
    support.CHILD_DATA_DIRS.clear()
    if request.node.get_closest_marker("child_crash_expected"):
        return
    logs = sorted(p for d in dirs for p in Path(d).glob("*-crash.log"))
    if logs:
        report = "\n".join(f"--- {p}\n{p.read_text(encoding='utf-8', errors='replace')}"
                           for p in logs)
        for p in logs:
            p.unlink()       # the default dir is shared: the next test starts clean
        pytest.fail(f"a spawned child crashed:\n{report}", pytrace=False)


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


@pytest.fixture(autouse=True)
def _isolate_output_state(monkeypatch):
    """Reset the per-process CLI state main() resets per call, for tests that call internals.

    A leaked "stdout" in `_repaired_at_start` makes every later in-process `_dispatch` dup2 a
    write-only devnull over pytest's capture fd (EBADF for the rest of the run); a leaked
    `_OUT_FAILED` ends the next follow at once; a leaked `_JSON_MODE` turns the next direct
    `die` into a JSON object on stdout (class 32).
    """
    from mcuscope import _stdio, cli_output

    monkeypatch.setattr(_stdio, "_repaired_at_start", set())
    monkeypatch.setattr(cli_output, "_OUT_FAILED", False)
    monkeypatch.setattr(cli_output, "_JSON_MODE", False)
