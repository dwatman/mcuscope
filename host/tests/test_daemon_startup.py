"""Tests for mcuscoped's pre-startup checks and the in-process simulator.

Both cover failures invisible from inside the daemon: an address conflict the probe missed
and reported only from inside uvicorn.run(), after the pid claim; and the demo simulator,
which used to be reached over a loopback listener and is now a link.
"""

from __future__ import annotations

import asyncio
import socket
import threading

import httpx
import pytest

from mcuscope import daemon as daemon_mod
from mcuscope.config import Config, PortConfig
from mcuscope.lockfile import CaptureLock
from mcuscope.server import create_app
from tests.support import UNOPENABLE, free_port


def _ipv6_loopback_available() -> bool:
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    except OSError:
        return False
    try:
        s.bind(("::1", 0))
    except OSError:
        return False
    finally:
        s.close()
    return True


def test_port_conflict_probes_every_resolved_address(monkeypatch) -> None:
    """uvicorn binds every address a host name resolves to, so probing only the first
    let a conflict on a later one through into the mid-startup failure - after
    pidfile.claim() - that the probe exists to prevent."""
    if not _ipv6_loopback_available():
        pytest.skip("no IPv6 loopback here, so a two-address host cannot be simulated")
    port = free_port()
    # What a dual-stack `--host <name>` resolves to: IPv6 first, IPv4 second.
    infos = [
        (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::1", port, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: infos)

    busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        busy.bind(("127.0.0.1", port))
        busy.listen(1)

        msg = daemon_mod._port_conflict("dual-stack.example", port)
        assert msg is not None and "already in use" in msg
    finally:
        busy.close()

    # And with nothing bound on either address the probe stays silent.
    assert daemon_mod._port_conflict("dual-stack.example", port) is None


def test_a_startup_failure_after_the_claim_leaves_no_pid_record(tmp_path, monkeypatch) -> None:
    """Everything after the pid claim runs inside the try, so a failure there still
    reaches the finally: a stranded record would have `mcu daemon stop` signal whatever
    process later recycles the pid, and a stranded lock would need clearing by hand."""
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    monkeypatch.setattr(
        daemon_mod, "create_app", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    (tmp_path / "empty.toml").touch()   # a named config must exist
    with pytest.raises(RuntimeError):
        daemon_mod.main(["-c", str(tmp_path / "empty.toml"), "--port", str(free_port())])

    assert not list((tmp_path / "data").glob("*.pid")), "a pid record outlived the daemon"
    released = CaptureLock(str(tmp_path / "data" / "capture.db"))
    released.acquire(timeout=0)   # raises LockError if the capture was left claimed
    released.release()


async def test_the_sim_demo_binds_nothing_and_still_captures(tmp_path) -> None:
    """`--sim` reaches the simulator through a link, not a loopback socket.

    It used to open an ephemeral listener and connect to itself, which is where the
    healthy-while-dead failure came from: a listener left bound with no thread behind it
    keeps completing handshakes, so the daemon reconnects to a corpse and reports the port
    healthy. There is nothing to bind now, so that failure mode is gone rather than
    guarded - `spawn()` keeps its own test of the invariant, for standalone `mcu-sim`.

    The configured board beside the demo port is the other half: the opener is handed to
    every port the daemon builds, and answering unconditionally served a real board's
    alias out of the simulator - fabricated data under a real name.
    """
    config = Config()
    config.storage.db_path = str(tmp_path / "capture.db")
    config.ports.append(PortConfig(alias="board", device=UNOPENABLE, autoconnect=True))
    open_link_fn = daemon_mod._start_sim(config)

    sim_port = next(pc for pc in config.ports if pc.alias == "sim")
    assert sim_port.device == "sim://demo", "the demo went back to a socket"
    assert not any(t.name == "mcu-sim" for t in threading.enumerate()), \
        "the demo started a serving thread"

    app = create_app(config, open_link_fn=open_link_fn)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        board_ever_connected = False
        for _ in range(200):
            ports = {p["alias"]: p for p in (await client.get("/status")).json()["ports"]}
            port = ports["sim"]
            board_ever_connected |= ports["board"]["connected"]
            if port["connected"] and port["lines_rx"]:
                break
            await asyncio.sleep(0.02)
        assert port["connected"] and port["lines_rx"], "the demo port never captured"
        assert not board_ever_connected, "the configured board was served out of the simulator"
        assert ports["board"]["connected"] is False
        assert ports["board"]["lines_rx"] == 0, "the board's capture came from the simulator"


def test_a_lingering_time_wait_socket_is_not_a_port_conflict() -> None:
    """The probe must answer the same question uvicorn's bind will, not a stricter one.

    asyncio sets SO_REUSEADDR for uvicorn on POSIX, so uvicorn binds happily over the
    TIME-WAIT sockets a just-stopped daemon leaves behind. The probe did a plain bind, so for
    roughly a minute after a clean stop it refused to start and said "another mcuscoped or
    another service is listening there" while nothing was listening at all.
    """
    from mcuscope.daemon import _port_conflict

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = srv.accept()
    # Close the server side first, which is what leaves TIME-WAIT on the listening port.
    conn.close()
    srv.close()
    client.close()
    try:
        # The wildcard, which is what the daemon binds and what actually fails: a plain
        # bind of 0.0.0.0:P is refused while 127.0.0.1:P sits in TIME-WAIT, though binding
        # 127.0.0.1:P itself would have succeeded. The real report came from `--host 0.0.0.0`.
        assert _port_conflict("0.0.0.0", port) is None, (
            "a TIME-WAIT socket was reported as another process listening"
        )
    finally:
        client.close()


def test_a_live_listener_is_still_a_port_conflict() -> None:
    # The other half: SO_REUSEADDR must not blind the probe to a real one.
    from mcuscope.daemon import _port_conflict

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)
    try:
        msg = _port_conflict("127.0.0.1", port)
        assert msg is not None and "already in use" in msg
    finally:
        srv.close()


def _startup_output(tmp_path, monkeypatch, capsys, extra: list[str]) -> str:
    """Run main() up to the point uvicorn would block, returning what it printed."""
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    monkeypatch.setattr(daemon_mod, "_serve", lambda *a, **kw: None)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "empty.toml").touch()   # a named config must exist
    rc = daemon_mod.main(
        ["-c", str(tmp_path / "empty.toml"), "--port", str(free_port()), *extra]
    )
    assert rc in (0, None), rc
    return capsys.readouterr().out


def test_startup_names_the_plotjuggler_destination(tmp_path, monkeypatch, capsys) -> None:
    """argparse abbreviation turns `--plot` into `--plotjuggler`, so a user who meant
    "with plots" gets a UDP stream to 127.0.0.1:9870 instead. The startup output is the
    only place that can say so, and it must stay silent when nothing is being streamed."""
    quiet = _startup_output(tmp_path / "off", monkeypatch, capsys, [])
    assert "web UI:" in quiet, quiet
    # The whole address: a bare "9870" also matches a free_port() such as 39870.
    assert "PlotJuggler" not in quiet and "127.0.0.1:9870" not in quiet, quiet

    # The abbreviation itself, not the full flag: this is the line that caused the surprise.
    loud = _startup_output(tmp_path / "on", monkeypatch, capsys, ["--plot"])
    assert "127.0.0.1:9870" in loud, loud
    assert "--plotjuggler" in loud, loud

    # A destination given explicitly is the one named, not the default.
    named = _startup_output(tmp_path / "dest", monkeypatch, capsys, ["--pj", "10.0.0.5:9999"])
    assert "10.0.0.5:9999" in named and "--plotjuggler" in named, named


def _startup_with_config(tmp_path, monkeypatch, capsys, argv: list[str]) -> str:
    monkeypatch.setattr(daemon_mod, "_serve", lambda *a, **kw: None)
    rc = daemon_mod.main([*argv, "--port", str(free_port())])
    assert rc in (0, None), rc
    return capsys.readouterr().out


def test_startup_names_the_config_it_read_and_its_database(tmp_path, monkeypatch, capsys) -> None:
    cfg = tmp_path / "bench.toml"
    db = tmp_path / "bench.db"
    cfg.write_text(f'[storage]\ndb_path = "{db.as_posix()}"\n', encoding="utf-8", newline="\n")
    out = _startup_with_config(tmp_path, monkeypatch, capsys, ["-c", str(cfg)])
    assert f"config: {cfg}\n" in out, out
    assert "not found" not in out, out
    assert f"database: {db.as_posix()}\n" in out, "the configured db_path, not the default"


class _Spawned(Exception):
    """Raised by the fake Popen: the start got as far as spawning the daemon."""


@pytest.mark.parametrize("via", ["option", "env", "present"])
def test_daemon_start_refuses_a_named_config_that_is_missing(
    tmp_path, monkeypatch, capsys, via: str
) -> None:
    """`mcu daemon start -c typo.toml` refuses before spawning, as mcuscoped does."""
    from mcuscope import cli

    cfg = tmp_path / "typo.toml"
    if via == "present":
        cfg.write_text("", encoding="utf-8", newline="\n")
    argv = ["--url", "http://127.0.0.1:1", "daemon", "start"]
    if via == "env":
        monkeypatch.setenv("MCUSCOPED_CONFIG", str(cfg))
    else:
        argv += ["-c", str(cfg)]

    def fake_popen(args, **kwargs):
        raise _Spawned(args)

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    if via == "present":
        with pytest.raises(_Spawned):
            cli.main(argv)
        assert "no such config file" not in capsys.readouterr().err
        return
    assert cli.main(argv) == 1   # no _Spawned: nothing was started
    assert f"no such config file: {cfg}" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["١٨٦١٣", " 8558", "+8558", "8_558", "0", "65536", "-1"])
def test_daemon_port_flag_is_on_the_grammar_and_bounded(value, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        daemon_mod.build_parser().parse_args([f"--port={value}"])
    assert exc.value.code == 2
    assert "argument --port: " in capsys.readouterr().err
    assert daemon_mod.build_parser().parse_args(["--port", "8558"]).port == 8558


def test_port_already_in_use_is_refused() -> None:
    """uvicorn's unconditional SO_REUSEADDR means Windows lets a second daemon bind a
    port that is already being listened on, so it started, printed its URL and was never
    reached.

    The probe now runs on POSIX too. The kernel does refuse the bind there by itself, but
    only once uvicorn.run() reaches it - which is after pidfile.claim(), so the second
    daemon took over the first's pid record and deleted it on its way out. See
    test_port_conflict_is_detected_on_every_platform.
    """
    import socket

    from mcuscope.daemon import _port_conflict

    held = socket.socket()
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", 0))
    port = held.getsockname()[1]
    held.listen(5)
    try:
        conflict = _port_conflict("127.0.0.1", port)
        assert conflict is not None and str(port) in conflict
    finally:
        held.close()
    # A free port is never reported, on either platform.
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    free_port = free.getsockname()[1]
    free.close()
    assert _port_conflict("127.0.0.1", free_port) is None


def test_daemon_declines_to_start_on_a_taken_port(tmp_path, monkeypatch, capsys) -> None:
    """The conflict has to end the start, not just be noticed."""
    from mcuscope import daemon as daemon_mod

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[server]\nhost = "127.0.0.1"\nport = 8558\n\n'
        f'[storage]\ndb_path = {str(tmp_path / "capture.db")!r}\n',
        encoding="utf-8", newline="\n",
    )
    monkeypatch.setattr(daemon_mod, "_port_conflict", lambda h, p: "127.0.0.1:8558 is busy")
    monkeypatch.setattr(
        daemon_mod, "_serve",
        lambda *a, **k: pytest.fail("uvicorn must not be reached on a port conflict"),
    )
    assert daemon_mod.main(["--config", str(cfg)]) == 1
    assert "is busy" in capsys.readouterr().err   # startup refusals go to stderr


# -- August 2026 review pass ----------------------------------------------------------


def test_port_conflict_is_detected_on_every_platform() -> None:
    """POSIX skipped the probe, so a failing second daemon deleted the first's pid record.

    uvicorn only reports EADDRINUSE from inside run(), which is after pidfile.claim(): the
    second daemon claimed the record, failed to bind, and removed it on the way out.
    """
    import socket

    from mcuscope.daemon import _port_conflict

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    busy = listener.getsockname()[1]
    try:
        msg = _port_conflict("127.0.0.1", busy)
        assert msg is not None and str(busy) in msg
    finally:
        listener.close()
    # The same port, once released, must read as free (no leaked probe socket either).
    assert _port_conflict("127.0.0.1", busy) is None


def test_a_lock_dir_that_cannot_be_written_is_a_startup_failure(tmp_path, monkeypatch,
                                                                capsys) -> None:
    """Only LockError was handled, so a read-only or full data dir left an OSError
    traceback at the user instead of the one-line startup failure every other cause gets."""
    from mcuscope import daemon as daemon_mod
    from mcuscope.lockfile import CaptureLock

    def raise_oserror(self, timeout: float = 2.0) -> None:
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(CaptureLock, "acquire", raise_oserror)
    (tmp_path / "empty-config.toml").touch()   # a named config must exist
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(tmp_path / "empty-config.toml"))
    assert daemon_mod.main(["--port", "8558"]) == 1
    assert capsys.readouterr().err.startswith("mcuscoped: cannot claim ")


def test_port_override_is_bounded_like_the_config_key(tmp_path, monkeypatch, capsys) -> None:
    """`--port 99999` bypassed the 1..65535 bound the config file gets and failed much
    later, from inside the bind, naming neither the flag nor the reason."""
    from mcuscope import daemon as daemon_mod

    (tmp_path / "empty-config.toml").touch()   # a named config must exist
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(tmp_path / "empty-config.toml"))
    # 0 is the trap: a truthiness guard reads it as "no override" and starts on the
    # config port, refusing nothing.
    for bad in ("99999", "0", "-1"):
        with pytest.raises(SystemExit) as exc:
            daemon_mod.main(["--port", bad])
        assert exc.value.code == 2
        assert "argument --port: must be 1..65535" in capsys.readouterr().err


# -- F11: startup refusals go to stderr ------------------------------------------------


def test_a_config_refusal_prints_to_stderr(tmp_path, capsys) -> None:
    """`mcuscoped >/dev/null` (a wrapper script, a unit file) discarded every refusal,
    leaving a bare exit 1."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[server]\nport = "abc"\n', encoding="utf-8", newline="\n")
    assert daemon_mod.main(["-c", str(cfg)]) == 1
    cap = capsys.readouterr()
    assert "mcuscoped:" in cap.err and "port" in cap.err
    assert "mcuscoped:" not in cap.out


def test_a_port_conflict_prints_to_stderr(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    monkeypatch.setattr(daemon_mod, "_port_conflict", lambda host, port: "127.0.0.1:1 is in use")
    (tmp_path / "empty.toml").touch()   # a named config must exist
    assert daemon_mod.main(["-c", str(tmp_path / "empty.toml"), "--port", "1"]) == 1
    cap = capsys.readouterr()
    assert "is in use" in cap.err
    assert "is in use" not in cap.out


def test_an_empty_host_override_is_refused(tmp_path, monkeypatch, capsys) -> None:
    """`if args.host:` swallowed `--host ""`, while `--port 0` is refused: same flag pair,
    two rules."""
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    # The refusal has to happen before anything binds: without it the daemon started on the
    # configured host instead, which is the silent half of the defect.
    monkeypatch.setattr(
        daemon_mod, "_serve",
        lambda *a, **kw: pytest.fail("an empty --host was taken as no override"),
    )
    (tmp_path / "empty.toml").touch()   # a named config must exist
    for bad in ("", "   "):
        assert daemon_mod.main(["-c", str(tmp_path / "empty.toml"), "--host", bad]) == 1
        assert "--host must be" in capsys.readouterr().err


def test_signal_registration_off_the_main_thread_is_survivable(capsys, monkeypatch) -> None:
    """signal.signal raises ValueError off the main thread, between the pid claim and the
    try that owns the release: an embedder calling main() lost the record."""
    import signal

    # A previous test in the same process may have left a handler installed, which would
    # skip the registration this test exists to drive.
    monkeypatch.setattr(signal, "getsignal", lambda sig: signal.SIG_DFL)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            daemon_mod._release_pid_on_terminating_signal(None)
        except BaseException as exc:   # noqa: BLE001 - the point of the test
            errors.append(exc)

    t = threading.Thread(target=run)
    t.start()
    t.join()
    assert errors == [], f"signal registration escaped off the main thread: {errors}"
    assert "main thread" in capsys.readouterr().err
