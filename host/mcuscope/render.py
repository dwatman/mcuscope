"""Row renderers shared by the CLI and the daemon's streaming exports.

Pure formatting, no click/typer: `/lines/export?format=text` must render a row exactly as
`mcu log` does, and importing cli_output would pull the whole CLI stack into the daemon.
"""

from __future__ import annotations

import time
from typing import Any


def fmt_ts(ts: float) -> str:
    """Time of day with milliseconds, for per-line output where the date is noise."""
    return time.strftime("%H:%M:%S", time.localtime(ts)) + f".{int(ts * 1000) % 1000:03d}"


def fmt_line(row: dict[str, Any]) -> str:
    return f"{fmt_ts(row['ts'])} {row['chan']:>6}| {row['raw']}"
