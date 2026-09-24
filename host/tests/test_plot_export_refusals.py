"""`/plot/export` refusals and the decoded wide header (SPEC 9.2).

Lines are fed into the running daemon's port, as `test_plot_export_decode` does, because
every case is a particular order of definitions and samples."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import pytest

from mcuscope.serial_link import SerialPort
from tests.support import Stack, stack_client
from tests.test_plot_export_decode import DEF, csv_rows, export, feed, sample

LABEL = "deadband is numeric, but {} renders as a label (an enum or a decoded bit lane)"


def _second_port(stack: Stack, *lines: str) -> None:
    ports = stack.app.state.ports
    second = ports._ports.get("second") or SerialPort(stack.app.state.store, ports._loop, "second")
    ports._ports["second"] = second
    for line in lines:
        asyncio.run_coroutine_threadsafe(
            second._store_rx_line(time.time(), line), ports._loop
        ).result(10)


# -- A-3: a label channel declared inside the window ----------------------------------


def test_a_band_on_an_enum_first_declared_inside_the_window_is_refused(
    make_stack: Callable[..., Stack],
) -> None:
    """The refusal was judged on the definitions primed before the window only; `mode` is
    declared after `volts`' first sample, so its band passed and did nothing."""
    stack = make_stack()
    feed(
        stack,
        "!pd 3 volts:s2*0.01:V", "!ps 3 1 0064",
        "!pd 4 mode:u1:=0=IDLE,1=ARMED,2=RUN", "!ps 4 1 00", "!ps 4 2 01", "!ps 4 3 02",
    )
    with stack_client(stack) as c:
        r = export(c, names="volts,mode", decode=1, changes=1, deadband="mode=5")
        ok = export(c, names="volts,mode", decode=1, changes=1, deadband="volts=5")
    assert r.status_code == 400, r.text
    assert r.json()["error"] == LABEL.format("mode")
    assert ok.status_code == 200, ok.text


def test_a_band_on_a_channel_redefined_as_an_enum_inside_the_window_is_accepted(
    make_stack: Callable[..., Stack],
) -> None:
    """The numeric half before the redefinition is still banded: judging only the newest
    declaration of the sid would be the false 400 F4 fixed for two streams."""
    stack = make_stack()
    feed(stack, "!pd 3 mode:u1", "!ps 3 1 05", "!pd 3 mode:u1:=0=A,1=B", "!ps 3 2 01")
    with stack_client(stack) as c:
        r = export(c, names="mode", decode=1, changes=1, deadband="mode=0.5")
    assert r.status_code == 200, r.text


# -- A-8 and B-16: the value grammar, and a name given twice --------------------------


@pytest.mark.parametrize("value", ["1_0", " 5 ", "5 ", "+5", "0x10", ".5", "5.", "1e999"])
def test_a_deadband_value_outside_the_grammar_is_refused(
    make_stack: Callable[..., Stack], value: str
) -> None:
    stack = make_stack()
    feed(stack, DEF, sample(1, 0, 100, 1))
    with stack_client(stack) as c:
        r = export(c, names="volts", decode=1, changes=1, deadband=f"volts={value}")
    assert r.status_code == 400, f"{value!r}: {r.text}"
    assert r.json()["error"] == f"deadband value is not a number: volts={value}"


def test_deadband_values_in_the_grammar_are_accepted(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack()
    feed(stack, DEF, sample(1, 0, 100, 1))
    with stack_client(stack) as c:
        for value in ("10", "0.05", "1e3", "2.5E-1", "-0"):   # negatives refused elsewhere
            r = export(c, names="volts", decode=1, changes=1, deadband=f"volts={value}")
            assert r.status_code == 200, f"{value}: {r.text}"


def test_a_deadband_naming_one_channel_twice_is_refused(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack()
    feed(stack, DEF, sample(1, 0, 100, 1))
    with stack_client(stack) as c:
        r = export(c, names="volts", decode=1, changes=1, deadband="volts=1,volts=2")
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "deadband names volts twice"


# -- A-11: a repeated export name --------------------------------------------------------


@pytest.mark.parametrize("fmt", ["long", "wide"])
def test_a_name_listed_twice_is_refused(make_stack: Callable[..., Stack], fmt: str) -> None:
    stack = make_stack()
    feed(stack, DEF, sample(1, 0, 100, 1))
    with stack_client(stack) as c:
        r = export(c, names="volts,mode,volts", format=fmt)
        ok = export(c, names="volts,mode", format=fmt)
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "names lists volts twice"
    assert ok.status_code == 200


# -- F-28 and F-29: the decoded wide header ---------------------------------------------


def test_the_newest_definition_before_the_window_labels_a_shared_lane(
    make_stack: Callable[..., Stack],
) -> None:
    """SPEC 9.2: two boards name the lanes' group differently; the newer one names the column."""
    stack = make_stack()
    _second_port(stack, "!pd 3 io:u1:/led,irq")
    feed(stack, "!pd 3 pins:u1:/led,irq")
    _second_port(stack, "!ps 3 1 01")
    feed(stack, "!ps 3 2 02")
    with stack_client(stack) as c:
        r = export(c, names="led,irq", format="wide", decode=1)
    assert r.status_code == 200, r.text
    assert csv_rows(r.text)[0] == ["ts", "tick_ms", "pins.led", "pins.irq"]


def test_a_lane_first_declared_inside_the_window_is_qualified_in_the_header(
    make_stack: Callable[..., Stack],
) -> None:
    stack = make_stack()
    feed(stack, "!pd 3 a:u1", "!ps 3 1 05", "!pd 3 a:u1 io:u1:/led,irq", "!ps 3 2 05,01")
    with stack_client(stack) as c:
        r = export(c, names="a,led", format="wide", decode=1)
    assert r.status_code == 200, r.text
    assert csv_rows(r.text)[0] == ["ts", "tick_ms", "a", "io.led"]


# -- measurement F4: /plot/export and an unknown channel name ---------------------------


def _a_plot_channel(stack: Stack, tries: int = 200) -> str:
    with stack_client(stack) as c:
        for _ in range(tries):
            chans = c.get("/plot/channels").json()["channels"]
            if chans:
                return chans[0]["name"]
            time.sleep(0.05)
    raise AssertionError("the simulator produced no plot channels")


def test_plot_export_refuses_when_no_requested_name_exists(make_stack) -> None:
    stack = make_stack(["--plot"])
    _a_plot_channel(stack)   # the capture has channels; these two are simply not among them
    with stack_client(stack) as c:
        r = c.get("/plot/export", params={"names": "nosuchchan,alsomissing"})
    assert r.status_code == 400
    assert "nosuchchan" in r.json()["error"]
    assert "alsomissing" in r.json()["error"]


def test_plot_export_refuses_one_unknown_name_among_several(make_stack) -> None:
    """Superseded the round that added it: a dead name was exported past at exit 0.

    The tolerance was paid for by the cost of the channel scan, which the writer's
    in-memory summary removed (1 ms warm against 48 ms). SPEC 3.4 changed with it.
    """
    stack = make_stack(["--plot"])
    name = _a_plot_channel(stack)
    with stack_client(stack) as c:
        r = c.get("/plot/export", params={"names": f"{name},nosuchchan"})
        good = c.get("/plot/export", params={"names": name})
    assert r.status_code == 400
    assert r.json()["error"] == "no such plot channel: nosuchchan; see /plot/channels"
    assert good.status_code == 200 and good.text.splitlines()[0].startswith("ts,")
