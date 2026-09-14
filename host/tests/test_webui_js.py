"""Run the web UI's JavaScript test suite from pytest.

This shells out to `node --test` over `host/tests/webui_js`, which loads every shipped
`webui/*.js` module under a small DOM stub (no npm packages, no jsdom, no browser driver)
and drives the pure logic: the WS backfill/staging path, the plot decode and scale path,
the pane queue bounds, and the CAN/terminal/status formatters. The node runner returns
non-zero if any check fails. Skipped cleanly when node is missing or too old, so the Python
suite still passes on a box without it.
"""

from __future__ import annotations

import json
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

pytestmark = [
    pytest.mark.skipif(_MAJOR is None, reason="no node on PATH"),
    pytest.mark.skipif(
        _MAJOR is not None and _MAJOR < MIN_NODE_MAJOR,
        reason=f"node {_MAJOR} predates the built-in test runner (need {MIN_NODE_MAJOR}+)",
    ),
]

# Accepted and refused URLs for every guard exportdlg_guards.mjs mirrors.
GUARD_URLS = [
    "/lines/export?format=csv",
    "/lines/export?format=xml",
    "/lines/export?chan=debug&chan=cmd",
    "/lines/export?chan=debug,cmd",
    "/lines/export?chan=debug&chan=nope",
    "/lines/export?id_to=0",
    "/lines/export?id_to=-1",
    "/lines/export?since_ts=10&until_ts=5",
    "/plot/export?names=v",
    "/plot/export?format=long",
    "/plot/export?names=v&format=xml",
    "/plot/export?names=v&changes=1",
    "/plot/export?names=v&decode=1&changes=1",
    "/plot/export?names=v&decode=1&changes=1&deadband=v=0.5",
    "/plot/export?names=v&decode=1&changes=1&deadband=w=1",
    "/plot/export?names=v&decode=1&changes=1&deadband=v",
    "/plot/export?names=v&decode=1&deadband=v=1",
    "/can/frames?format=csv",
    "/can/frames?format=xml",
    "/can/frames?id=0x100",
    "/can/frames?id=zz",
    "/can/frames?id=0x20000000",
]


def test_export_guard_double_agrees_with_the_daemon(tmp_path) -> None:
    """The JS export double must refuse exactly what the daemon refuses, in its words."""
    from fastapi.testclient import TestClient

    from tests.test_export_lines_can import T0, _mk_app, _on_loop

    module = (JS_TESTS / "exportdlg_guards.mjs").as_uri()
    script = f"import {{ refuse }} from {json.dumps(module)};" \
             f"console.log(JSON.stringify({json.dumps(GUARD_URLS)}.map(refuse)));"
    proc = subprocess.run(["node", "--input-type=module", "-e", script],
                          capture_output=True, **CHILD_TEXT)
    assert proc.returncode == 0, proc.stderr
    double = json.loads(proc.stdout)

    with TestClient(_mk_app(tmp_path), base_url="http://127.0.0.1") as client:
        store = client.app.state.store
        _on_loop(client, store.add_line(ts=T0, port="board", dir="rx", chan="event", seq=None,
                                        raw="!p v=1", plot=[(1, None, "v", 1.0)]))
        daemon = []
        for url in GUARD_URLS:
            r = client.get(url)
            daemon.append(r.json()["error"] if r.status_code >= 400 else None)
    mismatches = [(u, d, j) for u, d, j in zip(GUARD_URLS, daemon, double, strict=True)
                  if d != j]
    assert not mismatches, f"(url, daemon, double): {mismatches}"


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
    #
    # The summary line's spelling depends on the default reporter, which node has changed
    # once already: tap prints "# pass 155" (the default up to node 21 when piped), spec
    # prints "ℹ pass 155" (the default from node 22 everywhere). Accept both rather
    # than pin one version's vocabulary; `--test-reporter=tap` would pin it properly but
    # only exists from 18.15, above this file's floor.
    counts = {
        k: int(v)
        for k, v in re.findall(r"^(?:#|ℹ) (pass|fail) (\d+)$", proc.stdout, re.M)
    }
    if not counts:
        pytest.fail(
            f"no tap or spec summary in the node output; did the runner change?\n{proc.stdout}"
        )
    if counts.get("fail") or counts.get("pass", 0) < declared:
        pytest.fail(
            f"the JavaScript suite reported {counts}, but its {len(files)} files declare "
            f"{declared} tests; it did not run what it should have:\n{proc.stdout}"
        )
