"""Run the web UI's JavaScript test suite from pytest.

This shells out to `node --test` over `host/tests/webui_js`, which loads every shipped
`webui/*.js` module under a small DOM stub (no npm packages, no jsdom, no browser driver)
and drives the pure logic: the WS backfill/staging path, the plot decode and scale path,
the pane queue bounds, and the CAN/terminal/status formatters. The node runner returns
non-zero if any check fails. Skipped cleanly when node is missing or too old, so the Python
suite still passes on a box without it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support import CHILD_TEXT

JS_TESTS = Path(__file__).resolve().parent / "webui_js"

# `node --test` and the `node:test` module both land in 18; nothing here needs anything newer.
MIN_NODE_MAJOR = 18


def _node_major() -> int | None:
    """Major version of the node on PATH, or None if there is none / it will not answer."""
    if shutil.which("node") is None:
        return None
    try:
        out = subprocess.run(["node", "--version"], capture_output=True, **CHILD_TEXT, check=True)
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
    # Derived from the suite itself rather than pinned to a number that goes stale: how many
    # top-level test() calls the files declare. A floor, not an equality - nested subtests
    # and t.test() forms count towards node's total and not towards this.
    files = sorted(JS_TESTS.glob("*.test.mjs"))
    assert files, f"no JavaScript test files found in {JS_TESTS}"
    declared = sum(
        len(re.findall(r"^test\(", f.read_text(encoding="utf-8"), re.M)) for f in files
    )
    assert declared, f"{len(files)} JavaScript test files declare no tests at all"

    proc = subprocess.run(
        ["node", "--test"],
        cwd=JS_TESTS,
        capture_output=True,
        **CHILD_TEXT,
    )
    if proc.returncode != 0:
        pytest.fail(f"web UI JavaScript tests failed:\n{proc.stdout}\n{proc.stderr}")

    # A green exit code proves nothing on its own: `node --test` exits 0 in a directory with
    # no test files, and counts a file that declares none as one passing test - so a bad cwd,
    # a renamed suffix or a filter that matches nothing all read as a pass. Check what the
    # runner says it ran against what the files declare.
    counts = {k: int(v) for k, v in re.findall(r"^# (pass|fail) (\d+)$", proc.stdout, re.M)}
    if not counts:
        pytest.fail(f"no TAP summary in the node output; did the runner change?\n{proc.stdout}")
    if counts.get("fail") or counts.get("pass", 0) < declared:
        pytest.fail(
            f"the JavaScript suite reported {counts}, but its {len(files)} files declare "
            f"{declared} tests; it did not run what it should have:\n{proc.stdout}"
        )
