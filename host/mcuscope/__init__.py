"""mcuscope: host daemon and CLI for MCUscope, a hardware debug and plotting bridge.

See docs/SPEC.md for the authoritative protocol and API contract. The package is
split into small modules:

- protocol: pure encode/decode of the UART line protocol (no I/O).
- store: SQLite capture storage.
- serial_link: per-port serial handling, seq machinery, reconnect.
- server: FastAPI REST + WebSocket app.
- daemon: mcuscoped entry point (config load, wiring, lifecycle).
- cli: the mcu command-line client.
"""

import sys

# requires-python only gates *installers*. Anything that bypasses the metadata (a source
# checkout, `python -m mcuscope.cli`, a hand-made venv on an older interpreter) got as far
# as importing `server` and then failed deep in pydantic, evaluating a `str | None`
# annotation (PEP 604, 3.10+) for a request model; the lower-level modules import cleanly
# on 3.9. Say it plainly at package import and name the interpreter, since the usual
# cause is a stray one earlier on PATH.
# noqa UP036: ruff reads this as dead code because the *target* version is 3.10. Catching
# an interpreter below that target is the entire point, and the check runs before any
# 3.10-only syntax the rest of the package uses.
if sys.version_info < (3, 10):  # noqa: UP036  # pragma: no cover
    raise RuntimeError(
        f"mcuscope requires Python 3.10 or newer; this is "
        f"{sys.version.split()[0]} ({sys.executable})"
    )

__version__ = "0.2.0"
