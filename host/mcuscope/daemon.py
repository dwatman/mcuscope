"""mcuscoped entry point: parse args, load config, run the app under uvicorn.

The app itself (endpoints, lifespan, port/store wiring) lives in server.py. This
module is just the process entry: it resolves configuration (SPEC 3.3) and hands the
app to uvicorn. The default bind is 127.0.0.1; non-loopback binds are supported for
LAN use and should set an access token via MCUSCOPED_TOKEN or --token (a loud
warning is printed otherwise; the token is runtime-only, never a config key).
"""

from __future__ import annotations

import argparse
import os
import signal
import threading
import webbrowser

import uvicorn

from . import __version__, _stdio, pidfile
from .config import Config, ConfigError, PortConfig, load_config, resolve_db_path
from .lockfile import CaptureLock, LockError
from .server import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcuscoped",
        description="Host daemon owning serial ports and serving the mcuscope REST/WS API.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"mcuscoped {__version__}\n{_stdio.python_line()}",
    )
    parser.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        help="Path to config.toml (env MCUSCOPED_CONFIG; "
        "default: platformdirs user config dir).",
    )
    parser.add_argument("--host", metavar="ADDR", help="Override server.host from config.")
    parser.add_argument(
        "--port", type=int, metavar="PORT", help="Override server.port from config."
    )
    parser.add_argument(
        "--token",
        metavar="TOKEN",
        help="Require this access token from non-loopback clients. Prefer the "
        "MCUSCOPED_TOKEN environment variable (not visible in the process list); "
        "the token is runtime-only and never read from the config file.",
    )
    parser.add_argument(
        "--sim",
        action="store_true",
        help="Zero-hardware demo: start the bundled MCU simulator in-process and "
        "autoconnect to it as port 'sim'. Combine with --open to land straight "
        "in the web UI.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the web UI in the default browser once the server is up.",
    )
    parser.add_argument(
        "--ignore-capture-lock",
        action="store_true",
        help="Start even if the capture database appears to be owned by another daemon. "
        "Only for a filesystem without working file locks: two daemons writing one "
        "capture collide on row ids.",
    )
    return parser


def _apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port
    env_token = os.environ.get("MCUSCOPED_TOKEN", "").strip()
    if env_token:
        config.server.token = env_token
    if args.token:
        config.server.token = args.token
    return config


GRACEFUL_SHUTDOWN_S = 5  # cap on waiting out in-flight requests at shutdown

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"})


def _warn_if_exposed(host: str, token: str | None) -> None:
    """Warn loudly when binding a non-loopback address without a token (SPEC 3.4)."""
    if host in _LOOPBACK_HOSTS:
        return
    if token is None:
        print(
            f"WARNING: binding {host} exposes the UNAUTHENTICATED mcuscope API to the network. "
            "Anyone who can reach this address can read captured data and drive the target. "
            "Set MCUSCOPED_TOKEN (or --token) to require an access token from network "
            "clients; the same-origin guard blocks browsers but not direct clients. "
            "Config editing over the API is disabled for network clients until a "
            "token is set.",
            flush=True,
        )
    elif len(token) < 16:
        print(
            "WARNING: the access token is shorter than 16 characters; use a longer "
            "random token for network exposure.",
            flush=True,
        )


def _start_sim(config: Config):
    """Start the bundled simulator in-process and add it to the config as port 'sim'.

    Returns a callable that shuts the simulator down. The listener uses an ephemeral
    TCP port, so it never collides with a standalone `mcu-sim` (default 9900).
    """
    from . import sim as mcu_sim  # local import: the demo path should not tax normal startup

    stop = threading.Event()
    sock = mcu_sim.open_tcp_listener(0)
    sim_port = sock.getsockname()[1]
    sim_args = mcu_sim.build_parser().parse_args(["--plot"])  # plots + CAN heartbeat on show
    threading.Thread(
        target=mcu_sim.serve_listener, args=(sim_args, sock, stop),
        name="mcu-sim", daemon=True,
    ).start()
    config.ports = [pc for pc in config.ports if pc.alias != "sim"]
    config.ports.append(
        PortConfig(alias="sim", device=f"socket://127.0.0.1:{sim_port}", autoconnect=True)
    )

    def shutdown() -> None:
        stop.set()
        try:
            sock.close()
        except OSError:
            pass

    return shutdown


def _ui_url(config: Config) -> str:
    # A wildcard bind is not a connectable address; show the loopback URL instead.
    host = config.server.host
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    elif ":" in host:  # bare IPv6 address needs brackets in a URL
        host = f"[{host}]"
    return f"http://{host}:{config.server.port}/ui/"


def _release_pid_on_terminating_signal(pid_path: str | None) -> None:
    """Make sure the pid record is removed when a signal ends the process.

    uvicorn (Server.capture_signals) handles SIGTERM/SIGBREAK itself, and after its
    graceful shutdown restores the original handlers and REPLAYS the signal, so the
    process dies inside uvicorn.run and main()'s finally never runs. Installing this
    handler first makes it that "original": the replay lands here, the pid record is
    released, and the signal is re-raised with the default disposition so the exit
    code still says what killed us. SIGINT is not needed: Python's default handler
    turns the replay into KeyboardInterrupt, which does unwind through finally.
    """

    def _handler(sig: int, frame: object) -> None:
        pidfile.release(pid_path)
        signal.signal(sig, signal.SIG_DFL)
        signal.raise_signal(sig)

    sigs = [signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):  # Windows: what `mcu daemon stop` sends
        sigs.append(signal.SIGBREAK)
    for sig in sigs:
        if signal.getsignal(sig) == signal.SIG_DFL:
            signal.signal(sig, _handler)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg_path = args.config or os.environ.get("MCUSCOPED_CONFIG") or None
    try:
        config = _apply_overrides(load_config(cfg_path), args)
    except ConfigError as exc:
        print(f"mcuscoped: {exc}", flush=True)
        return 1
    _warn_if_exposed(config.server.host, config.server.token)
    # Claim the capture before anything opens it. The app lifespan runs before uvicorn
    # binds its port, so checking any later means a doomed second daemon has already
    # written rows into the running one's database.
    lock = CaptureLock(resolve_db_path(config))
    try:
        lock.acquire()
    except LockError as exc:
        if not args.ignore_capture_lock:
            print(f"mcuscoped: {exc}", flush=True)
            return 1
        print(
            f"mcuscoped: WARNING: {exc.path} appears to be in use; starting anyway because "
            "--ignore-capture-lock was given. Two daemons writing one capture will collide "
            "on row ids.",
            flush=True,
        )
    # Everything from the pid claim onward runs inside the try: an exception in the
    # sim start or app construction must still reach the finally, or the pid record
    # (claimed first) would be left stranded and `mcu daemon stop` would signal
    # whatever process later recycles the pid.
    pid_path = None
    sim_shutdown = None
    try:
        # The pid record is written here, not only by `mcu daemon start`, so `mcu daemon
        # stop` works however the daemon was launched - including under a windowless
        # interpreter where it is the only stop path there is (see pidfile.py).
        pid_path = pidfile.claim(config.server.host, config.server.port)
        _release_pid_on_terminating_signal(pid_path)
        if args.sim:
            sim_shutdown = _start_sim(config)
        # POST /shutdown ends the process by raising SIGTERM in-process: uvicorn's
        # handler runs the graceful shutdown, then replays the signal into
        # _release_pid_on_terminating_signal above. In-process raise works on Windows
        # too (signal.signal supports SIGTERM there for exactly this delivery), which
        # is what makes /shutdown the one graceful stop that crosses console
        # boundaries. Only the real daemon wires this; create_app defaults to refusing.
        app = create_app(
            config, config_path=cfg_path,
            shutdown_cb=lambda: signal.raise_signal(signal.SIGTERM),
        )
        url = _ui_url(config)
        print(f"web UI: {url}", flush=True)
        # On disk too: a start under a windowless interpreter is otherwise invisible
        # (streams on devnull), and the crash log only fires on an exception.
        _stdio.write_startup_log(
            "mcuscoped",
            f"mcuscoped {__version__} started, pid {os.getpid()}\n"
            f"web UI: {url}\n"
            f"to stop: mcu daemon stop    (or: taskkill /PID {os.getpid()} /F, "
            f"kill {os.getpid()})\n"
            + _stdio.interpreter_report() + "\n",
        )
        if args.open:
            # uvicorn.run blocks, so the browser launch rides a short daemon timer; by the
            # time it fires the server is listening (and if startup failed, the tab simply
            # shows the offline page).
            timer = threading.Timer(1.0, webbrowser.open, args=(url,))
            timer.daemon = True
            timer.start()
        uvicorn.run(
            app, host=config.server.host, port=config.server.port, log_level="warning",
            # Explicit so uvicorn never probes sys.stdout.isatty() itself: that probe
            # crashed the whole daemon on interpreters that start with null std streams.
            use_colors=False,
            # Without this, shutdown waits for every in-flight request, and /wait, /cmd and
            # /assert legitimately hold a request open for up to MAX_TIMEOUT_MS (5 minutes).
            # A single `mcu wait --timeout 300` made Ctrl-C look hung for that long with no
            # message, and the impatient second Ctrl-C is a force-exit that cancels the store
            # writer and drops queued rows. Bound the wait so the lifespan finaliser (port
            # stop, session close, store flush) always gets to run.
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
        )
    finally:
        if sim_shutdown is not None:
            sim_shutdown()
        lock.release()
        pidfile.release(pid_path)
    return 0


def console_entry() -> int:
    """Console-script entry: repaired std streams plus a crash-file backstop."""
    return _stdio.console_entry(main, "mcuscoped")


if __name__ == "__main__":
    raise SystemExit(console_entry())
