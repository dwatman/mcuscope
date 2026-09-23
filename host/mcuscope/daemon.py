"""mcuscoped entry point: parse args, load config, run the app under uvicorn.

The app itself (endpoints, lifespan, port/store wiring) lives in server.py. This
module is just the process entry: it resolves configuration (SPEC 3.3) and hands the
app to uvicorn. The default bind is 127.0.0.1; non-loopback binds are supported for
LAN use and should set an access token via MCUSCOPED_TOKEN or --token (a loud
warning is printed otherwise; the token is runtime-only, never a config key).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn

from . import __version__, _stdio, pidfile, pjstream
from .config import (
    Config,
    ConfigError,
    PortConfig,
    check_host,
    default_config_path,
    load_config,
    resolve_db_path,
)
from .lockfile import CaptureLock, LockError
from .protocol import int_arg
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
        # Bounded like the config key (config._as_int): 0 and 99999 fail here, not in the bind.
        "--port", type=int_arg(1, 65535), metavar="PORT", help="Override server.port from config."
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
        "--plotjuggler",
        "--pj",
        nargs="?",
        const="",
        default=None,
        metavar="HOST:PORT",
        help="Stream decoded plot points to PlotJuggler over UDP (SPEC 3.7). "
        "Wins over the [plotjuggler] config table; with no value, uses the "
        "configured (or default 127.0.0.1:9870) destination.",
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
    if args.host is not None:
        # `is not None`, like --port below: `--host ""` is a typo, not "leave it unset".
        try:
            config.server.host = check_host(args.host)
        except ValueError as exc:
            raise ConfigError(f"--host {exc}") from None
    if args.port is not None:
        config.server.port = args.port
    if args.plotjuggler is not None:
        # Same early bound as --port: a bad destination should fail at the flag, not as
        # a warning buried in the log once the first plot line arrives.
        if args.plotjuggler:
            try:
                pjstream.parse_dest(args.plotjuggler)
            except ValueError as exc:
                raise ConfigError(f"--plotjuggler: {exc}") from None
            config.plotjuggler.dest = args.plotjuggler
        config.plotjuggler.enabled = True
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
    """Point the config's `sim` port at the bundled simulator, and return its link opener.

    The demo used to talk to itself over a loopback TCP socket: an ephemeral listener, an
    accept loop and a serving thread, all so the reader thread could reach a simulator in
    the same process. It opens a link straight onto the simulator core instead, so there is
    nothing to bind, nothing to collide with a standalone `mcu-sim`, and nothing to shut
    down. `mcu-sim` still serves TCP for attaching from elsewhere.
    """
    from . import sim as mcu_sim  # local import: the demo path should not tax normal startup
    from .link import open_link

    sim_args = mcu_sim.build_parser().parse_args(["--demo"])  # one chart, digital, 4 CAN ids
    config.ports = [pc for pc in config.ports if pc.alias != "sim"]
    config.ports.append(PortConfig(alias="sim", device="sim://demo", autoconnect=True))

    # Dispatch on the device, because this opener is handed to EVERY port the daemon
    # builds, not just the demo one. Answering unconditionally meant `--sim` alongside a
    # configured real board reported that board connected and answered its commands out of
    # the simulator - fabricated data under a real alias, with nothing to see it by. The
    # loopback-socket version was scoped by accident: only the sim port held a socket:// URL.
    def opener(device: str, baud: int):
        if device.startswith("sim://"):
            return mcu_sim.open_sim_link(device, baud, sim_args)
        return open_link(device, baud)

    return opener


def _files_notice(cfg_path: str | None, config: Config) -> str:
    """Name the config file and capture database this run uses.

    A missing default file is not an error (Settings can create it); a named one is refused
    before this runs.
    """
    cfg_file = Path(cfg_path) if cfg_path else default_config_path()
    cfg_line = (f"config: {cfg_file}" if cfg_file.exists()
                else f"config: {cfg_file} not found, using defaults")
    return f"{cfg_line}\ndatabase: {resolve_db_path(config)}"


def _ui_url(config: Config) -> str:
    # A wildcard bind is not a connectable address; show the loopback URL instead.
    host = config.server.host
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    elif ":" in host:  # bare IPv6 address needs brackets in a URL
        host = f"[{host}]"
    return f"http://{host}:{config.server.port}/ui/"


def _port_conflict(host: str, port: int) -> str | None:
    """Detect an address already in use, before startup has done anything with side effects.

    The probe exists for ordering, not for detection: POSIX reports the collision by
    itself, but only from inside uvicorn.run(), which is *after* pidfile.claim() - so a
    second daemon that fails to bind took over the running daemon's pid record on the way
    in and deleted it on the way out, leaving the first daemon running and unstoppable by
    `mcu daemon stop`. Finding the conflict up here keeps that failure side-effect-free.

    On Windows detection is the point as well. uvicorn's default path is
    loop.create_server(host=, port=), and asyncio sets SO_REUSEADDR there only on POSIX
    (the unconditional SO_REUSEADDR is uvicorn's --workers/--fd path) - but a Windows bind
    can still succeed against an address another process is actively listening on when
    that process set SO_REUSEADDR, and the second daemon then prints its web UI URL and is
    never reached. SO_EXCLUSIVEADDRUSE on the probe refuses that bind; a plain bind is
    enough on POSIX. The capture lock catches the common double-start (same db_path).

    Every resolved address is probed, not just the first: uvicorn binds them all, so a
    conflict on a later address of a multi-homed `--host <name>` would otherwise slip past
    into exactly the mid-startup failure this exists to prevent. Returns a message, or
    None if the port is free. Each probe socket is closed again before uvicorn binds: it
    never accepted a connection, so there is no TIME_WAIT to trip over, and the gap
    between the two binds is not a race worth worrying about next to the failure it
    removes.
    """
    import socket

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return None   # unresolvable: let uvicorn produce the real error
    for family, socktype, proto, _canon, addr in infos:
        try:
            probe = socket.socket(family, socktype, proto)
        except OSError:
            continue  # address family unavailable here; uvicorn will hit the same wall
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):   # Windows only
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                # POSIX: match what asyncio does for uvicorn's bind (see above), or the probe
                # answers a stricter question than the one being asked. Without this a plain
                # bind also fails on lingering TIME-WAIT sockets, so for ~60 s after a clean
                # stop a restart was refused with "another mcuscoped is listening there" when
                # nothing was listening at all.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(addr)
        except OSError as exc:
            return (
                f"{host}:{port} is already in use ({exc.strerror or exc}). Another mcuscoped "
                "or another service is listening there; stop it, or start this one on a "
                "different port with --port."
            )
        finally:
            probe.close()
    return None


class Server(uvicorn.Server):
    """uvicorn's server with the long polls woken at the start of shutdown.

    `handle_exit` is the one hook both stop paths share (SIGTERM, and /shutdown, which
    raises it). The store's own `stop()` runs in the lifespan finaliser, after the graceful
    wait has cancelled every parked `/wait` and `/assert` into a 500; the sentinel has to go
    out before that wait begins so they answer 503 instead.
    """

    def handle_exit(self, sig, frame) -> None:
        store = getattr(getattr(self.config.app, "state", None), "store", None)
        if store is not None:
            # Scheduled, not called: as a signal handler this runs between two bytecodes of
            # whatever the loop is doing, possibly the fan-out's put on the same queue.
            # The graceful wait starts on a later tick, so the sentinel still goes first.
            asyncio.get_running_loop().call_soon_threadsafe(store.stop_subscribers)
        super().handle_exit(sig, frame)


class _FirstError(logging.Handler):
    """Keeps the last line of the first error uvicorn logs: the exception line of a
    lifespan traceback, or the bind's OSError."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.reason: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        lines = [ln for ln in record.getMessage().splitlines() if ln.strip()]
        if self.reason is None and lines:
            self.reason = lines[-1].strip()


def _serve(app: Any, **kw: Any) -> None:
    """`uvicorn.run`'s single-worker path with `Server` above: the Ctrl-C swallow and the
    startup-failed exit code kept, so `mcuscoped` exits 3 on a bind failure as before.
    A start that never reached `started` rewrites the startup log to say so."""
    server = Server(uvicorn.Config(app, **kw))
    # Added after uvicorn.Config, whose logging setup would otherwise drop it.
    errors = _FirstError()
    uvicorn_log = logging.getLogger("uvicorn.error")
    uvicorn_log.addHandler(errors)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    except SystemExit:
        # uvicorn's own startup-failure exits (lifespan or bind) raise out of run().
        if server.started:
            raise
    finally:
        uvicorn_log.removeHandler(errors)
    if not server.started:
        code = 3   # uvicorn.main.STARTUP_FAILED
        _stdio.write_startup_log(
            "mcuscoped",
            f"mcuscoped {__version__} failed to start, pid {os.getpid()}, exit {code}\n"
            f"reason: {errors.reason or 'unknown (nothing was logged)'}\n"
            + _stdio.interpreter_report() + "\n",
        )
        sys.exit(code)


def _report_key(host: str, port: int, pid_path: str | None) -> str:
    """Key for this process's startup and crash reports: host-port, plus our pid when
    another live process holds that host:port's pid record.

    Two starts on one host:port with different db_paths both pass the port probe; the
    record holder is the one whose reports own the shared name, so the other's "started"
    and "failed to start" cannot overwrite them. A record naming our parent is the Windows
    launcher shim `mcu daemon start` recorded: this process is then the recorded daemon.
    """
    key = f"{host}-{port}"
    if pid_path is not None:
        return key
    try:
        holder = pidfile.read_pid_record(pidfile.pid_file_path(host, port))
    except OSError:
        return key
    if holder is None or holder in (os.getpid(), os.getppid()):
        return key
    return f"{key}-{os.getpid()}" if pidfile.pid_running(holder) else key


def _release_pid_on_terminating_signal(pid_path: str | None) -> None:
    """Make sure the pid record is removed when a signal ends the process.

    uvicorn (Server.capture_signals) handles SIGTERM/SIGBREAK itself, and after its
    graceful shutdown restores the original handlers and REPLAYS the signal, so the
    process dies inside uvicorn.run and main()'s finally never runs. Installing this
    handler first makes it that "original": the replay lands here, the pid record is
    released, and the signal is re-raised with the default disposition so the exit
    code still says what killed us. SIGINT is not needed: Python's default handler
    turns the replay into KeyboardInterrupt, which does unwind through finally.

    SIGHUP (a closed terminal or SSH session) is raised on as SIGTERM, so it gets the
    same graceful shutdown; an ignored SIGHUP (`nohup`) stays ignored.
    """

    def _handler(sig: int, frame: object) -> None:
        pidfile.release(pid_path)
        signal.signal(sig, signal.SIG_DFL)
        signal.raise_signal(sig)

    def _hangup(sig: int, frame: object) -> None:
        signal.raise_signal(signal.SIGTERM)

    handlers = [(signal.SIGTERM, _handler)]
    if hasattr(signal, "SIGBREAK"):  # Windows: what `mcu daemon stop` sends
        handlers.append((signal.SIGBREAK, _handler))
    if hasattr(signal, "SIGHUP"):    # POSIX only
        handlers.append((signal.SIGHUP, _hangup))
    for sig, handler in handlers:
        if signal.getsignal(sig) == signal.SIG_DFL:
            try:
                signal.signal(sig, handler)
            except ValueError:
                # Not the main thread (an embedder calling main() from one): signal
                # registration is unavailable there, for every signal alike, so warn
                # once and stop. The pid record is still released by main()'s finally
                # on a normal exit; only the signal-replay path is lost.
                print("mcuscoped: cannot install signal handlers off the main thread; "
                      "a signal will leave the pid record behind",
                      file=sys.stderr, flush=True)
                break


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg_path = args.config or os.environ.get("MCUSCOPED_CONFIG") or None
    if cfg_path:
        # Absolute from here on: /status reports it, and `mcu daemon restart` (run from any
        # directory) checks that file before stopping this daemon.
        cfg_path = os.path.abspath(os.path.expanduser(cfg_path))
    config_warnings: list[str] = []
    try:
        # A named file must exist; only the default location may be absent (SPEC 3.3).
        if cfg_path and not os.path.exists(cfg_path):
            raise ConfigError(f"no such config file: {cfg_path}")
        config = load_config(cfg_path, warnings=config_warnings)
        # Logged here, once; GET /status carries them (A-5).
        for warning in config_warnings:
            logging.getLogger("mcuscope.config").warning("%s", warning)
        config = _apply_overrides(config, args)
    except ConfigError as exc:
        print(f"mcuscoped: {exc}", file=sys.stderr, flush=True)
        return 1
    _warn_if_exposed(config.server.host, config.server.token)
    files = _files_notice(cfg_path, config)
    print(files, flush=True)
    # Claim the capture before anything opens it. The app lifespan runs before uvicorn
    # binds its port, so checking any later means a doomed second daemon has already
    # written rows into the running one's database.
    lock = CaptureLock(resolve_db_path(config))
    try:
        lock.acquire()
    except LockError as exc:
        if not args.ignore_capture_lock:
            print(f"mcuscoped: {exc}", file=sys.stderr, flush=True)
            return 1
        print(
            f"mcuscoped: WARNING: {exc.path} appears to be in use; starting anyway because "
            "--ignore-capture-lock was given. Two daemons writing one capture will collide "
            "on row ids.",
            file=sys.stderr,
            flush=True,
        )
    except OSError as exc:
        # A data dir that is read-only, full, or on a filesystem without locking: the lock
        # file cannot be created at all. That is a startup failure like any other, not a
        # traceback at the user, and --ignore-capture-lock does not make it survivable.
        print(f"mcuscoped: cannot claim {lock.path}: {exc}", file=sys.stderr, flush=True)
        return 1
    # Everything from the pid claim onward runs inside the try: an exception in the
    # sim start or app construction must still reach the finally, or the pid record
    # (claimed first) would be left stranded and `mcu daemon stop` would signal
    # whatever process later recycles the pid.
    pid_path = None
    open_link_fn = None
    try:
        # After the capture lock, so a plain double-start still gets that richer message
        # (which names the holding pid) rather than this one.
        conflict = _port_conflict(config.server.host, config.server.port)
        if conflict is not None:
            print(f"mcuscoped: {conflict}", file=sys.stderr, flush=True)
            return 1
        # The pid record is written here, not only by `mcu daemon start`, so `mcu daemon
        # stop` works however the daemon was launched - including under a windowless
        # interpreter where it is the only stop path there is (see pidfile.py).
        # Keyed like the pid record, and from here on: a second daemon on another port
        # must not overwrite this one's startup log or crash log, which for a windowless
        # start are the only trace it leaves anywhere.
        _stdio.set_report_key(f"{config.server.host}-{config.server.port}")
        pid_path = pidfile.claim(config.server.host, config.server.port)
        _stdio.set_report_key(_report_key(config.server.host, config.server.port, pid_path))
        _release_pid_on_terminating_signal(pid_path)
        if args.sim:
            open_link_fn = _start_sim(config)
        # POST /shutdown ends the process by raising SIGTERM in-process: uvicorn's
        # handler runs the graceful shutdown, then replays the signal into
        # _release_pid_on_terminating_signal above. In-process raise works on Windows
        # too (signal.signal supports SIGTERM there for exactly this delivery), which
        # is what makes /shutdown the one graceful stop that crosses console
        # boundaries. Only the real daemon wires this; create_app defaults to refusing.
        app = create_app(
            config, config_path=cfg_path,
            shutdown_cb=lambda: signal.raise_signal(signal.SIGTERM),
            open_link_fn=open_link_fn, config_warnings=config_warnings,
        )
        url = _ui_url(config)
        print(f"web UI: {url}", flush=True)
        if config.plotjuggler.enabled:
            # argparse abbreviation resolves `--plot` to `--plotjuggler`, so a user who
            # meant "with plots" gets a UDP stream they did not ask for. Naming it here
            # is the only trace it leaves.
            print(
                f"PlotJuggler: streaming plot points to {config.plotjuggler.dest}"
                " (--plotjuggler)",
                flush=True,
            )
        # On disk too: a start under a windowless interpreter is otherwise invisible
        # (streams on devnull), and the crash log only fires on an exception.
        _stdio.write_startup_log(
            "mcuscoped",
            f"mcuscoped {__version__} started, pid {os.getpid()}\n"
            f"web UI: {url}\n"
            f"{files}\n"
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
        _serve(
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
        lock.release()
        pidfile.release(pid_path)
    return 0


def console_entry() -> int:
    """Console-script entry: repaired std streams plus a crash-file backstop."""
    return _stdio.console_entry(main, "mcuscoped")


if __name__ == "__main__":
    raise SystemExit(console_entry())
