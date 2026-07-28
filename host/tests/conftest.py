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
