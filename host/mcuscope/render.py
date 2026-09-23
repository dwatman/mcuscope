"""Row renderers shared by the CLI and the daemon's streaming exports.

Pure formatting, no click/typer: `/lines/export?format=text` must render a row exactly as
`mcu log` does, and importing cli_output would pull the whole CLI stack into the daemon.
"""

from __future__ import annotations

import time
from typing import Any

# Every boundary str.splitlines() honours, shown as an escape so one row stays one line
# of a text export (the store folds CR and LF already; the rest reach it from the wire
# or from POST /marker).
_BREAKS = {
    c: (f"\\x{c:02x}" if c < 0x100 else f"\\u{c:04x}")
    for c in (0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029)
}


def fmt_ts(ts: float) -> str:
    """Time of day with milliseconds, for per-line output where the date is noise."""
    return time.strftime("%H:%M:%S", time.localtime(ts)) + f".{int(ts * 1000) % 1000:03d}"


def one_line(raw: Any) -> str:
    """Device text as one line of output, each line boundary shown as an escape."""
    return str(raw).translate(_BREAKS)


def fmt_line(row: dict[str, Any], show_port: bool = False) -> str:
    """`HH:MM:SS.mmm chan| raw`, with `[port]` after the time when `show_port`."""
    port = f"[{row['port']}] " if show_port else ""
    return f"{fmt_ts(row['ts'])} {port}{row['chan']:>6}| {one_line(row['raw'])}"
