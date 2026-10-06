"""write_new_file opens in binary mode: without O_BINARY the Windows CRT writes CRLF."""

from __future__ import annotations

import os

from mcuscope import config


def test_write_new_file_passes_o_binary_to_os_open(tmp_path, monkeypatch) -> None:
    sentinel = 1 << 30   # a bit no POSIX open flag uses; stands in for Windows' O_BINARY
    monkeypatch.setattr(os, "O_BINARY", sentinel, raising=False)
    seen = []
    real = os.open

    def spy(path, flags, *a, **k):
        seen.append(flags)
        return real(path, flags & ~sentinel, *a, **k)

    monkeypatch.setattr(config.os, "open", spy)
    config.write_new_file(tmp_path / "t", b"a\nb\n", like=tmp_path / "none")
    assert seen and seen[0] & sentinel
    assert (tmp_path / "t").read_bytes() == b"a\nb\n"
