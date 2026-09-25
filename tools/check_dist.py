#!/usr/bin/env python3
"""Checks on the built distribution, run by CI and by the release workflow on what they build.

    python tools/check_dist.py artifacts host/dist --source host
        Every file under mcuscope/webui/ in the source tree ships, non-empty, in the one wheel
        and the one sdist in the dist directory. The web UI is package data, so a build that
        drops it installs and imports fine and fails only when a user opens the UI.

    python tools/check_dist.py serve <venv scripts dir>
        The installed console scripts serve: `mcuscoped --sim` on a throwaway config and a free
        port answers /status, and `mcu --url ... daemon stop` exits 0 and the daemon exits. Its
        own status is not judged: a graceful stop replays SIGTERM, so POSIX reports -15.

Stdlib only, so it runs under any interpreter, with or without the package installed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# Files that must exist in the source tree AND the artifacts. They keep the check from passing
# vacuously if the webui tree itself went missing and the derived list below came up empty.
SENTINELS = [
    "mcuscope/webui/index.html",
    "mcuscope/webui/app.js",
    "mcuscope/webui/style.css",
    "mcuscope/webui/vendor/uPlot.iife.min.js",
    "mcuscope/webui/vendor/uPlot.min.css",
]


def check_artifacts(dist: Path, source: Path) -> list[str]:
    """Failures, one line each; empty when both artifacts carry the whole web UI."""
    failures = [f"source tree is missing sentinel {rel}" for rel in SENTINELS
                if not (source / rel).is_file()]
    webui = source / "mcuscope" / "webui"
    expected = sorted(
        "mcuscope/webui/" + p.relative_to(webui).as_posix()
        for p in webui.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    if len(expected) < len(SENTINELS):
        failures.append(f"only {len(expected)} webui files found in the source tree")

    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1:
        failures.append(f"expected exactly 1 wheel in {dist}, found {len(wheels)}")
    if len(sdists) != 1:
        failures.append(f"expected exactly 1 sdist in {dist}, found {len(sdists)}")
    if wheels:
        with zipfile.ZipFile(wheels[0]) as zf:
            sizes = {i.filename: i.file_size for i in zf.infolist()}
        for rel in expected:
            if rel not in sizes:
                failures.append(f"wheel is missing {rel}")
            elif sizes[rel] == 0:
                failures.append(f"wheel ships {rel} as an empty file")
    if sdists:
        with tarfile.open(sdists[0]) as tf:
            # sdist members are prefixed with <name>-<version>/
            members = {m.name.split("/", 1)[1]: m.size
                       for m in tf.getmembers() if m.isfile() and "/" in m.name}
        for rel in expected:
            if rel not in members:
                failures.append(f"sdist is missing {rel}")
            elif members[rel] == 0:
                failures.append(f"sdist ships {rel} as an empty file")
    return failures


def _script(scripts: Path, name: str) -> str:
    found = shutil.which(name, path=str(scripts))   # PATHEXT finds mcuscoped.exe on Windows
    if found is None:
        sys.exit(f"no {name} console script in {scripts}")
    return found


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _status(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{url}/status", timeout=2) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def check_serve(scripts: Path, timeout: float = 60.0) -> list[str]:
    """Start the installed daemon, see it answer, stop it with the installed CLI."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="check-dist-") as tmp:
        base = Path(tmp)
        env = dict(os.environ, MCUSCOPE_UPDATE_CHECK="0")
        for kind in ("DATA", "CONFIG", "CACHE"):   # never the runner's own user dirs
            env[f"MCUSCOPE_{kind}_DIR"] = str(base / kind.lower())
        db = (base / "capture.db").as_posix()
        config = base / "config.toml"
        config.write_text(f'[storage]\ndb_path = "{db}"\n', encoding="utf-8", newline="\n")
        port = _free_port()
        url = f"http://127.0.0.1:{port}"
        daemon = subprocess.Popen(
            [_script(scripts, "mcuscoped"), "--sim", "-c", str(config), "--port", str(port)],
            env=env,
        )
        try:
            deadline = time.monotonic() + timeout
            body = None
            while time.monotonic() < deadline and daemon.poll() is None:
                body = _status(url)
                if body is not None:
                    break
                time.sleep(0.2)
            if body is None:
                failures.append(f"mcuscoped never answered {url}/status "
                                f"(exit status {daemon.poll()})")
                return failures
            if Path(body.get("db_path", "")).resolve() != Path(db).resolve():
                failures.append(f"{url} is not the daemon just started: db_path "
                                f"{body.get('db_path')!r}")
                return failures
            stop = subprocess.run([_script(scripts, "mcu"), "--url", url, "daemon", "stop"],
                                  env=env, timeout=timeout)
            if stop.returncode != 0:
                failures.append(f"mcu daemon stop exited {stop.returncode}")
            try:
                print(f"mcuscoped exited with status {daemon.wait(timeout=timeout)}")
            except subprocess.TimeoutExpired:
                failures.append("mcuscoped still running after mcu daemon stop")
        finally:
            if daemon.poll() is None:
                daemon.kill()
                daemon.wait()
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("artifacts", help="the web UI ships in the wheel and the sdist")
    a.add_argument("dist", type=Path)
    a.add_argument("--source", type=Path, required=True, help="the host/ source tree")
    s = sub.add_parser("serve", help="the installed console scripts serve and stop")
    s.add_argument("scripts", type=Path, help="the venv's bin/ (Scripts\\ on Windows)")
    args = ap.parse_args(argv)
    if args.cmd == "artifacts":
        failures = check_artifacts(args.dist, args.source)
        done = f"OK: the web UI is present in the wheel and the sdist in {args.dist}"
    else:
        failures = check_serve(args.scripts)
        done = "OK: mcuscoped served /status and mcu daemon stop stopped it"
    for f in failures:
        print(f"FAILED: {f}", file=sys.stderr)
    if failures:
        return 1
    print(done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
