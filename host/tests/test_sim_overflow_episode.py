"""SPEC 2.3 over-long event episodes in the simulator, as monitor.c's event_end sends them.

The first cut of a type is announced at once and later cuts of it are counted; the episode
ends with `cut=<n>` at the next event of its type sent whole, a cut of another type, or a
quiet second. The episode belongs to the session, so it spans the transport's passes.
"""

from __future__ import annotations

import errno
import os
import socket
import threading
import time

import pytest

from mcuscope import sim as sim_module

LONG_P = "!p a " + "y" * 300      # cut to "!p a"
LONG_M = "!m a " + "y" * 300


def _enc(lines: list[str], episode) -> list[str]:
    return sim_module.encode_lines(lines, episode).decode("ascii").splitlines()


def test_one_notice_per_episode_and_the_count_when_a_whole_event_ends_it():
    ep = sim_module.OverflowEpisode()
    assert _enc([LONG_P], ep) == ["!p a", "!e event p overflow"]
    assert _enc([LONG_P, LONG_P], ep) == ["!p a", "!p a"]
    assert _enc(["!p 5 v=1"], ep) == ["!e event p overflow cut=3", "!p 5 v=1"]
    assert _enc([LONG_P], ep) == ["!p a", "!e event p overflow"]


def test_another_type_whole_leaves_the_episode_a_cut_of_it_ends_it():
    ep = sim_module.OverflowEpisode()
    _enc([LONG_P], ep)
    assert _enc(["!q 1", "!can 1 - 100 -"], ep) == ["!q 1", "!can 1 - 100 -"]
    assert _enc([LONG_M], ep) == ["!e event p overflow cut=1", "!m a", "!e event m overflow"]


def test_a_cut_that_keeps_nothing_is_counted_silently():
    ep = sim_module.OverflowEpisode()
    _enc([LONG_M], ep)
    assert _enc(["!m @7 " + "m" * 300], ep) == []
    assert _enc(["!m @8 x"], ep) == ["!e event m overflow cut=2", "!m @8 x"]


def test_the_quiet_second_is_measured_from_the_last_cut():
    quiet = sim_module.OVERFLOW_QUIET_S
    ep = sim_module.OverflowEpisode()
    _enc([LONG_P], ep)
    time.sleep(0.05)
    before = time.monotonic()
    _enc([LONG_P], ep)
    after = time.monotonic()
    # Not yet a second after the second cut (the first was 50 ms earlier).
    assert ep.expire(before + quiet - 0.001) == []
    assert ep.expire(after + quiet) == ["!e event p overflow cut=2"]
    # The boundary itself ends it.
    _enc([LONG_P], ep)
    assert ep.expire(ep.last + quiet - 0.001) == []
    assert ep.expire(ep.last + quiet) == ["!e event p overflow cut=1"]


def test_sanitized_types_share_an_episode():
    ep = sim_module.OverflowEpisode()
    lines = ["!a\x01 a " + "y" * 300, "!a\x02 a " + "y" * 300]
    assert _enc(lines, ep) == ["!a. a", "!e event a. overflow", "!a. a"]


def test_a_quiet_second_ends_the_episode_from_poll_events():
    sim = sim_module.Simulator(sim_module.build_parser().parse_args([]))
    _enc([LONG_P], sim.overflow)
    sim.overflow.last = time.monotonic() - sim_module.OVERFLOW_QUIET_S + 0.5
    assert not [x for x in sim.poll_events() if x.startswith("!e event")]
    sim.overflow.last = time.monotonic() - sim_module.OVERFLOW_QUIET_S
    assert [x for x in sim.poll_events() if x.startswith("!e event")] == [
        "!e event p overflow cut=1"
    ]
    assert not [x for x in sim.poll_events() if x.startswith("!e event")]


def _scripted(monkeypatch, args, *passes: list[str], done: threading.Event | None = None):
    """The Simulator built on `args` polls these passes, one per call, then nothing.

    Keyed on `args` so a simulator of another test cannot take a pass. Once `done` is set it
    raises EBADF, which ends a pty serve loop the way a dead master does.
    """
    script = list(passes)
    real = sim_module.Simulator.poll_events

    def poll_events(self):
        if self.args is not args:
            return real(self)
        if done is not None and done.is_set():
            raise OSError(errno.EBADF, "test finished")
        return script.pop(0) if script else []

    monkeypatch.setattr(sim_module.Simulator, "poll_events", poll_events)


PASSES = ([LONG_P], [LONG_P], ["!p 5 v=1"])
WANT = b"!p a\n!e event p overflow\n!p a\n!e event p overflow cut=2\n!p 5 v=1\n"


def test_the_in_process_link_keeps_one_episode_across_passes(monkeypatch):
    args = sim_module.build_parser().parse_args([])
    _scripted(monkeypatch, args, *PASSES)
    src = sim_module.SimSource(args)
    assert b"".join(src.poll() for _ in range(4)) == WANT


def test_feed_and_poll_share_the_episode(monkeypatch):
    args = sim_module.build_parser().parse_args([])
    _scripted(monkeypatch, args, [], ["!p 5 v=1"])
    real = sim_module.Simulator.handle_line

    def handle_line(self, line):
        return [LONG_P] if self.args is args else real(self, line)

    monkeypatch.setattr(sim_module.Simulator, "handle_line", handle_line)
    src = sim_module.SimSource(args)
    assert src.feed(b">1 ping\n") == b"!p a\n!e event p overflow\n"
    assert src.feed(b">2 ping\n") == b"!p a\n"
    assert src.poll() + src.poll() == b"!e event p overflow cut=2\n!p 5 v=1\n"


def _read_until(read, want: bytes, timeout: float = 5.0) -> bytes:
    buf = b""
    deadline = time.monotonic() + timeout
    while want not in buf and time.monotonic() < deadline:
        buf += read()
    return buf


def test_the_tcp_session_keeps_one_episode_across_passes(monkeypatch):
    args = sim_module.build_parser().parse_args([])
    _scripted(monkeypatch, args, *PASSES)
    handle = sim_module.spawn(args)
    try:
        with socket.create_connection(("127.0.0.1", handle.port), timeout=5) as conn:
            conn.settimeout(0.2)

            def read() -> bytes:
                try:
                    return conn.recv(4096)
                except TimeoutError:
                    return b""

            assert _read_until(read, b"v=1\n") == WANT
    finally:
        handle.stop()


@pytest.mark.skipif(os.name != "posix", reason="pty transport is POSIX-only")
def test_the_pty_session_keeps_one_episode_across_passes(monkeypatch, tmp_path):
    link = tmp_path / "pty"
    args = sim_module.build_parser().parse_args(["--pty", "--symlink", str(link)])
    done = threading.Event()
    _scripted(monkeypatch, args, *PASSES, done=done)
    t = threading.Thread(target=sim_module.serve_pty, args=(args,), daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while not link.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    fd = os.open(link, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        def read() -> bytes:
            try:
                return os.read(fd, 4096)
            except BlockingIOError:
                time.sleep(0.01)
                return b""

        got = _read_until(read, b"v=1\n")
    finally:
        os.close(fd)
        done.set()
    t.join(timeout=5)
    assert not t.is_alive()
    # The pty's line discipline turns LF into CRLF on the way out.
    assert got.replace(b"\r\n", b"\n") == WANT
