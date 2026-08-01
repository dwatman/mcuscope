"""Run the web UI's JavaScript test suite from pytest.

This shells out to `node --test` over `host/tests/webui_js`, which loads every shipped
`webui/*.js` module under a small DOM stub (no npm packages, no jsdom, no browser driver)
and drives the pure logic: the WS backfill/staging path, the plot decode and scale path,
the pane queue bounds, and the CAN/terminal/status formatters. The node runner returns
non-zero if any check fails. Skipped cleanly when node is missing or too old, so the Python
suite still passes on a box without it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

JS_TESTS = Path(__file__).resolve().parent / "webui_js"

# `node --test` and the `node:test` module both land in 18; nothing here needs anything newer.
MIN_NODE_MAJOR = 18


def _node_major() -> int | None:
    """Major version of the node on PATH, or None if there is none / it will not answer."""
    if shutil.which("node") is None:
        return None
    try:
        out = subprocess.run(["node", "--version"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    try:
        return int(out.stdout.strip().lstrip("v").split(".")[0])
    except ValueError:
        return None


_MAJOR = _node_major()


@pytest.mark.skipif(_MAJOR is None, reason="no node on PATH")
@pytest.mark.skipif(
    _MAJOR is not None and _MAJOR < MIN_NODE_MAJOR,
    reason=f"node {_MAJOR} predates the built-in test runner (need {MIN_NODE_MAJOR}+)",
)
def test_webui_js_suite() -> None:
    proc = subprocess.run(
        ["node", "--test"],
        cwd=JS_TESTS,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.fail(f"web UI JavaScript tests failed:\n{proc.stdout}\n{proc.stderr}")
