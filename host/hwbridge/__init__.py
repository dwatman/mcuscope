"""hwbridge: host daemon and CLI for the mcu-interface hardware debug bridge.

See docs/SPEC.md for the authoritative protocol and API contract. The package is
split into small modules:

- protocol: pure encode/decode of the UART line protocol (no I/O).
- store: SQLite capture storage.
- serial_link: per-port serial handling, seq machinery, reconnect.
- server: FastAPI REST + WebSocket app.
- daemon: hwbridged entry point (config load, wiring, lifecycle).
- cli: the mcu command-line client.
"""

__version__ = "0.1.0"
