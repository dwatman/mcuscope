"""The capture's identity is the file the writer's handle holds (REVIEW class 92): a file
replaced while it is being opened refuses the start instead of being recorded."""

from __future__ import annotations

import os

import pytest

from mcuscope.store import CaptureUnreadable, Store


async def test_a_capture_replaced_while_the_writer_opens_it_refuses_the_start(
    tmp_path, monkeypatch
) -> None:
    db = tmp_path / "capture.db"
    imposter = tmp_path / "imposter.db"
    imposter.write_bytes(b"")
    real = Store._setup_writer

    def setup_then_replace(self, conn):
        real(self, conn)
        os.replace(imposter, db)   # the handle keeps the original file

    monkeypatch.setattr(Store, "_setup_writer", setup_then_replace)
    store = Store(str(db))
    with pytest.raises(CaptureUnreadable, match="was replaced while it was being opened"):
        await store.start()
