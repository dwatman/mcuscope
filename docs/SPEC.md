# MCUscope System Specification

Version 1.0 (design). Author: Claude Fable 5, 2026-07-03. Implementation target: Claude Opus.

This document is the authoritative contract. Where the implementation plan and this
document disagree, this document wins. Anything marked **[P2]** is a later phase:
design for it, do not build it in v1.

---

## 1. Goals and constraints

Goal: let an AI agent (Claude Code) and a human interactively debug embedded systems:
send bus transactions (CAN classic, I2C, SPI), control GPIO, read ADC, observe debug
output, and perform "send X, wait for Y, timeout Z" interactions, all through one tool.

Constraints and environment facts (from the project owner):

- Host side must run on **both Linux (Ubuntu/Mint) and Windows 10/11**. Serial
  devices are USB-serial adapters or the ST-Link V2/V3 virtual COM port
  (`/dev/ttyACM*` / `/dev/ttyUSB*` on Linux, `COMx` on Windows). The entire host
  stack is cross-platform (Python, pyserial, FastAPI, SQLite, browser UI); the only
  POSIX-only piece is the simulator's optional pty mode, so tests run against its
  TCP mode and pty-specific tests skip on Windows. Serial I/O uses plain pyserial
  with a reader thread per port (NOT pyserial-asyncio, whose Windows support is
  unreliable).
- MCU side: STM32, **bare-metal superloop, no RTOS**, LL drivers preferred (CAN uses
  HAL because LL does not cover it). The owner has an existing, reusable
  **DMA+interrupt UART driver with circular RX/TX buffers**. The monitor module must
  sit entirely above that driver: it reads bytes from and writes bytes to the circular
  buffers via a shim, and is polled from the superloop. It must never require an RTOS,
  dynamic allocation, or direct register access in its core.
- The owner has (or will write) small drivers for CAN/I2C/SPI access. The monitor's
  bus commands call **shim functions** the owner implements per project against those
  drivers.
- v1 peripherals: CAN classic (bxCAN), I2C master, SPI master, GPIO, ADC.
- Firmware flashing / MCU reset integration: **[P2]**.
- MCP server: **[P2]**. v1 AI interface is the CLI.
- Owner writing style rule: no em dashes or en dashes anywhere in this repo (code,
  comments, docs, commit messages). Use commas, colons, parentheses, or spaced hyphens.

Non-goals for the v1 core (phases 0 to 5): multi-user auth (the API binds to
127.0.0.1 only), CAN FD, RTT/SWO transport, DBC decoding, and OS-level autostart
(systemd enable / Windows Task Scheduler integration; the daemon is started manually
or via `mcu daemon start`). A browser-based UI (enhanced serial terminal, setup,
decoded views, realtime plots) is in scope as phases 6 and 7; see section 9.

---

## 2. Wire protocol (UART, MCU <-> PC)

### 2.1 Framing

- Encoding: 7-bit printable ASCII. Lines terminated by `\n` (LF). The parser must
  accept and strip a preceding `\r`. Both sides emit plain LF.
- Maximum line length: 255 bytes of content plus the LF terminator (256 bytes total on
  the wire), both directions. The firmware parser discards oversized lines and (if it
  was a command) replies `ERR 8 overflow` when the terminator finally arrives; if the
  seq could not be parsed, it stays silent.
- Tokens are separated by single spaces. No quoting or escaping in v1: all arguments
  are hex strings, decimal numbers, or bare names (no spaces).
- Hex data payloads are uppercase or lowercase hex pairs with no separators or `0x`
  prefix (e.g. `DEADBEEF`). IDs and addresses are hex without `0x` unless stated.

### 2.2 Line types, distinguished by first character

| First char | Direction | Meaning |
|------------|-----------|---------|
| `>` | PC to MCU | Command |
| `<` | MCU to PC | Response to a command |
| `!` | MCU to PC | Asynchronous event |
| anything else | MCU to PC | Debug output (normal application prints, untouched) |

Firmware requirement: every line (monitor responses, monitor events, and application
debug prints) must be written to the UART TX circular buffer **atomically as a whole
line**, so lines never interleave mid-line. The owner's existing printf path already
writes whole formatted strings; the monitor buffers each outgoing line and pushes it
in one shim call. Application debug lines must not begin with `<` or `!` (document
this; do not enforce).

PC to MCU traffic other than `>` commands is permitted (raw lines via the daemon's
`send` API, for talking to non-monitor firmware); the monitor silently ignores lines
that do not start with `>`.

### 2.3 Commands and responses

Command:

```
>SEQ CMD [SUBCMD] [ARGS...]
```

- `SEQ`: decimal 1 to 65535, assigned by the daemon, wraps around, never 0.
- Response, exactly one per command, echoing the seq:

```
<SEQ OK [data tokens...]
<SEQ ERR CODE NAME [detail...]
```

Error codes (fixed table, shared constant between firmware and daemon):

| Code | Name | Meaning |
|------|------|---------|
| 1 | badcmd | Unknown command |
| 2 | badarg | Wrong argument count/format/range |
| 3 | timeout | Bus operation timed out |
| 4 | buserr | Bus error (CAN TX failed, SPI fault, etc.) |
| 5 | nack | I2C NACK |
| 6 | busy | Resource busy, retry later |
| 7 | nosup | Command known but not supported by this port layer |
| 8 | overflow | Line or buffer overflow |
| 9 | internal | Anything else |

Handlers are allowed to block briefly (a few ms bus timeout) inside the superloop;
this is accepted for v1 and must be documented in the firmware integration notes.

### 2.4 v1 command set

`ping`
: Response: `OK monitor 1 <name>` where 1 is the protocol version and `<name>` is a
  short project identifier from the port layer.

`info`
: Response: `OK up=<ms> <extra...>` where extra tokens come from an optional port hook
  (e.g. reset cause, firmware version). Unknown tokens must be tolerated by the daemon.

CAN (classic, bxCAN):

`can tx <id> <data|-> [flags]`
: Transmit. `<id>` hex. `<data>` hex pairs, 0 to 8 bytes; `-` means zero-length.
  `flags` optional token containing any of: `x` (29-bit extended id), `r` (RTR; data
  token then gives DLC as a single decimal digit instead of payload, e.g.
  `can tx 1A3 4 r` requests 4 bytes). Response: `OK` once queued/sent, or `ERR`.

`can filter <id> <mask> [flags]` / `can filter all` / `can filter none`
: Controls which received frames are streamed up as events. `all` is the default at
  boot. Matching is `(rx_id & mask) == (id & mask)`. Only one software filter slot is
  required in v1 (plus all/none); hardware filter usage is up to the port layer.
  Response: `OK`.

`can stat`
: Response: `OK rx=<n> tx=<n> err=<n> state=<active|passive|busoff>`.

I2C (master):

`i2c scan`
: 7-bit address sweep 0x08 to 0x77. Response: `OK 48 4A 68` (found addresses, hex,
  space separated; empty data section if none).

`i2c wr <addr> <data>`
: Write bytes. Response: `OK`.

`i2c rd <addr> <n>`
: Read `<n>` (decimal, 1 to 64) bytes. Response: `OK <data_hex>`.

`i2c wrrd <addr> <wr_data> <n>`
: Write then read with repeated start (the register-read idiom). Response:
  `OK <data_hex>`.

SPI (master):

`spi xfer <cs> <data>`
: Full-duplex transfer of the given bytes with chip-select `<cs>` (a name from the
  port layer's CS table, e.g. `imu`) asserted around the whole transfer. Response:
  `OK <miso_data_hex>` (same length as sent).

GPIO:

`gpio set <name> <0|1>`
: Response: `OK`.

`gpio get <name>`
: Response: `OK 0` or `OK 1`.

Names come from a port-layer table; unknown name is `ERR 2 badarg`.

ADC:

`adc read <name>`
: Response: `OK raw=<n> mv=<n>` (mv is optional; port layer may return raw only, in
  which case just `OK raw=<n>`).

Project-specific commands: the registration API (section 5) lets application code add
commands (e.g. `>7 sensor cal`); the daemon and CLI must pass arbitrary commands
through without a whitelist.

### 2.5 Events

```
!can <tick> <flags> <id> <data|->
```

- Emitted for each received CAN frame that passes the filter.
- `<tick>`: MCU milliseconds tick (decimal, wraps at 2^32) at reception.
- `<flags>`: `-` for none, else a token of `x` and/or `r` characters.
- RTR frames carry `<data|->` as a single decimal DLC digit, matching `can tx`.

Plot data (consumed by the phase 7 plot viewer; firmware may emit these from day
one). There are two formats sharing one downstream pipeline: an **ad-hoc** format
for zero-setup throwaway debugging, and **typed streams** for continuous signal
streaming (compact, supports floats without float printf, self-describing types).

Ad-hoc format:

```
!p <tick> <name>=<value> [<name>=<value> ...]
```

- `<tick>`: MCU milliseconds tick, decimal.
- `<value>`: decimal integer or fixed-point number (optional `-`, digits, optional
  `.` and digits), parsed as float64 on the host. Emitted with plain
  `monitor_eventf("p %lu ax=%ld", tick, ax_mg)`; intended for "watch this one
  variable for an hour" use, not sustained streams.

Typed streams (definition plus samples):

```
!pd <sid> <name>:<type>[*<scale>][:<unit>] [<name>:<type>... ...]
!ps <sid> <tick> <val>,<val>,...
```

- `<sid>`: stream id, a single ASCII digit `0` to `9`. Multiple streams may run
  concurrently with different layouts and rates (e.g. fast IMU stream, slow battery
  stream); ten is ample since each stream carries multiple channels.
- `<type>`: `u1 s1 u2 s2 u4 s4 f4` (unsigned/signed integer or IEEE754 float, size
  in bytes). `f4` is transmitted as its raw 32-bit pattern, so firmware needs no
  float printf.
- `<scale>` (optional): decimal float; the host multiplies the decoded value by it
  before storage/display. `<unit>` (optional): display label. Example:
  `ax:s2*0.00098:g`. Data lines carry no scale/unit cost.
- `<kind>` (optional): the `<unit>` slot may instead carry a leading sigil that
  selects a render kind other than plain analog, in place of a display unit:
  - `=<v>=<label>,<v>=<label>,...` declares an **enum/state** channel: each raw
    decoded integer is mapped to a label for display. Example:
    `state:u1:=0=IDLE,1=ARMED,4=RUN`. Valid on integer types only (not `f4`); the
    host stores the raw decoded value unscaled and looks up the label for display.
    `<v>` is a plain decimal integer with an optional leading `-` (no `+`, no `_`
    digit grouping); `<label>` is 1 to 16 chars from `[A-Za-z0-9_.]`. A `*<scale>`
    is meaningless on an enum (or bits) channel and makes the `!pd` line invalid.
  - `/<lane>,<lane>,...` declares a **packed bits** channel: the raw integer is
    expanded into one 0/1 channel per lane, LSB-first (the first name is bit 0).
    Example: `gpio:u1:/led,irq,pwm_en`. An empty name (`,,`) skips that bit without
    naming a channel. At most `<type>` width in bits lanes may be given, and at
    least one lane must be named. Valid on unsigned integer types only (not `f4` or
    signed types).
  - A malformed `<kind>` sigil (bad type, too many lanes, no lanes, bad label
    characters) makes the whole `!pd` line invalid; the daemon stores it as a
    generic event and the sid's previous definition (if any) is left in place.
  - The whole `!pd` line, including any enum labels or bit lane names, must still
    fit within the 255-byte line limit (SPEC 2.1).
- `!ps` values: fixed-width zero-padded uppercase hex, **big-endian** (natural
  reading order; emission cost is identical to little-endian, the encoder just walks
  each field's bytes in reverse), comma separated, in definition order. `<tick>` is
  unpadded hex (hex everywhere means no decimal division on the MCU). Example pair:

```
!pd 0 ax:s2*0.00098:g ay:s2*0.00098:g az:s2*0.00098:g
!ps 0 12D687 FC01,0200,4000
```

- The firmware re-emits `!pd` for each active stream roughly every 5 s, so a
  late-joining consumer (or restarted daemon) is blind for at most that long.
- Consumers cache the latest `!pd` per sid and decode `!ps` against it; an `!ps`
  with no known definition (or a token-count/width mismatch) is stored as a generic
  event row and skipped for decoding.
- Channel `<name>`: `[A-Za-z_][A-Za-z0-9_.]*`, at most 16 chars, and must be unique
  across all streams and ad-hoc names (the host keys channels by name alone).

Throughput: a tick plus four s2 channels is about 33 bytes/line, sustaining roughly
350 lines/s at 115200 baud and 8x that at 921600 (preferred for streaming-heavy
work; the format saves about 30% over decimal, the baud rate is the bigger lever).
Binary (non-line) streaming is **[P2]** and so far unjustified.

Other event types may be added later (`!gpio`, `!adc` for change notifications); the
daemon must store unknown `!` lines as generic events without failing. In the v1
core, all plot lines are stored as generic event rows; decoding into `plot_points`
(section 9.2) is added in phase 7.

Firmware rule: events must be emitted from main-loop context only. The CAN RX
interrupt (or driver) queues frames; the monitor drains that queue during
`monitor_poll()` via a shim getter and emits the event lines. This keeps IRQ context
out of the monitor entirely.

---

## 3. Host daemon: `mcuscoped`

### 3.1 Technology

- Python >= 3.11, cross-platform: Linux and Windows 10/11. Single package `mcuscope`
  in `host/`, one `pyproject.toml`, installable with `uv tool install mcuscope` or
  `pipx install mcuscope` once published (from a checkout: `uv tool install ./host` or
  `pipx install ./host`). The package version is single-sourced from
  `mcuscope/__init__.py` (hatchling dynamic version). Provides two console scripts:
  `mcuscoped` (daemon) and `mcu` (CLI).
- Dependencies (keep to exactly these plus their transitive deps):
  `pyserial`, `fastapi`, `uvicorn`, `typer`, `httpx`, `platformdirs`, `websockets`
  (or use FastAPI's WS support and drop the separate dep; implementer's choice).
  `sqlite3` from stdlib. `tomllib` from stdlib for reading config; `tomlkit` for the
  config write-back API (3.3.1), because it round-trips comments and formatting so a
  hand-edited file survives UI edits.
  Do NOT use `pyserial-asyncio` (unreliable on Windows). Serial I/O: one blocking
  reader thread per port pushing into the asyncio loop via
  `loop.call_soon_threadsafe`, and a writer path guarded by a lock (writes are
  small); thread lifecycle tied to attach/detach.
- Device strings are passed to `serial.serial_for_url`, so `COM7`, `/dev/ttyACM0`,
  and URLs like `socket://127.0.0.1:9000` (simulator, remote serial) all work. The
  API is unauthenticated, so device strings from the network are restricted to bare
  paths and the `socket://` / `rfc2217://` schemes; other `serial_for_url` schemes
  (notably `spy://...?file=`, which opens an arbitrary file for writing) and any `?`
  query options are rejected, and per-line writes are capped at the 255-byte limit.
- The default bind is `127.0.0.1`. Default port `8765`, overridable in config
  and by `MCUSCOPE_URL` for clients. Loopback clients are never authenticated; the
  local machine is the trust boundary. Because a web page the operator visits shares
  that boundary, the daemon enforces a **same-origin guard**: any HTTP or WebSocket
  request carrying an `Origin` that does not match its own `Host` is refused (403 /
  close), which blocks cross-site CSRF, cross-site WebSocket capture exfiltration,
  and DNS rebinding while leaving non-browser clients (the `mcu` CLI) unaffected.
- **LAN access + token** (`--token`, env `MCUSCOPED_TOKEN`): binding a
  non-loopback address (e.g. `0.0.0.0`) is supported for LAN use. The token is
  **runtime-only**: it is passed via the environment variable (preferred; not visible
  in the process list) or the `--token` flag, and is deliberately **not** a config-file
  key, so the UI-writable config surface can never grant, change, or remove
  authentication. When a token is set, every request or WebSocket handshake from a
  non-loopback client must present
  it: `Authorization: Bearer <token>` or `X-Auth-Token` header, or `?token=` query
  parameter (WebSocket only, since browsers cannot set WS headers). Failures get a 401
  `{"error": ...}` envelope (WS: close 1008). Token comparison is constant-time.
  Wrong-token attempts are rate limited per client address (10 failures within 60 s
  locks the address out for 60 s: HTTP 429 with `Retry-After`, WS close 1013, no
  comparison performed while locked), so an online brute force is throttled to a rate
  at which any realistic token is unguessable. Requests carrying **no** token do not
  count toward the lockout, and a correct token clears the address's failure record. The
  static UI files (`/`, `/ui/...`) are served without the token so the page can load
  and then prompt for it; all API and WS traffic is protected. Clients pass it via
  `mcu --token` / env `MCUSCOPE_TOKEN`, the web UI stores it in localStorage after
  prompting. Binding non-loopback **without** a token prints a loud startup warning
  and serves unauthenticated; do that only on a trusted network.
- User-supplied `match` regexes (`/lines`, `/wait`) are evaluated off the event-loop
  thread (a worker executor, with a private read connection for `/lines`), so a slow
  or catastrophic-backtracking pattern ties up a worker but can never stall ingestion,
  the loop, or other clients. A `MAX_MATCH_LEN` cap bounds the pattern length as a
  first gate. (The stdlib `re` engine cannot be interrupted mid-backtrack; off-loading
  keeps the daemon responsive without a non-stdlib regex-timeout dependency.)

### 3.2 Responsibilities

1. Open and own one or more serial ports; auto-reconnect with backoff when a device
   disappears and reappears. Device identity across replug: on Linux prefer
   `/dev/serial/by-id/...` paths in config; on either OS a port may instead specify
   `serial_number`, which the daemon resolves to a device via pyserial `list_ports`
   at each (re)connect attempt.
2. Split the RX byte stream into lines, classify each (`debug`, `resp`, `event`),
   timestamp on arrival, decode known events (CAN), and append everything to SQLite.
   Also log every TX line (`cmd` or raw `send`) and internal notices (`sys` channel:
   port opened/lost, daemon start/stop) and user annotations (`marker` channel).
3. Manage command sequence numbers and match responses: one in-flight command per
   port at a time (serialize with an asyncio lock; queue further commands). On
   timeout, mark the seq dead so a late response is logged but not delivered.
4. Serve the REST + WebSocket API below.
5. Enforce a retention policy: delete `lines` (and cascaded `can_frames`) older than
   `retention_days` (default 7) on startup and hourly; `PRAGMA journal_mode=WAL`.
   Optionally also enforce a size cap, `storage.max_db_bytes` (default 0, meaning no
   cap), checked once a minute: while live content exceeds it, trim the **oldest** lines
   until the capture is back under 90% of the cap, and record a `sys` row saying how many
   were lost. The cap is measured against live content (allocated pages minus the
   freelist), not the file size, because SQLite reuses freed pages rather than shrinking:
   a file-size cap would keep reading "too big" after each trim and delete until the
   capture was empty. Age retention is the primary bound; the size cap is an opt-in
   disk-space guard.

### 3.3 Configuration

Config lives at `platformdirs.user_config_dir("mcuscope")/config.toml`
(`~/.config/mcuscope/config.toml` on Linux,
`%APPDATA%\mcuscope\config.toml` on Windows); the default db path uses
`platformdirs.user_data_dir("mcuscope")`. `mcuscoped --config PATH` (or env
`MCUSCOPED_CONFIG`) selects a different file, which is how multiple setups are kept.
All keys optional; a missing file is valid (defaults, no ports), so a first run needs
no setup beyond starting the daemon and opening the UI:

```toml
[server]
host = "127.0.0.1"      # bind 0.0.0.0 for LAN access (set a token!)
port = 8765

[storage]
db_path = ""            # default: <user_data_dir>/mcuscope/capture.db
retention_days = 7
max_db_bytes = 0        # 0 = no size cap; when set, the oldest lines are trimmed

[[ports]]
alias = "board"                          # name used by clients
device = "/dev/serial/by-id/usb-STM..."  # or COM7, /dev/ttyACM0, socket://127.0.0.1:9000
# serial_number = "066BFF3..."           # alternative to device: resolve via USB
                                         # serial number at each (re)connect,
                                         # stable on both Linux and Windows
baud = 115200
autoconnect = true
```

The access token is **not** a config key (see 3.1); a `server.token` key found in the
file is ignored with a warning pointing at `MCUSCOPED_TOKEN`.

Ports attached/detached at runtime via `POST /ports` / `DELETE /ports/{alias}` remain
ephemeral; persistence is explicit, via the config endpoints below (the UI's attach
dialog offers a "save to config" option that simply also updates the saved ports list).

#### 3.3.1 Config write-back API

The daemon can edit its own config file so the whole setup is drivable from the UI,
while the file stays hand-editable:

- Saving is **read-modify-write**: the current file is re-parsed with `tomlkit` at
  save time, only the affected keys are changed, and the result is written atomically
  (temp file + `os.replace`) with the parent directory created if needed. Comments,
  ordering, and unknown keys elsewhere in the file survive, including hand edits made
  while the daemon is running. Exception: `PUT /config/ports` replaces the whole
  `[[ports]]` array-of-tables, so comments inside individual port tables are not
  preserved.
- The endpoints validate with the same rules as the loader (alias grammar, device or
  serial_number required, bounds on port/baud/retention), so the UI can never write
  entries the loader would skip.
- Saved config vs running state: edits take effect live where possible
  (`retention_days`, `max_db_bytes`, the ports list on next attach), but `server.host`,
  `server.port`, and `storage.db_path` only apply on restart. Responses and
  `GET /config` carry `restart_required: true` whenever a saved value differs from
  the running one; the UI shows a persistent "restart to apply" badge. The daemon
  does not restart itself.
- **Write protection**: `PUT /config/*` requests from non-loopback clients are
  refused with 403 when no token is set, even though the rest of the API serves
  unauthenticated in that mode (config write includes the bind address and a file
  path, so it is held to a higher bar). Loopback clients are always allowed; with a
  token set, the normal token rule applies.

`GET /config`
: The **saved** config (the file), not runtime state:
  `{"path": "...", "exists": bool, "server": {"host":..., "port":...},
  "storage": {"db_path":..., "retention_days":...}, "ports": [{...}],
  "token_set": bool, "restart_required": bool}`. Never includes a token value.

`PUT /config/server {host, port}` / `PUT /config/storage {db_path, retention_days}`
: Update one section. Returns `{"ok": true, "restart_required": bool}`.

`PUT /config/ports {ports: [{alias, device?, serial_number?, baud?, autoconnect?}]}`
: Replace the saved ports list. Returns `{"ok": true, "restart_required": false}`
  (ports apply live; the daemon does not auto-attach on save).

### 3.4 REST API

All request/response bodies are JSON. Errors: appropriate HTTP status plus
`{"error": "message"}`. Times in queries are either absolute unix seconds (float) or
relative via `last_ms`.

`GET /status`
: `{"version": ..., "uptime_s": ..., "db_path": ..., "db_size_bytes": ...,
   "db_max_bytes": n, "lines_trimmed": n,
   "ports": [{"alias": "board", "device": ..., "baud": ..., "connected": true,
   "lines_rx": n, "lines_tx": n, "rx_dropped": n}]}`
  `db_size_bytes` is disk usage (database plus its `-wal`). `db_max_bytes` is the
  configured size cap (0 = none) and `lines_trimmed` counts the oldest lines it has
  removed. `rx_dropped` is the running count of received lines shed because storage could
  not keep up (SPEC 3.2 drop-oldest); non-zero means the capture has holes.

`GET /ports` / `POST /ports {alias, device, baud}` / `DELETE /ports/{alias}`
: List, attach, detach. Attaching with an existing alias replaces that attachment
  (this is how a baud change is done).

`POST /ports/{alias}/reconnect`
: Re-attach the named port with its own stored parameters (device/baud/serial_number),
  tearing down the old reader and retrying immediately - skips the reconnect backoff
  after e.g. replugging a device. Returns `{"port": {...}}` like attach; 400 for an
  unknown alias.

`GET /devices`
: Enumerate candidate serial devices on the host via pyserial `list_ports`:
  `{"devices": [{"device": "/dev/ttyACM0" | "COM7",
  "by_id": "/dev/serial/by-id/..." | null, "description": "...",
  "vid_pid": "0483:374B" | null, "serial_number": "066BFF3..." | null}]}`. Feeds
  the UI attach dialog and future CLI completion.

`GET /config` / `PUT /config/server` / `PUT /config/storage` / `PUT /config/ports`
: Read and edit the saved config file; see 3.3.1.

`POST /send {port, line}`
: Write one raw line (LF appended if missing) with no seq management, logged as
  chan `cmd`, seq null. Returns `{"ok": true}`. This is the escape hatch for
  non-monitor firmware.

`POST /cmd {port, cmd, timeout_ms=1000}`
: `cmd` is the command text WITHOUT `>` and seq (e.g. `"i2c rd 48 2"`). The daemon
  assigns a seq, sends, and waits for the matching response or timeout. Returns:
  ```json
  {"status": "ok" | "err" | "timeout",
   "seq": 17,
   "data": "raw-token-string-after-OK",        // when ok, may be ""
   "err_code": 5, "err_name": "nack", "err_detail": "...",   // when err
   "latency_ms": 12.3,
   "line_id": 12345}                            // lines.id of the response row
  ```

`GET /lines?port=&chan=&match=&since_id=&since_ts=&last_ms=&limit=100&order=desc`
: Query the capture. `match` is a Python regex applied to `raw`. `chan` may repeat.
  Returns `{"lines": [{"id":, "ts":, "port":, "dir":, "chan":, "seq":, "raw":}, ...],
  "truncated": bool}`. Hard cap `limit` at 1000.

`GET /can/frames?port=&id=&last_ms=&since_id=&limit=100`
: Decoded CAN view, same envelope with
  `{"line_id":, "ts":, "tick_ms":, "can_id":, "ext":, "rtr":, "dlc":, "data_hex":}`.
  `id` accepts hex like `0x1A3` or `1A3`.

`POST /wait {port, match, timeout_ms=2000, send=null, chan=null, since="now"}`
: The key AI primitive. Optionally send `send` first: if `send` looks like a monitor
  command (client sets `send_mode`: `"cmd"` or `"raw"`, default `"cmd"`), route it
  through the seq machinery. Then block until a line matching regex `match`
  (optionally restricted to channel `chan`) arrives with `lines.id` greater than the
  position captured at call start, or timeout. Returns
  `{"status": "match" | "timeout", "line": {...} | null, "waited_ms": ...,
    "cmd_result": {...} | null}`.

`POST /marker {port=null, text}`
: Insert an annotation row (chan `marker`). Returns `{"line_id": ...}`.

`GET /sessions?limit=` / `POST /sessions {name, note}` / `POST /sessions/stop` /
`DELETE /sessions/{id}`
: Sessions name a span of the capture so one run can be queried and exported on its own.
  A session is stored as an id range over the single capture timeline, not as a column on
  every line: nothing is written per row, existing captures need no migration, and
  scoping rides the primary key. The cost is that sessions cannot overlap or nest -
  starting one closes the running one. Starting and stopping each write a `marker` row,
  so a run's boundaries are visible in the terminal. `GET` returns recent sessions with
  the number of lines still stored for each (retention can remove a finished run's lines,
  and it then reads as 0 rather than claiming rows that are gone) plus the running one.
  `DELETE` forgets the label only; the captured lines are untouched.

  `/lines`, `/can/frames`, `/plot/series` and `/plot/export` accept `session=<id|name>`
  (a name resolves to the newest match). An unknown reference matches nothing rather than
  widening to the whole capture, so a typo cannot hand back every line ever stored.

`GET /ws?port=`
: WebSocket; streams every new line row as it is stored (optionally filtered by port).
  Each message is a **JSON array** of one or more row objects: the daemon coalesces rows
  that are already queued for a subscriber into a single frame, so a burst costs one
  encode and one write instead of one per line. Clients must iterate the array. Used by
  `mcu tail -f` and the web UI.

### 3.5 Storage schema

```sql
CREATE TABLE lines(
  id     INTEGER PRIMARY KEY,
  ts     REAL    NOT NULL,             -- unix epoch, host receive/send time
  port   TEXT    NOT NULL,             -- alias; '' for daemon-level sys/marker rows
  dir    TEXT    NOT NULL CHECK(dir IN ('rx','tx','-')),
  chan   TEXT    NOT NULL CHECK(chan IN ('debug','cmd','resp','event','marker','sys')),
  seq    INTEGER,                      -- for cmd/resp rows
  raw    TEXT    NOT NULL              -- full line, terminator stripped
);
CREATE INDEX idx_lines_ts ON lines(ts);
CREATE INDEX idx_lines_chan_id ON lines(chan, id);   -- id, not ts: /lines orders by id

CREATE TABLE sessions(
  id         INTEGER PRIMARY KEY,
  name       TEXT    NOT NULL,
  note       TEXT    NOT NULL DEFAULT '',
  started_ts REAL    NOT NULL,
  ended_ts   REAL,                       -- NULL while the session is running
  start_id   INTEGER NOT NULL,           -- first lines.id in the session (inclusive)
  end_id     INTEGER                     -- last lines.id (inclusive); NULL while running
);
CREATE INDEX idx_sessions_name ON sessions(name, id);

CREATE TABLE can_frames(
  line_id INTEGER PRIMARY KEY REFERENCES lines(id) ON DELETE CASCADE,
  tick_ms INTEGER,
  can_id  INTEGER NOT NULL,
  ext     INTEGER NOT NULL DEFAULT 0,
  rtr     INTEGER NOT NULL DEFAULT 0,
  dlc     INTEGER NOT NULL,
  data    BLOB
);
CREATE INDEX idx_can_id_line ON can_frames(can_id, line_id);
```

A malformed `!can` line must still be stored as a `lines` row (chan `event`) even if
decoding into `can_frames` fails; log a `sys` row noting the decode failure once per
burst, not per line.

---

## 4. CLI: `mcu`

Thin HTTP client of the daemon. Global options: `--json` (machine output),
`--port/-p ALIAS` (defaults to the only attached port; error if ambiguous),
`--url` / env `MCUSCOPE_URL`.

Exit codes (contract for AI use): `0` success/match, `1` error (bus ERR, HTTP error,
bad usage), `2` timeout, `3` daemon unreachable.

| Command | Behavior |
|---|---|
| `mcu status` | Daemon + port health |
| `mcu ports` / `mcu attach DEV [--baud N] [--alias A]` / `mcu detach A` | Port management |
| `mcu cmd "i2c rd 48 2" [--timeout MS]` | Send monitor command, print response data (or ERR to stderr) |
| `mcu send "raw text"` | Raw line, no response wait |
| `mcu tail [-n N] [-f] [--chan C] [--match RE]` | Recent lines / follow via WS; human format `HH:MM:SS.mmm chan| raw` |
| `mcu lines --last-ms MS [--chan C] [--match RE] [--limit N] [--since-id N]` | Query capture (the AI workhorse) |
| `mcu wait --match RE [--timeout MS] [--send CMD] [--chan C]` | The wait primitive; prints matching line |
| `mcu can tx ID [DATA] [--ext] [--rtr N]` | Sugar for `cmd "can tx ..."` |
| `mcu can dump [--id ID] [--last-ms MS] [-n N] [-f]` | Decoded CAN frames from capture |
| `mcu can stat` / `mcu can filter ...` | Pass-through sugar |
| `mcu i2c scan` / `mcu i2c rd ADDR N [--reg HEX]` / `mcu i2c wr ADDR DATA` | Sugar; `--reg` uses `wrrd` |
| `mcu spi xfer CS DATA` | Sugar |
| `mcu gpio set NAME 0|1` / `mcu gpio get NAME` / `mcu adc read NAME` | Sugar |
| `mcu mark "text"` | Insert marker |
| `mcu log export [--last-ms MS] [-o FILE]` | Dump matching lines as JSONL or text |
| `mcu daemon start|stop|status` | Convenience: spawn/kill mcuscoped as a detached process, cross-platform (start_new_session on POSIX, DETACHED_PROCESS on Windows); a systemd user unit is also provided as a Linux convenience |
| `mcu ai-guide` | Print a compact usage guide written for an AI agent (see 6) |

With `--json`, every command prints exactly one JSON object (the API response,
lightly wrapped), no prose.

Phase 7 adds `mcu plot channels` and `mcu plot export` (see section 9.2).

---

## 5. Firmware monitor module

### 5.1 Files and portability rules

```
firmware/monitor/monitor.h          public API + shim declarations (the contract)
firmware/monitor/monitor.c          core: line assembly, parse, dispatch, response/event formatting
firmware/monitor/monitor_cmds.c     built-in v1 command handlers (can/i2c/spi/gpio/adc/ping/info)
firmware/monitor/port_template/monitor_port_template.c   every shim stubbed with TODOs
firmware/monitor/INTEGRATION.md     step-by-step integration into an existing STM32 LL project
```

Core rules: C99, no dynamic allocation, no HAL/LL/CMSIS includes anywhere in
`monitor.c`/`monitor_cmds.c`, no floating point, static buffers only, main-loop
context only. Target footprint: roughly 4 KB flash, under 1 KB RAM (two 256-byte
line buffers plus a small CAN RX queue owned by the port layer).

### 5.2 Public API (contract; implement exactly this)

```c
// monitor.h
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#define MONITOR_LINE_MAX 255
#define MONITOR_PROTO_VERSION 1

typedef struct {
    // Pull up to max bytes from the UART RX circular buffer. Returns bytes copied.
    size_t   (*uart_read)(uint8_t *buf, size_t max);
    // Push one complete line (includes trailing \n) atomically to the TX circular
    // buffer. Returns false if it does not fit (monitor drops the line and counts it).
    bool     (*uart_write)(const uint8_t *buf, size_t len);
    uint32_t (*tick_ms)(void);
    const char *name;        // short project id for `ping`
} monitor_port_t;

void monitor_init(const monitor_port_t *port);
// Call from the superloop. Drains RX, dispatches at most one command per call,
// drains the CAN RX queue into events. Cheap when idle.
void monitor_poll(void);

// --- extending the command set (application code) ---
// argv[0] is the command name; write the OK payload into resp (no "OK" prefix,
// no newline). Return 0 for OK, or a MONITOR_ERR_* code.
typedef int (*monitor_handler_t)(int argc, char **argv,
                                 char *resp, size_t resp_max);
bool monitor_register(const char *name, monitor_handler_t fn);   // static table, N=8 extra slots

// Emit an async event line "!<fmt...>" from main-loop context.
void monitor_eventf(const char *fmt, ...);

// --- typed plot streams (protocol 2.5) ---
typedef struct {
    char        sid;    // stream id digit '0' to '9'
    const char *body;   // definition body, e.g. "ax:s2*0.00098:g ay:s2*0.00098:g"
} mon_plot_def_t;
// Emit one "!ps" sample line. data points at a packed little-endian struct whose
// fields match the definition in order; len must equal the summed field sizes
// (else MONITOR_ERR_BADARG). The monitor parses each stream's definition once,
// on first use (static registry, max 4 streams), caching field widths; it emits
// each field as big-endian hex and re-emits the "!pd" definition line
// automatically every 5 s while the stream is active. Main-loop context only.
// Returns 0 or MONITOR_ERR_*.
// Performance contract: after the first call per stream, the hot path is a
// length check, nibble-lookup-table hex encoding into a static line buffer, and
// one uart_write call. No printf/snprintf, no division, no allocation. Order of
// a few hundred cycles for a typical 4-channel line.
int monitor_plot(const mon_plot_def_t *def, uint32_t tick,
                 const void *data, size_t len);

#define MONITOR_ERR_BADCMD   1
#define MONITOR_ERR_BADARG   2
#define MONITOR_ERR_TIMEOUT  3
#define MONITOR_ERR_BUSERR   4
#define MONITOR_ERR_NACK     5
#define MONITOR_ERR_BUSY     6
#define MONITOR_ERR_NOSUP    7
#define MONITOR_ERR_OVERFLOW 8
#define MONITOR_ERR_INTERNAL 9
```

### 5.3 Bus shims (owner implements per project against own drivers)

Declared in `monitor.h`, referenced by `monitor_cmds.c`, defined in the project's
`monitor_port.c`. Every shim has a default weak (or `#ifdef`-selected stub)
implementation returning `MONITOR_ERR_NOSUP`, so a project that has no SPI simply
never defines `mon_spi_xfer` and the command answers `ERR 7 nosup`.

```c
typedef struct {
    uint32_t id;
    uint8_t  dlc;
    uint8_t  data[8];
    bool     ext;
    bool     rtr;
    uint32_t tick_ms;       // set by the driver at reception
} mon_can_frame_t;

int  mon_can_tx(const mon_can_frame_t *f);                       // ERR_* or 0
bool mon_can_rx_pop(mon_can_frame_t *f);                         // drain driver's RX queue
int  mon_can_filter(uint32_t id, uint32_t mask, bool ext);       // software filter is fine
int  mon_can_stat(uint32_t *rx, uint32_t *tx, uint32_t *err, const char **state);

int  mon_i2c_xfer(uint8_t addr7,
                  const uint8_t *wr, size_t wr_len,              // may be 0
                  uint8_t *rd, size_t rd_len);                   // may be 0; both = wrrd
int  mon_spi_xfer(const char *cs_name,
                  const uint8_t *tx, uint8_t *rx, size_t len);
int  mon_gpio_set(const char *name, bool level);
int  mon_gpio_get(const char *name, bool *level);
int  mon_adc_read(const char *name, int32_t *raw, int32_t *mv);  // *mv = INT32_MIN if n/a
int  mon_info_extra(char *buf, size_t max);                      // optional tokens for `info`
```

`i2c scan` is implemented in `monitor_cmds.c` as a loop of zero-length
`mon_i2c_xfer` probes (wr_len 0, rd_len 0 means address-probe; shim returns 0 on ACK,
ERR_NACK otherwise). Document this convention prominently in the shim comments.

### 5.4 Parser notes

- Line assembly: accumulate into a static buffer until LF; on overflow, discard until
  next LF, then respond `ERR 8 overflow` if a seq was parseable from the discarded
  prefix, else stay silent.
- Ignore lines not starting with `>`.
- Tokenize in place (replace spaces with NUL); max 12 tokens.
- Dispatch: two-level lookup, first token then optional second token, over one static
  table of `{ "can", "tx", handler }`-style rows; registered app commands match on
  first token only.
- Responses are formatted into the outgoing line buffer and pushed via `uart_write`
  in one call.

---

## 6. AI integration

Two artifacts, both part of v1:

1. `mcu ai-guide`: prints roughly 60 lines covering: what the daemon is, the exit-code
   contract, `--json`, and one example per major command, with the send-and-wait and
   lines-query patterns emphasized. This lets an agent that only knows "run
   `mcu ai-guide`" self-serve the details on demand instead of bloating CLAUDE.md.
2. `docs/CLAUDE_SNIPPET.md`: a short block (under 15 lines) the owner pastes into
   `~/.claude/CLAUDE.md`, saying: hardware debug bridge available; check `mcu status`;
   run `mcu ai-guide` for usage; typical loop is `mcu cmd`, `mcu wait`, `mcu lines`;
   always prefer `--json`.

**[P2]** MCP wrapper: a separate small stdio MCP server exposing `cmd`, `wait`,
`lines`, `can_dump`, `marker` as typed tools, calling the same REST API. Nothing in
v1 may preclude this.

---

## 7. MCU simulator (required for development and CI)

The simulator lives in the host package as `mcuscope.sim` (console script `mcu-sim`;
`tools/mcu_sim.py` remains as a source-checkout shim), so an installed daemon can run
it in-process: `mcuscoped --sim` starts it on an ephemeral port and autoconnects to it
as port `sim` - the zero-hardware demo path. It speaks the full monitor protocol over
one of two transports:

- Default (cross-platform): a TCP listener on `127.0.0.1` (port via `--tcp-port`,
  default 9900, `0` for ephemeral with the chosen port printed); the daemon attaches
  with `device = "socket://127.0.0.1:<port>"`. Tests use this mode on all platforms.
- `--pty` (POSIX only): opens a pty pair and prints the slave path, for attaching
  exactly like a real `/dev/tty*` device.

Behavior on either transport:

- `ping`, `info` per spec.
- A fake I2C bus: device at 0x48 acting like a simple temperature sensor (reg 0x00
  reads two bytes, value slowly drifting), device at 0x50 acting like a small EEPROM
  (readable/writable 256-byte array). `i2c scan` finds exactly these.
- SPI: echoes TX inverted (`rx[i] = ~tx[i]`), cs names `imu` and `flash`.
- GPIO: names `led`, `en_5v` (state retained); ADC: name `vbat` returning a slightly
  noisy value around 3300 mV.
- CAN: accepts `can tx`; emits a periodic `!can` heartbeat frame (id 0x100, 10 Hz,
  counter payload) and echoes any transmitted frame back with id+1 after 20 ms.
- Emits a debug line every 2 s (`sim alive n=<count>`), and a burst of debug lines
  immediately after any `gpio set` (to exercise interleaving).
- Flags to inject faults: `--drop-response N` (swallow the response to the Nth
  command), `--garbage` (occasionally emit binary junk), `--rtr` etc. as needed by
  tests.
- `--plot`: exercise both plot formats: ad-hoc `!p` lines at 20 Hz with two channels
  (`sine` and `noisy`, the second being the first plus noise), and a typed stream
  (`!pd 0 tri:s2*0.01:V ramp:u2 ftest:f4` with `!ps` samples at 20 Hz, ftest being a
  slow sine so f4 decode is visually verifiable), including the 5 s `!pd`
  rebroadcast. A `--plot-late-def` flag delays the first `!pd` by 5 s to test the
  undecodable-sample path.
- `--flood N`: emit N extra plain debug lines per second, catching up on whatever is
  owed since the last serve pass so the requested rate is met regardless of poll
  timing. This is how the capture path and the web UI's high-rate behaviour are
  exercised without a real board that can saturate a link.

The simulator doubles as executable documentation of the protocol and lets the owner
try the whole system with zero hardware on either OS: `mcuscoped` attaches to the
sim's TCP socket (or pty) exactly as it would a real port.

---

## 8. Testing strategy

- `host/tests/test_protocol.py`: pure unit tests for line classification, command
  formatting, response parsing, `!can` decoding, seq lifecycle including timeout and
  late-response handling.
- `host/tests/test_e2e.py`: pytest fixture launches `mcu_sim.py` (TCP mode, ephemeral
  port) and `mcuscoped` (ephemeral port, temp db), then exercises the REST API and
  the CLI (via subprocess) end to end: cmd ok/err/timeout paths, wait with and
  without send, lines queries, can dump, marker, WS tail, sim fault flags, and
  reconnect behavior when the sim's TCP connection drops and the listener returns.
  Runs on both Linux and Windows. A small POSIX-only test additionally attaches via
  the sim's `--pty` mode to keep the real-tty path honest (skip on Windows).
- Firmware: `monitor.c`/`monitor_cmds.c` must compile with a host compiler; provide
  `firmware/tests/` with a tiny host-side harness (fake shims, feed lines in, assert
  responses) run by the same pytest suite via a makefile or ctest. This keeps the
  parser honest without hardware.
- Real-hardware smoke checklist in `INTEGRATION.md` (manual, not CI).

---

## 9. Web UI (phases 6 and 7)

The UI is a browser page served by `mcuscoped` itself: an enhanced serial terminal
for viewing traffic and decoded data, occasional manual commands, port setup, and
(phase 7) realtime plotting. It is purely another client of the REST/WS API and must
not add any code paths to the serial or storage core beyond the endpoints already
specified (plus the plot ingest in 9.2).

### 9.1 Phase 6: terminal, setup, decoded CAN view

Technology constraints: static files in `host/mcuscope/webui/` mounted by FastAPI at
`/ui` (redirect `/` to `/ui`). **No build step, no npm, no CDN or network fetches**
(must work offline): one `index.html`, one `app.js` (vanilla JS, ES modules allowed),
one `style.css`. Size guidance: roughly 1200 lines total; no framework. Dark theme
default (it is a terminal, after all).

Panels:

- **Status / setup bar**: daemon version and uptime; one chip per port showing alias,
  device, baud, connected state. "Attach" opens a dialog: device dropdown populated
  from `GET /devices` (show description and by-id path), baud dropdown (9600, 19200,
  38400, 57600, 115200, 230400, 460800, 921600, 1M, 2M, 3M, plus a custom field),
  alias text field. Detach button per port. Errors from the API shown inline.
- **Terminal view**: one or more independently-filtered terminal panes laid out side
  by side. Add a pane or close one at any time (minimum one pane), so the operator can
  watch, say, "board-a CAN events" next to "sim debug" next to "everything". Each pane
  owns its filter controls (port selector, channel checkboxes, client-side regex match)
  and its autoscroll state. A single shared toolbar control toggles relative timestamps
  for all panes at once (with a common zero anchor), alongside pause-all and clear-all.
  All panes are fed from a single
  shared client-side line buffer: on load the page backfills the last 200 lines from
  `GET /lines` and then appends live from one `/ws` subscription (all ports); each pane
  renders the subset of that buffer matching its filter, keeping at most 5000 lines in
  view (drop oldest). Lines are color-coded by channel (debug, cmd, resp, event, marker,
  sys) with `HH:MM:SS.mmm` timestamps; when a pane's port filter is "all", each line is
  prefixed with a small colored port tag. Autoscroll is on by default and pauses
  automatically when the user scrolls up. While paused the pane is frozen and its
  scrollbar stays put; new matching lines are only counted on a "jump to latest" control.
  Resuming (that control, the pause pill, or scrolling back to the bottom) folds the
  buffered lines in and snaps to the newest. "Clear view" clears that pane's screen only,
  never the database. Pane layouts persist in localStorage.
- **Command box**: single input with a cmd/raw mode toggle. cmd mode posts to
  `POST /cmd` (timeout field, default 1000 ms) and renders the response inline
  (ok/err/timeout distinct); raw mode posts to `POST /send`. Up/down arrow history,
  persisted in localStorage.
- **CAN panel**: live table keyed by CAN id, built client-side from `!can` events on
  the WebSocket: id (hex, ext/rtr flags), dlc, latest data, message count, estimated
  period in ms (EWMA of inter-arrival), age since last seen. Reset button clears the
  table. This gives the classic CAN-tool "latest state per id" view.
- **Marker**: text field plus button posting to `POST /marker`; markers render as
  distinct divider lines in the terminal view.
- **Settings page**: edits the saved config via the 3.3.1 endpoints, so a fresh
  install is fully configurable from the browser. Sections: server (bind host,
  port), storage (db path, retention days), and the saved ports list (add/edit/
  remove rows; device dropdown fed by `GET /devices`, or a serial_number field).
  Shows the config file path, an "auth: token set / not set" indicator (read-only),
  and a persistent "restart daemon to apply" badge while `restart_required` is true.
  The attach dialog gains a "save to config" checkbox that updates the saved ports
  list alongside the runtime attach.

### 9.2 Phase 7: realtime plotting

- Charting library: **uPlot**, vendored into `webui/vendor/` (single minified JS
  plus one CSS file, MIT licensed, no dependencies). It comfortably handles
  realtime strip charts with 100k+ points; do not substitute a heavier library.
- Daemon ingest: decode both plot formats (grammar in 2.5) on arrival, same pattern
  as `can_frames`. Ad-hoc `!p` pairs decode directly; `!ps` decodes against the
  latest cached `!pd` for its sid (types, big-endian fixed-width hex, scale applied
  before storage; stored `value` is the scaled float64). On startup the daemon
  primes the definition cache by scanning recent stored lines for the newest `!pd`
  per sid, so a restart mid-stream recovers without waiting for the rebroadcast.

```sql
CREATE TABLE plot_points(
  line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
  tick_ms INTEGER,
  sid     TEXT,                -- NULL for ad-hoc !p points
  name    TEXT NOT NULL,
  value   REAL NOT NULL
);
CREATE INDEX idx_plot_name_line ON plot_points(name, line_id);
```

- New endpoints: `GET /plot/channels` (distinct names with sid, unit, scale, type
  where known from the definition cache, last value, point count) and
  `GET /plot/series?name=&last_ms=&since_id=&limit=10000&decimate=N` (history;
  `decimate` > 1 reduces a long window by **min/max** decimation: buckets of N points,
  each contributing its lowest and highest sample, so a transient still shows as a spike
  instead of aliasing away between kept samples. A bucket yields up to 2 points, so the
  reduction is about N/2). Live data comes from the existing WebSocket; no new
  streaming path.
- CSV export (required, not optional): `GET /plot/export?names=&last_ms=&format=long|wide`
  streaming CSV. `long` is `ts,tick_ms,sid,name,value` one point per row; `wide`
  requires all requested names to share one sid and emits `ts,tick_ms,<name>,...`
  one sample line per row. Exposed as a UI export button (current window, checked
  channels) and CLI `mcu plot export --names a,b --last-ms N [--wide] -o file.csv`.
- CLI also gains `mcu plot channels` (list) for discoverability.
- UI plot panel: **one chart per stream** (sid), plus one chart for ad-hoc `!p`
  channels, stacked vertically with a shared, synchronized x axis (linked cursor
  and zoom). Streams may have very different sample rates, and every point carries
  its own timestamp, so per-stream charts are the default organization, not a
  correctness requirement. Within each chart: channel checkboxes (auto-discovered
  from incoming events and `/plot/channels`, showing units), selectable time window
  (5 s, 30 s, 5 min), pause/resume, cursor value readout with unit. Client keeps a
  ring buffer per channel (cap around 100k points). Channels with very different ranges
  get independent y scales (the y axis is left undrawn; values are read from the legend),
  and traces are stepped (hold-last), not linearly interpolated.
- Time base: a single control shared with the terminal selects **host receive time**, **MCU
  tick**, or **relative** (relative time and tick both zero at a common reset point), and it
  drives both the pane timestamp column and the plot x axis at once, so the two views always
  read the same clock. The plot cursor is linked across all charts (shared x) and can also be
  driven by hovering a line in the terminal, which places every chart's cursor at that line's
  time.
- Overlaying channels from different streams on one chart is nice-to-have:
  implement only if trivial, otherwise leave as **[P2]**.

## 10. Later phases (design intent, do not build in v1)

- **[P2] Flash + reset**: config gains `[tools]` with command templates
  (`openocd`/`st-flash`/`probe-rs`); daemon endpoints `POST /flash {port, file}` and
  `POST /reset {port}` that pause the serial port, shell out, resume, and log a `sys`
  row; CLI `mcu flash FILE`, `mcu reset`. This enables the autonomous
  edit-build-flash-test loop.
- **[P2] MCP wrapper** (section 6).
- **[P2] DBC / register-map decoding**: optional `dbc` path per port; decoded signal
  text stored alongside frames; `mcu can dump --decode`.
- **[P2] pytest plugin**: fixtures wrapping the REST API for hardware-in-the-loop
  regression tests.
- **[P2] CAN FD**: extend flags token and payload lengths; schema already stores dlc
  and blob so only protocol and firmware change.
- **[P2] RTT transport** as an alternative byte source behind the same port
  abstraction.
- **[P2] Binary high-rate plot streaming** if the text `!p` format ever becomes the
  bottleneck (only relevant well above 115200 baud or a few hundred points/s).
- **[P2] OS-level autostart**: `systemctl --user enable` helper on Linux, Task
  Scheduler or startup-shortcut helper on Windows.
