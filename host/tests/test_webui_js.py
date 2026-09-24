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

_EMOJI = "%F0%9F%98%80"  # one code point, two UTF-16 units: `match` length counts code points
_DB = "/plot/export?names=v&decode=1&changes=1&deadband="

# Accepted and refused URLs for every clause exportdlg_guards.mjs mirrors, in the daemon's order.
# Excluded, as the double does not model them: `match=(` (regex compile), a wide export spanning
# two streams, a deadband on a label channel.
GUARD_URLS = [
    # /lines/export: 422 pass, declaration order, repeated scalar takes its last value
    "/lines/export",
    "/lines/export?chan=debug&chan=cmd",
    "/lines/export?chan=debug,cmd",
    "/lines/export?chan=debug&chan=nope",
    "/lines/export?chan=",
    "/lines/export?chan=x&chan=y",
    "/lines/export?chan=it's",
    "/lines/export?chan=a%5Cb",
    "/lines/export?chan=a%27b%22c",
    "/lines/export?chan=a%0Ab%09%01%7F%C2%80%C2%A0%C2%AD%E2%80%A8z",
    f"/lines/export?chan={_EMOJI}",
    "/lines/export?since_id=-5",
    "/lines/export?since_id=9223372036854775807",
    "/lines/export?since_id=9223372036854775808",
    "/lines/export?since_id=x",
    "/lines/export?since_ts=abc",
    "/lines/export?since_ts=",
    "/lines/export?since_ts=1e3",
    "/lines/export?since_ts=%201%20",
    "/lines/export?since_ts=%C2%A01",
    "/lines/export?since_ts=%EF%BB%BF1",
    "/lines/export?since_ts=1_0",
    "/lines/export?since_ts=-_1",
    "/lines/export?since_ts=1_.5",
    "/lines/export?since_ts=1__0",
    "/lines/export?since_ts=1_",
    "/lines/export?since_ts=0x10",
    "/lines/export?since_ts=.5",
    "/lines/export?since_ts=5.",
    "/lines/export?since_ts=.",
    "/lines/export?since_ts=1e",
    "/lines/export?since_ts=%D9%A3",
    "/lines/export?since_ts=1&since_ts=abc",
    "/lines/export?since_ts=abc&since_ts=1",
    "/lines/export?until_ts=x",
    "/lines/export?last_ms=1000000000000000",
    "/lines/export?last_ms=1000000000000001",
    "/lines/export?last_ms=1.5",
    "/lines/export?id_to=0",
    "/lines/export?id_to=-1",
    "/lines/export?id_to=-0",
    "/lines/export?id_to=-1.0",
    "/lines/export?id_to=1.00",
    "/lines/export?id_to=1.",
    "/lines/export?id_to=1.01",
    "/lines/export?id_to=.0",
    "/lines/export?id_to=%2B1",
    "/lines/export?id_to=%2B",
    "/lines/export?id_to=1_000",
    "/lines/export?id_to=1_.00",
    "/lines/export?id_to=%20%2B1_0.00%20",
    "/lines/export?id_to=0x10",
    "/lines/export?id_to=1e3",
    "/lines/export?id_to=",
    "/lines/export?id_to=%D9%A3",
    "/lines/export?id_to=00000000000000000000000001",
    "/lines/export?id_to=9223372036854775807",
    "/lines/export?id_to=9223372036854775808",
    "/lines/export?id_to=5&id_to=-1",
    "/lines/export?id_to=-1&id_to=5",
    "/lines/export?id_to=-1&last_ms=x&since_ts=y&chan=q",
    # /lines/export: handler guards
    "/lines/export?format=xml&id_to=-1",
    "/lines/export?format=csv",
    "/lines/export?format=jsonl",
    "/lines/export?format=xml",
    "/lines/export?format=",
    "/lines/export?format=xml&match=(",
    "/lines/export?match=",
    "/lines/export?match=" + "a" * 200,
    "/lines/export?match=" + "a" * 201,
    "/lines/export?match=" + _EMOJI * 200,
    "/lines/export?match=" + _EMOJI * 201,
    "/lines/export?match=" + "a" * 201 + "&since_ts=5&until_ts=1",
    "/lines/export?since_ts=10&until_ts=5",
    "/lines/export?since_ts=5&until_ts=5",
    "/lines/export?since_ts=10&until_ts=5&session=nosuch",
    "/lines/export?session=nosuch",
    "/lines/export?session=run-a",
    "/lines/export?session=2",
    "/lines/export?session=02",
    "/lines/export?session=3",
    "/lines/export?session=",
    "/lines/export?session=%D9%A1",
    # /can/frames
    "/can/frames",
    "/can/frames?bus=0",
    "/can/frames?bus=1",
    "/can/frames?bus=9",
    "/can/frames?bus=10",
    "/can/frames?limit=-1",
    "/can/frames?limit=0",
    "/can/frames?limit=1.0",
    "/can/frames?id_to=-1",
    "/can/frames?since_id=9223372036854775808",
    "/can/frames?last_ms=1000000000000001",
    "/can/frames?bus=0&limit=-1",
    "/can/frames?format=xml&bus=0",
    "/can/frames?format=csv",
    "/can/frames?format=xml",
    "/can/frames?format=xml&since_ts=5&until_ts=1",
    "/can/frames?since_ts=inf",
    "/lines/export?since_ts=nan",
    "/lines/export?until_ts=-inf",
    "/lines/export?since_ts=inf&until_ts=nan",
    "/plot/export?names=v&until_ts=infinity",
    "/lines/export?until_ts=1e300",
    "/can/frames?id=zz&since_ts=5&until_ts=1",
    "/can/frames?since_ts=5&until_ts=5",
    "/can/frames?id=0x100",
    "/can/frames?id=0x100,",
    "/can/frames?id=0x100,,0x200",
    "/can/frames?id=,",
    "/can/frames?id=",
    "/can/frames?id=zz",
    "/can/frames?id=0x100,zz",
    "/can/frames?id=0x",
    "/can/frames?id=%201",
    "/can/frames?id=0x1FFFFFFF",
    "/can/frames?id=0X1fffffff",
    "/can/frames?id=1FFFFFFF",
    "/can/frames?id=0x20000000",
    "/can/frames?id=FFFFFFFFFFFFFFFF",
    "/can/frames?id=11111111111111111",
    "/can/frames?id=zz&session=nosuch",
    "/can/frames?session=nosuch",
    "/can/frames?session=run-a",
    # /plot/export: 422 pass
    "/plot/export?format=long",
    "/plot/export?since_ts=x",
    "/plot/export?names=v&decode=2",
    "/plot/export?names=v&decode=",
    "/plot/export?names=v&decode=%20true",
    "/plot/export?names=v&decode=00",
    "/plot/export?names=v&decode=Yes&changes=ON",
    "/plot/export?names=v&decode=1&changes=2",
    "/plot/export?names=v&id_to=-1",
    "/plot/export?names=v&since_id=-5",
    "/plot/export?names=v&since_id=1.5",
    "/plot/export?names=v&since_id=9223372036854775807",
    "/plot/export?names=v&since_id=9223372036854775808",
    "/plot/export?names=v&since_id=x&since_ts=y&id_to=-1",
    "/plot/export?names=v&since_id=5&id_to=1",
    "/plot/export?names=v&last_ms=1000000000000001",
    "/plot/export?names=,&id_to=-1",
    # /plot/export: handler guards
    "/plot/export?names=",
    "/plot/export?names=,,",
    "/plot/export?names=,&format=xml",
    "/plot/export?names=v",
    "/plot/export?names=v&format=wide",
    "/plot/export?names=v&format=xml",
    "/plot/export?names=v&format=xml&since_ts=5&until_ts=1",
    "/plot/export?names=v&since_ts=5&until_ts=1&changes=1",
    "/plot/export?names=v&since_ts=5&until_ts=5",
    "/plot/export?names=v&changes=1",
    "/plot/export?names=v&decode=0&changes=1",
    "/plot/export?names=v&decode=1&changes=1",
    "/plot/export?names=v&decode=0&changes=off",
    "/plot/export?names=v&decode=1&deadband=v=1",
    "/plot/export?names=v&decode=1&changes=0&deadband=v=1",
    "/plot/export?names=v&deadband=",
    _DB,
    _DB + "v",
    _DB + "v=1,v=2",
    _DB + "v=+5",
    _DB + "v=1_0",
    _DB + "v=.5",
    "/plot/export?names=v,v",
    "/lines/export?last_ms=-1",
    "/can/frames?format=csv&last_ms=-1",
    _DB + "w=1",
    _DB + "=1",
    _DB + "w,v=nan",
    _DB + "v=nan,w",
    _DB + "v=abc",
    _DB + "v=inf",
    _DB + "v=-nan",
    _DB + "v=1e400",
    _DB + "v=0x1",
    _DB + "v=%D9%A3",
    _DB + "v=%20",
    _DB + "v=%1C1",
    _DB + "v=1__0",
    _DB + "v=0.5",
    _DB + "v=-0",
    _DB + "v=-1e-9",
    _DB + "v=%201%20",
    _DB + "v=1_0",
    _DB + "v=.5e-1",
    _DB + "v=-1",
    _DB + "v=5.",
    _DB + ",,v=1,",
    _DB + "v=abc&session=nosuch",
    "/plot/export?names=v&session=nosuch",
    "/plot/export?names=v&session=",
    "/plot/export?names=v&session=run-a",
    "/plot/export?names=v&session=2",
    "/plot/export?names=nope&session=nosuch",
    "/plot/export?names=nope",
    "/plot/export?names=v,v,nope,q",
    "/plot/export?names=nope,nope",
    "/plot/export?names=v&names=nope",
    "/plot/export?names=nope&names=v",
    "/plot/export?names=v&port=board",
    "/plot/export?names=v&port=other",
    "/plot/export?names=v&port=",
    "/plot/export?names=v&port=other&since_ts=5&until_ts=1",
    "/lines/export?port=other&session=nosuch",
    "/lines/export?port=board",
    "/can/frames?format=csv&port=other&id=zz",
    "/lines/export?chans=sys&limit=5",
    "/plot/export?names=v&last_ms=x&bogus=1",
    "/lines/export?since_id=-9223372036854775809",
]


def test_export_guard_double_agrees_with_the_daemon(tmp_path) -> None:
    """The JS export double must refuse exactly what the daemon refuses, in its words."""
    from fastapi.testclient import TestClient

    from tests.support import mk_app, on_loop
    from tests.test_export_lines_can import T0

    with TestClient(mk_app(tmp_path), base_url="http://127.0.0.1") as client:
        store = client.app.state.store
        run = on_loop(client, store.start_session("run-a"))
        assert run["id"] == 2, "GUARD_URLS names this session by id 2, after the auto session"
        on_loop(client, store.add_line(ts=T0, port="board", dir="rx", chan="event", seq=None,
                                        raw="!p v=1", plot=[(1, None, "v", 1.0)]))
        sessions = [{"id": s["id"], "name": s["name"]}
                    for s in on_loop(client, store.list_sessions_safe())]
        daemon = []
        for url in GUARD_URLS:
            r = client.get(url)
            daemon.append(r.json()["error"] if r.status_code >= 400 else None)

        # Every port a stored row carries (no board is attached here).
        ports = sorted({r["port"] for r in client.get("/lines", params={"limit": 1000})
                        .json()["lines"]})

    known = {"channels": [{"name": "v", "port": "board"}],
             "sessions": sessions, "ports": ports}
    module = (JS_TESTS / "exportdlg_guards.mjs").as_uri()
    script = (f"import {{ refuse }} from {json.dumps(module)};"
              f"const known = {json.dumps(known)};"
              f"const urls = {json.dumps(GUARD_URLS)};"
              "console.log(JSON.stringify(urls.map((u) => refuse(u, known))));")
    proc = subprocess.run(["node", "--input-type=module", "-e", script],
                          capture_output=True, **CHILD_TEXT)
    assert proc.returncode == 0, proc.stderr
    double = json.loads(proc.stdout)

    assert None in daemon and any(daemon), "the list must hold both accepted and refused URLs"
    # "Every clause" derived from the double: each refusal literal it can return (the longest
    # fixed fragment of a template) must come back for some URL here.
    src = (JS_TESTS / "exportdlg_guards.mjs").read_text(encoding="utf-8")
    clauses = {max(re.split(r"\$\{[^}]*\}", a or b), key=len)
               for a, b in re.findall(r'(?:return|errs\.push\()\s*(?:"([^"]*)"|`([^`]*)`)', src)}
    assert len(clauses) >= 20, clauses
    unreached = sorted(c for c in clauses if not any(c in (d or "") for d in double))
    assert not unreached, f"no GUARD_URL reaches these refusals of the double: {unreached}"
    mismatches = [(u, d, j) for u, d, j in zip(GUARD_URLS, daemon, double, strict=True)
                  if d != j]
    assert not mismatches, f"(url, daemon, double): {mismatches}"


# The whole node suite (one process per file) runs in this one child: 30 s on
# Linux, minutes on Windows, where process spawn is the cost. Its own backstop, well
# above the 90 s default, which fired on the Windows runner as a "wedged" stack dump.
@pytest.mark.timeout(600)
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
