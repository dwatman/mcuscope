#!/usr/bin/env python3
"""Manual smoke harness for the Phase 6 web UI (SPEC 9.1).

Brings up the simulator (--plot --garbage) and the daemon in-process, auto-verifies the
API-observable acceptance criteria, then stays running so you can open the UI in a browser
and eyeball each panel. Press Ctrl+C to tear everything down.

    python tools/webui_smoke.py               # serves http://127.0.0.1:8765/ui/
    python tools/webui_smoke.py --port 8770   # if 8765 is taken by another daemon
    python tools/webui_smoke.py --no-wait      # run the auto-checks and exit (for scripts)

Run it from the host venv (so `mcuscope` imports). Stop any other mcuscoped first, or pass
a free --port. The auto-checks cover the backend half of the SPEC 9.1 acceptance list; the
printed checklist covers the browser half (including the offline reload).
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import threading
import time

import httpx
import mcu_sim
import uvicorn

from mcuscope.config import Config, PortConfig, ServerConfig, StorageConfig
from mcuscope.server import create_app

GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"


def _start_sim(extra: list[str]) -> tuple:
    """Open a TCP sim listener on an ephemeral port and serve it in a background thread."""
    stop = threading.Event()
    sock = mcu_sim.open_tcp_listener(0)
    port = sock.getsockname()[1]
    args = mcu_sim.build_parser().parse_args(extra)
    thread = threading.Thread(
        target=mcu_sim.serve_listener, args=(args, sock, stop), daemon=True
    )
    thread.start()
    return stop, sock, port, thread


def _wait_ready(base: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base}/status", timeout=1.0)
            if r.status_code == 200 and r.json()["ports"] and r.json()["ports"][0]["connected"]:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise RuntimeError("daemon/sim did not become ready")


def _check(name: str, ok: bool, detail: str) -> bool:
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {name} {DIM}- {detail}{RESET}")
    return ok


def _run_checks(base: str, sim2_port: int) -> bool:
    c = httpx.Client(base_url=base, timeout=5.0)
    results: list[bool] = []

    # Terminal: capture is live - new line ids keep appearing.
    first = c.get("/lines", params={"order": "desc", "limit": 1}).json()["lines"]
    time.sleep(0.5)
    second = c.get("/lines", params={"order": "desc", "limit": 1}).json()["lines"]
    live = bool(first and second and second[0]["id"] > first[0]["id"])
    results.append(_check("terminal: live capture streaming", live,
                          f"id advanced {first[0]['id'] if first else '?'} -> "
                          f"{second[0]['id'] if second else '?'}"))

    # Command box: a cmd returns its response inline.
    r = c.post("/cmd", json={"cmd": "ping", "timeout_ms": 1000}).json()
    ok = r.get("status") == "ok"
    results.append(_check("command box: POST /cmd 'ping' -> ok", ok,
                          f"status={r.get('status')} data={r.get('data')!r} "
                          f"latency={r.get('latency_ms', 0):.1f}ms"))

    # CAN table: 0x100 heartbeat present with period near 100 ms.
    ticks: list[int] = []
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        frames = c.get("/can/frames", params={"id": "0x100", "limit": 30}).json()["frames"]
        ticks = sorted(f["tick_ms"] for f in frames)
        if len(ticks) >= 15:
            break
        time.sleep(0.2)
    deltas = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
    period = statistics.median(deltas) if deltas else 0
    ok = bool(deltas) and 80 <= period <= 120
    results.append(_check("CAN table: 0x100 heartbeat ~100 ms", ok,
                          f"median period {period:.0f} ms over {len(deltas)} frames"))

    # Plot panel: typed + ad-hoc channels decode and expose scaled values (SPEC 9.2).
    chans: dict = {}
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        chans = {c["name"]: c for c in c.get("/plot/channels").json()["channels"]}
        if {"tri", "sine"} <= set(chans) and chans["tri"]["count"] >= 5:
            break
        time.sleep(0.2)
    tri = chans.get("tri", {})
    ok = bool({"tri", "sine", "ftest"} <= set(chans)) and tri.get("unit") == "V"
    results.append(_check("plot panel: typed + ad-hoc channels decode", ok,
                          f"tri unit={tri.get('unit')} scale={tri.get('scale')} "
                          f"n={tri.get('count')}, {len(chans)} channels"))

    # Digital panel: enum bus ('state') and packed-bit channel ('led') are classified.
    ok = chans.get("state", {}).get("kind") == "enum" and chans.get("led", {}).get("kind") == "bit"
    results.append(_check("digital panel: enum + bit channels classified", ok,
                          f"state={chans.get('state', {}).get('kind')}, "
                          f"led={chans.get('led', {}).get('kind')}"))

    # Setup bar: attach a second sim, then detach it.
    dev2 = f"socket://127.0.0.1:{sim2_port}"
    c.post("/ports", json={"alias": "board2", "device": dev2, "baud": 115200})
    attached = False
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        ports = {p["alias"]: p["connected"] for p in c.get("/status").json()["ports"]}
        if ports.get("board2"):
            attached = True
            break
        time.sleep(0.1)
    c.delete("/ports/board2")
    gone = "board2" not in {p["alias"] for p in c.get("/status").json()["ports"]}
    results.append(_check("setup bar: attach + detach second sim", attached and gone,
                          f"attached={attached} detached={gone}"))

    c.close()
    return all(results)


def main() -> int:
    ap = argparse.ArgumentParser(description="Web UI smoke harness (SPEC 9.1)")
    ap.add_argument("--port", type=int, default=8765, help="daemon HTTP port (default 8765)")
    ap.add_argument("--no-wait", action="store_true",
                    help="run the auto-checks and exit instead of staying up for browser checks")
    args = ap.parse_args()

    sim_stop, sim_sock, sim_port, _ = _start_sim(["--plot", "--garbage"])
    sim2_stop, sim2_sock, sim2_port, _ = _start_sim([])

    tmpdir = tempfile.mkdtemp(prefix="webui-smoke-")
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=args.port),
        storage=StorageConfig(db_path=f"{tmpdir}/capture.db", retention_days=7),
        ports=[PortConfig(alias="board", device=f"socket://127.0.0.1:{sim_port}",
                          baud=115200, autoconnect=True)],
    )
    server = uvicorn.Server(uvicorn.Config(
        create_app(config), host="127.0.0.1", port=args.port, log_level="warning"))
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    base = f"http://127.0.0.1:{args.port}"

    try:
        _wait_ready(base)
    except RuntimeError as exc:
        print(f"{RED}stack failed to start:{RESET} {exc} (is port {args.port} already in use?)")
        return 1

    print(f"\n{DIM}auto-checks (backend half of the SPEC 9.1 acceptance list):{RESET}")
    ok = _run_checks(base, sim2_port)
    verdict = f"{GREEN}all passed{RESET}" if ok else f"{RED}FAILURES above{RESET}"
    print(f"\nauto-checks: {verdict}\n")

    if args.no_wait:
        server.should_exit = True
        return 0 if ok else 1

    print(f"open {GREEN}{base}/ui/{RESET} and confirm each panel:")
    for line in (
        "status bar shows 'mcuscoped <ver>' with a green dot and the 'board' chip connected",
        "terminal streams live debug lines; channel chips + regex filter narrow them",
        "add a pane; give it a different filter; pause it (scrollbar freezes, 'N new' counts)",
        "type 'i2c rd 48 2' in the command box -> inline ok result; try a bad cmd -> red err",
        "Marker field + button -> a divider line appears in the terminal",
        "CAN tab: rows for 0x100 (period ~100 ms), plus 200/18A(ext)/321/400(rtr); Reset clears",
        "Plots tab: 'stream 0' chart (tri/ramp/ftest w/ units) + 'ad-hoc' chart (sine/noisy)",
        "  toggle a channel checkbox, change the 5s/30s/5m window, pause, and 'x: mcu tick'",
        "  click 'csv' on a chart -> a plot CSV downloads (wide for the stream, long for ad-hoc)",
        "Digital/Enum panel (below the analog charts): 'state' bus (IDLE/ARMED/RUN) +",
        "  led/irq/pwm_en square waves; hover an analog chart -> shared amber cursor over the",
        "  lanes; window/pause/csv/collapse buttons; click a name to toggle, a swatch to recolour",
        "Attach dialog: add a second port, watch its chip go green, then detach it",
        "OFFLINE: Ctrl+C here to stop the daemon, then reload the page - it must still render",
    ):
        print(f"  {DIM}-{RESET} {line}")
    print(f"\n{DIM}Ctrl+C to tear down the stack.{RESET}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nshutting down...")
    finally:
        server.should_exit = True
        for stop, sock in ((sim_stop, sim_sock), (sim2_stop, sim2_sock)):
            stop.set()
            try:
                sock.close()
            except OSError:
                pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
