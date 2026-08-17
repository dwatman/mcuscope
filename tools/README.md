# tools

Host-side development tools.

- `mcu_sim.py` - MCU simulator speaking the full monitor protocol (SPEC section 7). Lets the whole system run with zero hardware; `mcuscoped` attaches to it exactly as it would a real serial port.
  - Default transport is a **TCP listener**, cross-platform (Linux and Windows).
    - It binds `127.0.0.1` on an ephemeral or chosen port.
    - It prints its device string (e.g. `socket://127.0.0.1:9900`) for the daemon's `device` config.
  - `--pty` is a **POSIX-only** opt-in that instead opens a pty pair and prints the slave path, for attaching like a real `/dev/tty*` device. Not available on Windows.
  - Useful flags: `--tcp-port PORT` (0 for ephemeral), `--plot` (stream plot data), `--garbage` (inject junk lines), `--drop-response N` (simulate dropped responses).
- `webui_smoke.py` - manual smoke harness for the web UI (SPEC 9.1).
  - Brings up the simulator and daemon in-process and auto-verifies the API-observable acceptance criteria.
  - Then serves `http://127.0.0.1:8765/ui/` and stays running so you can eyeball each panel in a browser.
  - Run it from the host venv so the `mcuscope` package imports; pass `--port` if 8765 is taken, or `--no-wait` to run just the auto-checks and exit.
