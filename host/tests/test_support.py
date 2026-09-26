"""The harness helpers whose failure would hide a test rather than fail it."""

from __future__ import annotations

import os

import pytest

from tests.support import symlink_or_skip


def _refusal(winerror: int | None) -> OSError:
    exc = OSError(1, "refused")
    exc.winerror = winerror
    return exc


@pytest.mark.parametrize("exc, skip_reason", [
    pytest.param(_refusal(1314), "WinError 1314", id="privilege-not-held"),
    pytest.param(NotImplementedError(), "no symlink support", id="no-symlinks"),
    pytest.param(_refusal(5), None, id="other-winerror"),
    pytest.param(FileExistsError(17, "exists"), None, id="exists"),
    pytest.param(PermissionError(1, "EPERM"), None, id="posix-eperm"),
])
def test_symlink_or_skip_skips_only_on_a_refused_privilege(tmp_path, monkeypatch, exc,
                                                           skip_reason) -> None:
    def refuse(target, link):
        raise exc
    monkeypatch.setattr(os, "symlink", refuse)
    # BaseException: a skip is one, and pytest.raises(OSError) would let it through, so the
    # case would skip instead of fail.
    with pytest.raises(BaseException) as caught:
        symlink_or_skip(tmp_path / "link", tmp_path / "target")
    if skip_reason:
        assert caught.type is pytest.skip.Exception, caught.value
        assert skip_reason in str(caught.value)
    else:
        assert caught.value is exc, "a real failure was replaced, not raised"
