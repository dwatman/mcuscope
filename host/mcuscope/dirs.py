"""User data, config and cache directories (SPEC 3.3).

`MCUSCOPE_DATA_DIR`, `MCUSCOPE_CONFIG_DIR` and `MCUSCOPE_CACHE_DIR` name a directory
outright when set non-empty (used as given), else platformdirs resolves it. The override
is what isolates a spawned child on Windows, where platformdirs ignores `XDG_*`.
platformdirs is imported lazily: `_stdio._crash_dir` must never raise on the crash path.
"""

from __future__ import annotations

import os

APP_NAME = "mcuscope"


def user_dir(kind: str) -> str:
    """The app's "data", "config" or "cache" directory, override first."""
    override = os.environ.get(f"MCUSCOPE_{kind.upper()}_DIR")
    if override:
        return override
    import platformdirs

    return getattr(platformdirs, f"user_{kind}_dir")(APP_NAME)
