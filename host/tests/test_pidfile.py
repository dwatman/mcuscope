"""Tests for mcuscope.pidfile: the daemon-side pid record.

Regression coverage for the unstoppable-daemon bug: only `mcu daemon start` wrote
the pid file, so a daemon launched as plain `mcuscoped` could not be stopped with
`mcu daemon stop` - which, on a windowless Windows interpreter with no Ctrl-C,
left no stop path at all.
"""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from mcuscope import pidfile
from mcuscope.pidfile import read_pid_record


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "platformdirs.user_data_dir", lambda app: str(tmp_path / "data" / app)
    )
    return tmp_path / "data" / "mcuscope"


def test_pid_file_path_is_keyed_and_filename_safe(data_dir):
    path = pidfile.pid_file_path("127.0.0.1", 8558)
    assert path == str(data_dir / "mcuscoped-127.0.0.1-8558.pid")
    # An IPv6 literal must key a file too: colons cannot appear in the name.
    assert ":" not in os.path.basename(pidfile.pid_file_path("::1", 8558))


def test_claim_writes_own_pid_and_release_removes_it(data_dir):
    path = pidfile.claim("127.0.0.1", 8770)
    assert path is not None
    with open(path, encoding="utf-8") as fh:
        assert int(fh.read()) == os.getpid()
    pidfile.release(path)
    assert not os.path.exists(path)


def test_claim_does_not_clobber_a_live_record(data_dir):
    """`mcu daemon start` records the launcher pid it spawned; the daemon must not
    replace a live record with its own (on Windows that pid is the CTRL_BREAK
    process-group id, and replacing it downgrades a graceful stop to a kill)."""
    path = pidfile.pid_file_path("127.0.0.1", 8771)
    live = os.getppid()  # a real, running process that is not us
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(str(live))

    assert pidfile.claim("127.0.0.1", 8771) is None
    with open(path, encoding="utf-8") as fh:
        assert int(fh.read()) == live  # untouched


def test_a_record_that_is_not_utf8_is_malformed_and_claim_replaces_it(data_dir, tmp_path):
    """An undecodable record reads as no pid instead of raising out of every reader.

    UTF-16LE "12" is what PowerShell 5's `>` writes; it used to crash daemon startup
    in claim() and `mcu daemon stop` with a UnicodeDecodeError (registry R13-1).
    """
    path = str(tmp_path / "rec.pid")
    for bad in (b"\xff\xfe1\x002\x00", b"12\xff"):
        with open(path, "wb") as fh:
            fh.write(bad)
        assert pidfile.read_pid_record(path) is None, f"accepted {bad!r}"

    rec = pidfile.pid_file_path("127.0.0.1", 8773)
    with open(rec, "wb") as fh:
        fh.write(b"\xff\xfe1\x002\x00")
    assert pidfile.claim("127.0.0.1", 8773) == rec
    assert read_pid_record(rec) == os.getpid()


def test_claim_overwrites_a_stale_record(data_dir):
    path = pidfile.pid_file_path("127.0.0.1", 8772)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("999999999")  # far beyond any real pid space

    assert pidfile.claim("127.0.0.1", 8772) == path
    with open(path, encoding="utf-8") as fh:
        assert int(fh.read()) == os.getpid()


def test_claim_leaves_alone_a_live_record_that_is_not_our_parent(data_dir):
    """A live record is never taken over, whoever owns it.

    This used to overwrite, on the argument that a live pid which is neither us nor our
    parent must be a recycled pid in a crashed daemon's record. The port probe cannot
    support that argument: it closes long before either daemon binds, so two daemons with
    different db_path (the capture lock does not stop the second) on one host:port both
    pass it. The loser of the bind race then took the winner's record on the way in and
    deleted it on the way out, leaving a live daemon `mcu daemon stop` could not find.

    The recycled-pid case is covered from the other side instead: `mcu daemon stop` acts
    on the pid /status reports rather than the recorded one, and signals nothing when no
    daemon answers, so it neither misses the live daemon nor kills the innocent process
    wearing its old pid.
    """
    path = pidfile.pid_file_path("127.0.0.1", 8776)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(str(child.pid))

        assert pidfile.claim("127.0.0.1", 8776) is None
        with open(path, encoding="utf-8") as fh:
            assert int(fh.read()) == child.pid  # untouched
        # And the unrecorded daemon's release() must not delete the record either.
        pidfile.release(path)
        assert os.path.exists(path)
    finally:
        child.kill()
        child.wait(timeout=10)


def test_claim_twice_from_the_same_process(data_dir):
    """A reclaim of our own record (e.g. after a restart on the same key) must not
    trip over the O_EXCL create: the first file is ours, so it is removed and
    recreated."""
    path = pidfile.claim("127.0.0.1", 8777)
    assert path is not None
    assert pidfile.claim("127.0.0.1", 8777) == path
    with open(path, encoding="utf-8") as fh:
        assert int(fh.read()) == os.getpid()


def test_claim_reclaims_our_own_record(data_dir):
    """POSIX `daemon start`: Popen pid == the daemon's own pid, so the record the
    parent just wrote is ours to keep owning (and to remove on exit)."""
    path = pidfile.pid_file_path("127.0.0.1", 8773)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(str(os.getpid()))

    assert pidfile.claim("127.0.0.1", 8773) == path


def test_release_keeps_a_record_someone_else_rewrote(data_dir):
    path = pidfile.claim("127.0.0.1", 8774)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("12345")
    pidfile.release(path)
    assert os.path.exists(path)
    pidfile.release(None)  # no claim: must be a no-op, not an error


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is TerminateProcess on Windows")
def test_daemon_releases_pid_file_on_sigterm(tmp_path):
    """uvicorn replays a handled SIGTERM with the default disposition after its
    graceful shutdown, so the process dies inside uvicorn.run and main()'s finally
    never runs - the pre-installed handler must release the pid record instead."""
    from tests.support import free_port

    port = free_port()
    from tests.support import child_env

    env = child_env(str(tmp_path), XDG_CONFIG_HOME=str(tmp_path), MCUSCOPED_CONFIG="")
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcuscope.daemon", "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    pid_file = tmp_path / "mcuscope" / f"mcuscoped-127.0.0.1-{port}.pid"
    # Keyed by host:port like the pid record beside it: two daemons must not share one
    # startup log (see test_stdio.test_report_key_is_per_daemon).
    startup_log = tmp_path / "mcuscope" / f"mcuscoped-127.0.0.1-{port}-startup.log"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert proc.poll() is None, "daemon exited before writing its pid file"
            # Readiness is the *content*, not file existence: open("w") creates the
            # file before anything lands in it, so an existence check can win the
            # race against a partial write.
            try:
                ready = (
                    pid_file.read_text(encoding="utf-8") == str(proc.pid)
                    and "to stop" in startup_log.read_text(encoding="utf-8")
                )
            except OSError:
                ready = False
            if ready:
                break
            time.sleep(0.05)
        assert pid_file.read_text(encoding="utf-8") == str(proc.pid)
        assert "to stop" in startup_log.read_text(encoding="utf-8")

        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == -signal.SIGTERM
        assert not pid_file.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_read_pid_record_takes_only_ascii_decimal(data_dir, tmp_path):
    """The record's grammar is [0-9]+, not "whatever int() swallows" (review class 22).

    `٣` (U+0663) is the discriminating input: `'٣'.isdecimal()` is True and `int('٣')`
    is 3, so a garbled record used to read as pid 3 - a live process on any Linux box,
    which makes claim() refuse to record and leaves the daemon unrecorded.
    """
    path = str(tmp_path / "rec.pid")
    for bad in ("٣", "+17", "1_17", "-1", "", "  ", "8558x", "1" * 30, "1.0"):
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(bad)
        assert pidfile.read_pid_record(path) is None, f"accepted {bad!r}"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(" 8558\n")   # surrounding whitespace is still a valid record
    assert pidfile.read_pid_record(path) == 8558
    assert pidfile.read_pid_record(str(tmp_path / "absent.pid")) is None


def test_claim_removes_the_record_when_the_pid_write_fails(data_dir, monkeypatch):
    """A failed write must not leave an empty record behind.

    The create and the write are two syscalls; a full disk between them used to leave a
    zero-byte record that names no process, which the next claimer can only treat as
    stale - and which `daemon stop` called corrupt.

    Windows also refuses to unlink a file that is still open, so the removal only works
    once the descriptor is closed. POSIX does not care, which is why the Windows CI leg
    was the only one to see the empty record survive; the os.remove below fails while an
    fd is open so this leg carries the same rule.
    """
    real_write, real_open, real_close, real_remove = os.write, os.open, os.close, os.remove
    open_paths: dict[int, str] = {}

    def full_disk(fd, data):
        if data == str(os.getpid()).encode("ascii"):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(fd, data)

    def tracking_open(p, *args, **kw):
        fd = real_open(p, *args, **kw)
        open_paths[fd] = os.fspath(p)
        return fd

    def tracking_close(fd):
        open_paths.pop(fd, None)
        return real_close(fd)

    def windows_remove(p):
        if os.fspath(p) in open_paths.values():
            raise PermissionError(errno.EACCES, "The process cannot access the file")
        return real_remove(p)

    monkeypatch.setattr(os, "write", full_disk)
    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "close", tracking_close)
    monkeypatch.setattr(os, "remove", windows_remove)
    path = pidfile.pid_file_path("127.0.0.1", 8781)
    assert pidfile.claim("127.0.0.1", 8781) is None
    assert not os.path.exists(path), "an empty pid record was left behind"


def test_claim_does_not_take_a_record_another_claimer_is_still_writing(data_dir):
    """The empty half of another claimer's create-then-write window is not "stale".

    Reading the record once let a second daemon see the empty file, call it stale,
    remove it and claim - so both daemons believed they owned the record and the one
    whose file was unlinked ended up unrecorded.
    """
    path = pidfile.pid_file_path("127.0.0.1", 8782)
    other = os.getppid()   # a real, running process that is not us
    with open(path, "w", encoding="utf-8", newline="") as fh:
        pass               # created, not yet written: exactly the claimer's window

    def finish_the_write() -> None:
        time.sleep(pidfile.CLAIM_SETTLE_S / 5)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(str(other))

    writer = threading.Thread(target=finish_the_write)
    writer.start()
    try:
        assert pidfile.claim("127.0.0.1", 8782) is None
    finally:
        writer.join(timeout=5)
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == str(other)   # the live claimer's record, untouched


def test_pid_running_probes_without_signalling():
    assert pidfile.pid_running(os.getpid()) is True
    assert pidfile.pid_running(999999999) is False
    assert pidfile.pid_running(0) is False
    assert pidfile.pid_running(-1) is False


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="the zombie test reads /proc")
def test_an_unreaped_child_is_not_running():
    """os.kill(pid, 0) succeeds for a zombie, so a daemon launched by a script that never
    reaps it read as alive after it had exited: `mcu daemon stop` then waited out its whole
    grace period and reported failure after a shutdown that had worked."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and pidfile.pid_running(child.pid):
            time.sleep(0.02)
        assert not pidfile.pid_running(child.pid), "an exited, unreaped child reads as running"
    finally:
        child.wait(timeout=10)


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX signal-permission branch")
def test_a_process_we_may_not_signal_is_still_running(monkeypatch):
    """A daemon started by another user (or elevated) answers EPERM, not ESRCH. Reading
    that as "not running" would let claim() take a live daemon's record."""
    def denied(pid: int, sig: int) -> None:
        raise PermissionError("not yours to signal")

    monkeypatch.setattr(os, "kill", denied)
    assert pidfile.pid_running(999999999) is True


def test_a_pid_too_wide_for_the_syscall_is_a_malformed_record(data_dir, tmp_path):
    """A pid outside 1..PID_MAX is not a pid, and treating it as one crashed startup.

    The token grammar bounds the record at 20 digits, so 2**32+1234 parsed as a number
    and reached the liveness probe, where the argument conversion (Windows ctypes DWORD,
    POSIX C int) raises out of daemon.main's claim() and out of `mcu daemon stop`'s SPEC
    4 exit contract. Both layers are pinned: the record is rejected, and the probe itself
    answers rather than raises for anything a future caller hands it.
    """
    path = str(tmp_path / "wide.pid")
    for bad in (2**32 + 1234, pidfile.PID_MAX + 1, 2**64, 0):
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(str(bad))
        assert pidfile.read_pid_record(path) is None, f"accepted {bad}"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(str(pidfile.PID_MAX))
    assert pidfile.read_pid_record(path) == pidfile.PID_MAX
    for wide in (2**32 + 1234, 2**64, 2**128):
        assert pidfile.pid_running(wide) is False


def test_claim_does_not_remove_a_record_that_changed_under_it(data_dir, monkeypatch):
    """Two claimers reading the same stale record must not delete each other's claim.

    Both pass the liveness check, the first removes and recreates the file with its own
    pid, and the second's os.remove then deleted that *fresh* record - leaving a running
    daemon unrecorded, the state this module exists to prevent (review class 7). The
    re-read below is what stands between them; the remaining window is documented in
    claim() and is not what this test covers.
    """
    path = pidfile.pid_file_path("127.0.0.1", 8786)
    dead = 0x7FFFFFFE   # a plausible pid that is not running
    assert not pidfile.pid_running(dead)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(str(dead))

    winner = 424242
    real_read = pidfile.read_pid_record
    reads = {"n": 0}

    def racing_read(p):
        reads["n"] += 1
        value = real_read(p)
        if reads["n"] == 1:
            # The other claimer finishes between our first read and the removal.
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(str(winner))
        return value

    monkeypatch.setattr(pidfile, "read_pid_record", racing_read)
    assert pidfile.claim("127.0.0.1", 8786) is None
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == str(winner), "the other claimer's fresh record was removed"


def test_claim_gives_up_when_the_data_dir_cannot_be_made(tmp_path, monkeypatch):
    # A read-only or unwritable data dir must cost the recording, not the startup: the
    # daemon runs on, only without a record for `mcu daemon stop` to find.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8", newline="")
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(blocker / "data"))
    assert pidfile.claim("127.0.0.1", 8784) is None


def test_release_survives_a_record_it_cannot_remove(data_dir, monkeypatch):
    # Shutdown is past the point where anything can be done about it, and raising here
    # would come out of the signal handler that runs during uvicorn's own teardown.
    path = pidfile.claim("127.0.0.1", 8785)
    assert path is not None

    def denied(src, dst):
        raise PermissionError("the process cannot access the file")

    # The record held open (Windows) fails the rename aside, as it failed a remove.
    monkeypatch.setattr(os, "replace", denied)
    pidfile.release(path)
    assert os.path.exists(path)


def test_claim_keeps_a_record_that_is_already_ours(tmp_path, monkeypatch) -> None:
    """claim() removed and recreated its own record, opening a no-pid-file window."""
    import os

    from mcuscope import pidfile

    path = tmp_path / "mcuscope-127.0.0.1-9.pid"
    monkeypatch.setattr(pidfile, "pid_file_path", lambda h, p: str(path))
    path.write_text(str(os.getpid()), encoding="utf-8", newline="\n")

    # The removal is the defect, not the end state: the recreated file looks identical
    # (and the filesystem may even hand back the same inode), so watch for the unlink
    # itself. `mcu daemon stop` landing in that window reports "no pid file" and exits 1.
    removed: list[str] = []
    real_remove = os.remove
    monkeypatch.setattr(os, "remove", lambda p, *a, **kw: (removed.append(str(p)),
                                                           real_remove(p, *a, **kw))[1])

    claimed = pidfile.claim("127.0.0.1", 9)
    assert claimed == str(path)
    assert path.read_text(encoding="utf-8").strip() == str(os.getpid())
    assert removed == [], "claim() deleted a record that was already ours"


def _read_with_deadline(path: str) -> list:
    got: list = []
    t = threading.Thread(target=lambda: got.append(read_pid_record(path)), daemon=True)
    t.start()
    t.join(5)
    return got


@pytest.mark.skipif(sys.platform == "win32", reason="mkfifo is POSIX-only")
def test_a_fifo_at_the_pid_path_reads_as_no_record_without_blocking(tmp_path) -> None:
    fifo = tmp_path / "mcuscoped.pid"
    os.mkfifo(fifo)
    assert _read_with_deadline(str(fifo)) == [None], "read_pid_record blocked on a FIFO"


def test_a_regular_pid_record_still_reads(tmp_path) -> None:
    """Positive control for the FIFO guard: the same reader returns a plain record's pid."""
    rec = tmp_path / "mcuscoped.pid"
    rec.write_text("1234\n", encoding="utf-8", newline="")
    assert _read_with_deadline(str(rec)) == [1234]


# -- remove_record_if / create_record: a record written inside the window survives -------

DEAD = 0x7FFFFFFE   # a plausible pid that is not running
WINNER, NEWER = 424242, 434343


def _write(path: str, pid: int) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(str(pid))


def _on_read(monkeypatch, actions: dict) -> None:
    """Run actions[n] just after the nth read_pid_record returns: a write that lands
    between a caller's read and what it does next."""
    real, n = pidfile.read_pid_record, [0]

    def read(p):
        value = real(p)
        n[0] += 1
        if n[0] in actions:
            actions[n[0]]()
        return value

    monkeypatch.setattr(pidfile, "read_pid_record", read)


def _asides(path: str) -> list[str]:
    folder = os.path.dirname(path)
    return [f for f in os.listdir(folder) if f.endswith((".aside", ".tmp"))]


@pytest.fixture
def path(data_dir):
    return pidfile.pid_file_path("127.0.0.1", 8790)


def test_remove_record_if_removes_only_the_pid_it_names(path, monkeypatch) -> None:
    moves, real = [], os.replace
    monkeypatch.setattr(os, "replace", lambda s, d: moves.append(s) or real(s, d))
    _write(path, DEAD)
    assert pidfile.remove_record_if(path, WINNER) is False
    assert moves == [], "a record naming someone else was moved aside for nothing"
    assert read_pid_record(path) == DEAD
    assert pidfile.remove_record_if(path, DEAD) is True
    assert moves == [path], "positive control: the matching record went aside"
    assert not os.path.exists(path) and _asides(path) == []


def test_a_record_written_after_the_read_is_put_back(path, monkeypatch) -> None:
    """The straddle: a start that lost read its own child's record, the winner's rewrite
    landed, and the remove deleted the winner's record."""
    _write(path, DEAD)
    _on_read(monkeypatch, {1: lambda: _write(path, WINNER)})
    assert pidfile.remove_record_if(path, DEAD) is False
    assert read_pid_record(path) == WINNER
    assert _asides(path) == []


@pytest.mark.skipif(sys.platform == "win32", reason="Windows puts back with a rename")
def test_the_put_back_works_without_hard_links(path, monkeypatch) -> None:
    def no_links(src, dst):
        raise PermissionError(errno.EPERM, "Operation not permitted")   # a FAT data dir

    monkeypatch.setattr(os, "link", no_links)
    _write(path, DEAD)
    _on_read(monkeypatch, {1: lambda: _write(path, WINNER)})
    assert pidfile.remove_record_if(path, DEAD) is False
    assert read_pid_record(path) == WINNER
    assert _asides(path) == []


def test_a_newer_record_beats_the_put_back(path, monkeypatch) -> None:
    _write(path, DEAD)
    _on_read(monkeypatch, {1: lambda: _write(path, WINNER), 2: lambda: _write(path, NEWER)})
    assert pidfile.remove_record_if(path, DEAD) is False
    assert read_pid_record(path) == NEWER
    assert _asides(path) == [], "the displaced record was left lying beside it"


@pytest.mark.parametrize("content, named", [(str(WINNER), f"pid {WINNER}"),
                                            ("garbled", "no readable pid")])
def test_a_put_back_that_fails_names_the_pid_and_keeps_the_record(path, monkeypatch,
                                                                  capsys, content,
                                                                  named) -> None:
    def refused(src, dst):
        raise PermissionError(13, "Access is denied")

    def straddle() -> None:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)

    monkeypatch.setattr(pidfile, "_link_new", refused)
    _write(path, DEAD)
    _on_read(monkeypatch, {1: straddle})
    assert pidfile.remove_record_if(path, DEAD) is False
    (aside,) = _asides(path)
    aside = os.path.join(os.path.dirname(path), aside)
    assert f"mcuscoped: could not put back {path}, which names {named}: [Errno 13] Access " \
        f"is denied; it is kept as {aside}" in capsys.readouterr().err
    with open(aside, encoding="utf-8") as fh:
        assert fh.read() == content


def test_create_record_never_replaces(path) -> None:
    assert pidfile.create_record(path, WINNER) is True
    assert pidfile.create_record(path, NEWER) is False
    assert read_pid_record(path) == WINNER
    assert _asides(path) == []


def test_release_puts_back_a_record_written_after_its_read(data_dir, monkeypatch) -> None:
    path = pidfile.claim("127.0.0.1", 8791)
    assert path is not None
    _on_read(monkeypatch, {1: lambda: _write(path, WINNER)})
    pidfile.release(path)
    assert read_pid_record(path) == WINNER


def test_claim_puts_back_a_record_written_inside_its_stale_removal(data_dir,
                                                                   monkeypatch) -> None:
    """The 2026-08-10 residual: a claim landing between the re-read and the remove."""
    path = pidfile.pid_file_path("127.0.0.1", 8792)
    _write(path, DEAD)
    # Reads: claim's first, its re-read, then remove_record_if's own.
    _on_read(monkeypatch, {3: lambda: _write(path, WINNER)})
    assert pidfile.claim("127.0.0.1", 8792) is None
    assert read_pid_record(path) == WINNER


# -- round 5: retries, the no-hard-link fallback, interrupts, unique names ---------------


@pytest.fixture
def no_links(monkeypatch):
    def refuse(src, dst):
        raise PermissionError(errno.EPERM, "Operation not permitted")   # a FAT data dir

    monkeypatch.setattr(os, "link", refuse)


@pytest.fixture
def no_sleep(monkeypatch):
    from mcuscope import dirs

    monkeypatch.setattr(dirs.time, "sleep", lambda s: None)


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="Windows puts back by rename")


@pytest.mark.parametrize("links", [True, pytest.param(False, marks=posix_only)])
def test_neither_path_replaces_a_newer_record(path, monkeypatch, request, links) -> None:
    if not links:
        request.getfixturevalue("no_links")
    _write(path, DEAD)
    _on_read(monkeypatch, {1: lambda: _write(path, WINNER), 2: lambda: _write(path, NEWER)})
    assert pidfile.remove_record_if(path, DEAD) is False
    assert read_pid_record(path) == NEWER
    assert pidfile.create_record(path, WINNER) is False
    assert read_pid_record(path) == NEWER
    assert _asides(path) == []


@posix_only
def test_a_failed_write_without_hard_links_leaves_no_empty_record(path, no_links,
                                                                  monkeypatch) -> None:
    def full(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "write", full)
    with pytest.raises(OSError, match="No space left"):
        pidfile.create_record(path, WINNER)
    assert not os.path.exists(path), "an empty record was left for good"
    assert _asides(path) == []


def test_the_aside_rides_out_a_sharing_violation(path, monkeypatch, no_sleep) -> None:
    real, calls = os.replace, []

    def held(src, dst):
        calls.append(src)
        if len(calls) < 3:   # WinError 32: a reader or a scanner holds the record
            raise PermissionError(13, "The process cannot access the file", src, 32)
        real(src, dst)

    monkeypatch.setattr(os, "replace", held)
    _write(path, DEAD)
    assert pidfile.remove_record_if(path, DEAD) is True
    assert len(calls) == 3 and not os.path.exists(path)


def test_the_windows_put_back_retries_a_sharing_violation_but_not_an_existing_file(
        path, monkeypatch, no_sleep) -> None:
    monkeypatch.setattr(pidfile.sys, "platform", "win32")
    real, calls = os.rename, []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError(13, "The process cannot access the file", src, 32)
        if os.path.exists(dst):
            raise FileExistsError(17, "Cannot create a file when that file already exists")
        real(src, dst)

    monkeypatch.setattr(os, "rename", flaky)
    assert pidfile.create_record(path, WINNER) is True
    assert len(calls) == 3 and read_pid_record(path) == WINNER
    calls.clear()
    assert pidfile.create_record(path, NEWER) is False
    assert len(calls) == 3, "positive control: the retries, then one FileExistsError"
    exists: list[str] = []

    def taken(src, dst):
        exists.append(dst)
        raise FileExistsError(17, "Cannot create a file when that file already exists")

    monkeypatch.setattr(os, "rename", taken)
    assert pidfile.create_record(path, NEWER) is False
    assert len(exists) == 1, "FileExistsError was retried"


def test_ctrl_c_before_the_aside_is_judged_puts_it_back(path, monkeypatch) -> None:
    def interrupt() -> None:
        raise KeyboardInterrupt

    _write(path, DEAD)
    _on_read(monkeypatch, {2: interrupt})     # the read of the aside copy
    with pytest.raises(KeyboardInterrupt):
        pidfile.remove_record_if(path, DEAD)
    assert read_pid_record(path) == DEAD, "the record was lost with no message"
    assert _asides(path) == []


def test_ctrl_c_inside_the_put_back_still_puts_it_back(path, monkeypatch) -> None:
    real, calls = pidfile._link_new, []

    def interrupted_once(src, dst):
        calls.append(src)
        if len(calls) == 1:
            raise KeyboardInterrupt
        real(src, dst)

    monkeypatch.setattr(pidfile, "_link_new", interrupted_once)
    _write(path, DEAD)
    _on_read(monkeypatch, {1: lambda: _write(path, WINNER)})
    with pytest.raises(KeyboardInterrupt):
        pidfile.remove_record_if(path, DEAD)
    assert len(calls) == 2, "positive control: the put-back was interrupted, then retried"
    assert read_pid_record(path) == WINNER
    assert _asides(path) == []


def test_aside_and_tmp_names_differ_per_call(path, monkeypatch) -> None:
    """A leftover hard-linked to a live record must not be reused: renaming the record onto
    it is a no-op, and opening it for the tmp write truncates the record."""
    moves, links, real_replace, real_link = [], [], os.replace, pidfile._link_new
    monkeypatch.setattr(os, "replace", lambda s, d: moves.append(d) or real_replace(s, d))
    monkeypatch.setattr(pidfile, "_link_new", lambda s, d: links.append(s) or real_link(s, d))
    for _ in range(2):
        _write(path, DEAD)
        assert pidfile.remove_record_if(path, DEAD) is True
        assert pidfile.create_record(path, WINNER) is True
        os.remove(path)
    assert len(moves) == 2 and moves[0] != moves[1]
    assert len(links) == 2 and links[0] != links[1]


def test_ctrl_c_inside_the_put_back_drops_the_copy_when_a_newer_record_stands(
        path, monkeypatch) -> None:
    real, calls = pidfile._link_new, []

    def interrupted_once(src, dst):
        calls.append(src)
        if len(calls) == 1:
            raise KeyboardInterrupt
        real(src, dst)

    monkeypatch.setattr(pidfile, "_link_new", interrupted_once)
    _write(path, DEAD)
    _on_read(monkeypatch, {1: lambda: _write(path, WINNER), 2: lambda: _write(path, NEWER)})
    with pytest.raises(KeyboardInterrupt):
        pidfile.remove_record_if(path, DEAD)
    assert len(calls) == 2
    assert read_pid_record(path) == NEWER
    assert _asides(path) == [], "the displaced copy was left lying beside the newer record"


# -- round 6: interrupts at the edges, the kept guard, the retry policy ------------------


def test_ctrl_c_as_the_aside_rename_returns_puts_it_back(path, monkeypatch) -> None:
    real = pidfile.retry_sharing

    def renamed_then_interrupted(call, *args):
        real(call, *args)
        if call is os.replace:
            raise KeyboardInterrupt

    monkeypatch.setattr(pidfile, "retry_sharing", renamed_then_interrupted)
    _write(path, DEAD)
    with pytest.raises(KeyboardInterrupt):
        pidfile.remove_record_if(path, DEAD)
    assert read_pid_record(path) == DEAD, "the record was lost with no message"
    assert _asides(path) == []


def _write_interrupted_once(monkeypatch) -> list:
    real, calls = os.write, []

    def write(fd, data):
        calls.append(data)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return real(fd, data)

    monkeypatch.setattr(os, "write", write)
    return calls


@posix_only
def test_ctrl_c_inside_the_fallback_create_leaves_no_empty_record(path, no_links,
                                                                  monkeypatch) -> None:
    calls = _write_interrupted_once(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        pidfile.create_record(path, WINNER)
    assert calls, "positive control: the fallback write was reached"
    assert not os.path.exists(path), "an empty record was left for good"
    assert _asides(path) == []


@posix_only
def test_ctrl_c_inside_the_fallback_put_back_keeps_the_only_copy(path, no_links,
                                                                 monkeypatch) -> None:
    """The empty record the interrupted write left made the retry meet FileExistsError,
    which then deleted the aside copy: the record was lost and an empty one left."""
    _write(path, DEAD)
    _on_read(monkeypatch, {1: lambda: _write(path, WINNER)})
    calls = _write_interrupted_once(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        pidfile.remove_record_if(path, DEAD)
    assert len(calls) == 2, "positive control: interrupted, then put back again"
    assert read_pid_record(path) == WINNER
    assert _asides(path) == []


def test_a_failed_put_back_is_not_retried_behind_its_note(path, monkeypatch, capsys) -> None:
    """The note says the copy is kept aside, so the record must not come back after it."""
    real, calls = pidfile._link_new, []

    def fails_once(src, dst):
        calls.append(src)
        if len(calls) == 1:
            raise PermissionError(13, "Access is denied")
        real(src, dst)

    monkeypatch.setattr(pidfile, "_link_new", fails_once)
    _write(path, DEAD)
    _on_read(monkeypatch, {1: lambda: _write(path, WINNER)})
    assert pidfile.remove_record_if(path, DEAD) is False
    assert "could not put back" in capsys.readouterr().err
    assert len(calls) == 1, "the failed put-back was retried"
    assert not os.path.exists(path)
    assert len(_asides(path)) == 1


def test_the_sharing_retry_is_bounded_and_backs_off(monkeypatch) -> None:
    from mcuscope import dirs

    sleeps: list[float] = []
    monkeypatch.setattr(dirs.time, "sleep", sleeps.append)
    held = PermissionError(13, "The process cannot access the file")

    def always_held():
        raise held

    with pytest.raises(PermissionError) as info:
        dirs.retry_sharing(always_held)
    assert info.value is held, "the last error was not the one raised"
    assert sleeps == pytest.approx([0.02 * n for n in range(1, 10)])
    assert sum(sleeps) == pytest.approx(0.9)
    sleeps.clear()

    def exists():
        raise FileExistsError(17, "exists")

    with pytest.raises(FileExistsError):
        dirs.retry_sharing(exists)
    assert sleeps == [], "FileExistsError was retried"
