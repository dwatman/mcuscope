"""User data, config and cache directories (SPEC 3.3).

`MCUSCOPE_DATA_DIR`, `MCUSCOPE_CONFIG_DIR` and `MCUSCOPE_CACHE_DIR` name a directory
outright when set non-empty (used as given), else platformdirs resolves it. The override
is what isolates a spawned child on Windows, where platformdirs ignores `XDG_*`.
platformdirs is imported lazily: `_stdio._crash_dir` must never raise on the crash path.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

APP_NAME = "mcuscope"


def user_dir(kind: str) -> str:
    """The app's "data", "config" or "cache" directory, override first."""
    override = os.environ.get(f"MCUSCOPE_{kind.upper()}_DIR")
    if override:
        return override
    import platformdirs

    return getattr(platformdirs, f"user_{kind}_dir")(APP_NAME)


def retry_sharing(call: Callable[..., Any], *args: Any) -> Any:
    """`call(*args)`, retrying the PermissionError of a Windows sharing violation.

    Windows fails a rename or replace while any other process holds either file without
    FILE_SHARE_DELETE (an on-access scan, the Search indexer, a reader): WinError 5 or 32,
    usually gone within tens of milliseconds. A handle that is really held still fails,
    with the real error. On POSIX only a permanent permission error (EACCES, EPERM) raises
    it, and costs the 0.9 s. Never retries FileExistsError: a non-replacing rename onto an
    existing file is an answer.
    """
    attempts = 10
    for attempt in range(attempts):
        try:
            return call(*args)
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.02 * (attempt + 1))   # 0.9 s in total across the 10 attempts
