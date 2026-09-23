"""SerialPort splits received lines on U+0020 only (SPEC 2.1): a tab or a 0x1F byte is
part of its token, so it cannot make a line read as a seq, an event tag or a monitor name."""

from __future__ import annotations

import asyncio
import time

import pytest

from mcuscope.serial_link import SerialPort, _response_seq
from mcuscope.store import Store


@pytest.mark.parametrize(("line", "seq"), [
    ("<17 OK", 17),
    ("<  17   OK", 17),          # runs of spaces still separate
    ("<17\tOK", None),
    ("<17\x1fOK", None),
    ("<\t17 OK", None),
])
def test_a_response_seq_is_split_on_spaces_only(line, seq) -> None:
    assert _response_seq(line) == seq


async def test_a_can_event_is_dispatched_on_space_separated_tokens_only(tmp_path) -> None:
    store = Store(str(tmp_path / "tok.db"))
    await store.start()
    try:
        port = SerialPort(store, asyncio.get_running_loop(), "board", identify=False)
        await port._store_rx_line(time.time(), "!can 100 - 100 DEADBEEF")   # control
        await port._store_rx_line(time.time(), "!can\t100 - 100 DEADBEEF")
        await port._store_rx_line(time.time(), "!can 100\x1f- 100 DEADBEEF")
        frames, _more = store.query_can_frames()
        assert len(frames) == 1
    finally:
        await store.stop()


class _Rows:
    async def add_line(self, **kw):
        return {"id": 1, **kw}


@pytest.mark.parametrize(("data", "target"), [
    ("monitor 1 board", "board"),
    ("monitor\t1 board", None),
    ("monitor 1\x1fboard", None),
])
async def test_the_identify_reply_is_split_on_spaces_only(data, target) -> None:
    port = SerialPort(_Rows(), asyncio.get_running_loop(), "board")

    async def reply(cmd, timeout_ms):
        return {"status": "ok", "data": data}

    port.send_command = reply
    await port._identify()
    assert port.target == target
    await asyncio.gather(*list(port._bg_tasks))
