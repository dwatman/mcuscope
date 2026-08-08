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
  POSIX-only piece is the simulator's optional pty mode, so tests reach the simulator
  in process or over TCP, and the pty-specific ones skip on Windows.
  Serial I/O uses plain pyserial with a reader thread per port (NOT pyserial-asyncio,
  whose Windows support is unreliable).
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
- Decimal tokens (seq, error code, ticks, counts, enum values) are ASCII `0`-`9` only,
  with an optional leading `-` where a negative value is meaningful. Another script's
  decimal digits are not accepted anywhere, in either direction, even though a permissive
  `int()`/`atoi()` would convert them. A receiver may bound the digit count; the host
  bounds it at 20, past any value the protocol carries and well inside what its integer
  parser will convert.
- Hex data payloads are uppercase or lowercase hex pairs with no separators or `0x`
  prefix (e.g. `DEADBEEF`). IDs and addresses are emitted as hex without `0x`; a receiver
  may accept an optional `0x`/`0X` prefix on them (both reference implementations do).
  Data payloads never carry a prefix.

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
this; do not enforce). A firmware also sanitizes each outgoing line: any byte outside
printable ASCII is replaced before the line is pushed, so application text reaching an
event or response payload cannot embed an LF and forge a second protocol line.

PC to MCU traffic other than `>` commands is permitted (raw lines via the daemon's
`send` API, for talking to non-monitor firmware); the monitor silently ignores lines
that do not start with `>`.

### 2.3 Commands and responses

Command:

```
>SEQ CMD [SUBCMD] [ARGS...]
```

- `SEQ`: decimal 1 to 65535, assigned by the daemon, wraps around, never 0.
- A command line carries at most 12 tokens including the seq (section 5.4); a firmware
  rejects a longer one with `ERR 2 badarg` rather than truncating it.
- Response, exactly one per command, echoing the seq:

```
<SEQ OK [data tokens...]
<SEQ ERR CODE NAME [detail...]
```

A line whose seq parses but which carries no command name is answered `ERR 1 badcmd`.
Silence is reserved for the one case where there is no seq to echo.

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

An emitter uses only these codes. A receiver accepts any decimal code and reports it with
the name the line carried, so an unrecognized code from a project-specific handler still
resolves its command rather than failing the parse.

A response that would exceed the 255-byte line limit is answered `ERR 8 overflow` rather
than sent truncated, since a cut hex payload cannot be distinguished from a short one.
`i2c scan` is the one exception: it truncates its address list on a whole token, because
the fault that overflows it is the fault the command diagnoses. Event lines are truncated
to the limit.

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

  A **sender** emits that DLC as one ASCII decimal digit; a **receiver** may be more
  tolerant (leading zeros, or a multi-digit token it range-checks), and is conformant
  either way. Stated because the reference firmware's own integer parser has no length
  bound, so it accepts `08` where the host rejects it: two conformant implementations
  disagreeing about a token neither will ever be sent in practice is not worth a firmware
  change and a downstream re-vendor. The tolerance is asymmetric on purpose - being strict
  about what you send and lenient about what you accept is what keeps the two sides
  interoperable. It does **not** extend to the character set: only ASCII `0`-`9`, never
  another script's digits (see the decimal-token rule in 2.1).

`can filter <id> <mask> [flags]` / `can filter all` / `can filter none`
: Controls which received frames are streamed up as events. `all` is the default at
  boot. Matching is `(rx_id & mask) == (id & mask)`. Only one software filter slot is
  required in v1 (plus all/none); hardware filter usage is up to the port layer.
  `flags` accepts `x` (extended), which is passed to the port layer; whether a receiver
  also takes the full `can tx` flags token (a run of `x`/`r`, so a redundant `xx`) is
  unspecified, and the two reference implementations differ. `r` is **rejected
  with `ERR 2 badarg`**: matching is defined over id/mask only, so there is nowhere for
  an RTR flag to take effect, and answering `OK` to a filter that cannot be honoured is
  worse than refusing it. `<id>` and `<mask>` are hex with no range stated in v1: the
  reference monitor refuses anything wider than 32 bits with `ERR 2 badarg`, the simulator
  takes whatever its hex parser accepts, so a filter that can never match a receivable
  frame is not diagnosed. Response: `OK`.

`can stat`
: Response: `OK rx=<n> tx=<n> err=<n> state=<active|passive|busoff>`.

I2C (master):

`i2c scan`
: 7-bit address sweep 0x08 to 0x77. Response: `OK 48 4A 68` (found addresses, hex,
  space separated; empty data section if none).

`<addr>` is a 7-bit address in hex, `00` to `7F`; anything outside that is `ERR 2 badarg`,
not a bus error.

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
- `<id>`: hex. The host decodes only an id that fits the width the flags declare (11
  bits, or 29 with `x`) and stores any other `!can` line as a generic event; the reference
  firmware does not range-check the id its driver hands it, so an out-of-range frame is
  lost from the decoded view.
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
- `<value>`: decimal integer, fixed-point, or scientific notation (optional `-`, digits,
  optional `.` and digits, optional `e`/`E` exponent with optional sign), parsed as
  float64 on the host. Emitted with plain
  `monitor_eventf("p %lu ax=%ld", tick, ax_mg)`; intended for "watch this one
  variable for an hour" use, not sustained streams. The exponent form is accepted
  because firmware that does have float printf emits it unprompted (`%g` prints
  `1.2e-05`). A literal that overflows to infinity (`1e999`) is malformed.

Typed streams (definition plus samples):

```
!pd <sid> <name>:<type>[*<scale>][:<unit>] [<name>:<type>... ...]
!ps <sid> <tick> <val>,<val>,...
```

- `<sid>`: stream id, a single ASCII digit `0` to `9`. Multiple streams may run
  concurrently with different layouts and rates (e.g. fast IMU stream, slow battery
  stream); ten is ample since each stream carries multiple channels. A firmware need not
  support all ten concurrently: the reference monitor holds 4 streams of up to 16 channels
  each and answers `ERR 2 badarg` past that.
- `<type>`: `u1 s1 u2 s2 u4 s4 f4` (unsigned/signed integer or IEEE754 float, size
  in bytes). `f4` is transmitted as its raw 32-bit pattern, so firmware needs no
  float printf.
- `<scale>` (optional): a float in the same grammar as an ad-hoc `<value>` above, so
  scientific notation is available and usually more legible for the small factors this
  slot attracts (`ax:s2*9.8e-4:g` over `ax:s2*0.00098:g`). The host multiplies the
  decoded value by it before storage/display. `<unit>` (optional): display label.
  Data lines carry no scale/unit cost.
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
  - A firmware must not emit a `!pd` body it has not validated against this grammar: the
    host keeps the previous definition and stores every subsequent `!ps` for that sid as a
    generic event, with no error visible on the target.
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
  across all streams and ad-hoc names (the host keys channels by name alone). Nothing
  enforces this: a duplicate name merges the two channels into one series and the last
  definition seen wins for render metadata, with no error on either side.

Throughput: a tick plus four s2 channels is about 33 bytes/line, sustaining roughly
350 lines/s at 115200 baud and 8x that at 921600 (preferred for streaming-heavy
work; the format saves about 30% over decimal, the baud rate is the bigger lever).
Binary (non-line) streaming is **[P2]** and so far unjustified.

Markers (timeline annotations from firmware):

```
!m [@<tick>] <text>
```

- `<text>`: free-form, to end of line, at least one non-space character. It is the
  user's text and is stored as given (only surrounding whitespace is trimmed).
- `@<tick>` (optional): MCU milliseconds tick, decimal, with a literal `@`. The sigil is
  required because `<text>` is free-form and often built at runtime, so a bare leading
  number would be ambiguous: `!m 12 cells balanced` must not silently lose its first
  word to a tick. Omitting `@<tick>` is fully supported for firmware with no timebase,
  and is exactly what a forgotten sigil degrades to, so the mistake costs the tick and
  nothing else. Text that genuinely starts with `@` followed only by digits is the one
  case the host reads as a tick.
- The daemon stores a well-formed `!m` as a **`marker` channel row** (not `event`), so a
  firmware marker lands in the same filter, the same full-width divider and the same
  exports as `mcu mark` and the session boundaries. `lines.raw` keeps the whole wire line;
  consumers strip the `!m [@<tick>] ` prefix for display. A malformed one (no text, tick
  above 2^32-1) is stored as a generic event like any other undecodable line.
- Firmware emits one with `monitor_mark("calibration start")`, which fills the tick from
  the port's `tick_ms()`, or by hand with `printf("!m calibration start\n")`.

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
  (or use FastAPI's WS support and drop the separate dep; implementer's choice),
  `regex` (mandatory, for the pattern-matching rules below).
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
  paths and the `socket://` / `rfc2217://` / `sim://` schemes; other `serial_for_url` schemes
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
- User-supplied `match` regexes (`/lines`, `/wait`, `/assert`) are compiled with the
  **`regex` module, not stdlib `re`**, and evaluated off the event-loop thread on a
  **dedicated bounded pool** (with a private read connection for `/lines`). Both halves
  are load-bearing:
    - `re` holds the GIL for the whole of a backtrack, so off-loading alone is not
      containment - a 7-character pattern such as `(a+)+$` froze the entire process,
      making this a remote denial of service wherever the daemon is LAN-exposed.
      `regex` releases the GIL while matching, so the pool separation becomes real.
    - `regex` accepts a `timeout=`, which genuinely interrupts a backtrack in progress.
      Matching is bounded twice: a per-`search()` ceiling (`MATCH_TIMEOUT_S`, small - no
      honest match on a 255-byte line comes near it) and a per-query budget
      (`MATCH_BUDGET_S`, generous - a legitimate scan of a multi-million-line capture
      must not trip it). Either alone has a hole: a per-call limit is unbounded across
      millions of rows, and a query budget alone lets one row spend all of it.
  Exceeding either budget returns **400** with the standard `{"error": ...}` envelope.
  It is deliberately not a timeout result: `mcu wait` exit 2 already means "pattern
  valid, nothing matched in the window", and `mcu assert` never exits 2 at all, so a
  killed pattern must reach the caller as an error (exit 1).
  The pool is separate from the default executor deliberately: that one also joins the
  serial reader thread on detach and shutdown, and regex work sharing it would let a
  burst of slow patterns delay a detach. `MAX_MATCH_LEN` bounds pattern length as a first
  gate only; it is not a defence, since 7 characters suffice to write a hostile pattern.

### 3.2 Responsibilities

1. Open and own one or more serial ports; auto-reconnect with backoff when a device
   disappears and reappears. Device identity across replug: on Linux prefer
   `/dev/serial/by-id/...` paths in config; on either OS a port may instead specify
   `serial_number`, which the daemon resolves to a device via pyserial `list_ports`
   at each (re)connect attempt.
   The backoff is presence-gated: while the device node is absent the daemon polls
   for it every 0.25 s (cheaply) and opens 0.15 s after it reappears, so a replug
   reconnects within a fraction of a second instead of waiting out the grown interval.
   The settle is there because a node can appear a moment before it is openable, and a
   reappearance resets the backoff to its minimum rather than continuing to double. The
   doubling backoff, 0.5 s to 5 s, applies when the device *is* present but will not open
   (busy, permissions, udev rules still landing) and for transports with no presence
   test, i.e. `socket://`, `rfc2217://` and `sim://`.
   "Present" is `os.path.exists` on POSIX and a match against the port enumeration on
   Windows (where `os.path.exists` cannot see a COM name); for a `serial_number` port it
   is whether the enumeration resolves it. Anything that cannot be tested without opening
   it counts as present, which is what puts plain backoff in charge.
   Retries are **not** narrated line by line. A disconnected episode records the loss
   once and the reason once (three distinct reasons at most, since a changed reason is
   news), and the reconnect records one row carrying the count of failed attempts behind
   it. Retries repeat for as long as the device is away, so a row per attempt buries the
   capture in identical notices in exactly the state where it most needs reading; the
   individual attempts go to the daemon log at debug level instead.
2. Split the RX byte stream into lines, classify each (`debug`, `resp`, `event`, and
   `marker` for a well-formed `!m`), timestamp on arrival, decode known events (CAN,
   plot, markers), and append everything to SQLite.
   Also log every TX line (`cmd` or raw `send`) and internal notices (`sys` channel:
   port opened/lost, daemon start/stop) and annotations (`marker` channel: session
   boundaries, `POST /marker` from a client, and `!m` lines from firmware).
3. Manage command sequence numbers and match responses: one in-flight command per
   port at a time (serialize with an asyncio lock; queue further commands). On
   timeout, mark the seq dead so a late response is logged but not delivered.
4. Serve the REST + WebSocket API below.
5. Enforce a retention policy: delete `lines` (and cascaded `can_frames` and `plot_points`)
   older than `retention_days` (default 10, so two successive weekends are always covered) on
   startup and hourly; `PRAGMA journal_mode=WAL`.
   Age expiry is floored by `storage.min_sessions` (default 5): the lines belonging to
   the newest N sessions are never expired by age, however old they get. Age alone is a
   poor measure of what is worth keeping - a board captured over a quiet fortnight would
   otherwise lose its only recorded run to the calendar - so old data survives while
   there is little of it and expires only once newer runs have accumulated. With fewer
   than N sessions recorded, all of them are protected; lines captured while no session
   was running are not protected by the floor. `min_sessions = 0` disables it.
   Optionally also enforce a size cap, `storage.max_db_bytes` (default 0, meaning no
   cap), checked once a minute: while live content exceeds it, trim the **oldest** lines
   until the capture is back under 90% of the cap, and record a `sys` row saying how many
   were lost. The size cap honours the `min_sessions` floor where it can, so a protected
   run is the last thing to go, but not absolutely: if the protected sessions alone
   exceed the cap it trims into them and logs a warning, because a cap that can be
   silently suspended is not a bound on disk use at all. The cap is measured against live content (allocated pages minus the
   freelist), not the file size, because SQLite reuses freed pages rather than shrinking:
   a file-size cap would keep reading "too big" after each trim and delete until the
   capture was empty. Age retention is the primary bound; the size cap is an opt-in
   disk-space guard.
6. Own the capture database exclusively: exactly one daemon may write one capture.
   `lines.id` is allocated by the daemon rather than by SQLite (which is what lets the
   writer insert a batch with one `executemany`), so two daemons on one file collide on
   the primary key. The listening port is not a sound guard for this - two daemons on
   different ports can share a `db_path`, and uvicorn runs the app lifespan before it
   binds, so even the same-port case has already opened the database and written rows by
   the time the bind fails. `mcuscoped` therefore takes an **OS lock** on `<db_path>.lock`
   before anything opens the capture, and holds it for the process lifetime.

   A lock, not a pid file, because the kernel drops it when the process exits however it
   exits: a crash, a `SIGKILL` or a power cut cannot leave one behind to be cleared by
   hand. The leftover `.lock` file is not a leftover lock. Two escape hatches cover what
   the kernel does not: acquisition retries for ~2 s, because the realistic stuck case is
   a restart racing its predecessor's shutdown rather than a crash; and
   `mcuscoped --ignore-capture-lock` starts anyway with a warning, for a filesystem that
   does not implement locking (some network mounts). Refusal names the holding pid, host
   and start time, and points at the override. Byte 0 of the file is the lock target and
   holds a filler character; the holder's `{pid, host, started, db}` JSON is written from
   byte 1 on, outside the locked range, because Windows fails reads *inside* a locked byte
   range and a second daemon must still be able to say who holds it.

   Separately from the lock, the daemon probes every resolved bind address before uvicorn
   takes it and refuses to start when one is occupied, naming the address. This is
   ordering, not ownership: POSIX reports the collision by itself, but only from inside
   `uvicorn.run()`, which is after the pid record below is claimed, so a daemon doomed to
   fail the bind would take the running daemon's record on the way in and delete it on the
   way out. On Windows it is detection as well, since a bind can succeed against a listener
   that set `SO_REUSEADDR`; the probe sets `SO_EXCLUSIVEADDRUSE` there.

   Readers are deliberately unaffected: `sqlite3 capture.db`, a session export, or any
   other reader is safe under WAL and is never blocked.
7. Record its own pid so `mcu daemon stop` can find it however the daemon was started:
   one file per bind address under `platformdirs.user_data_dir("mcuscope")`, named
   `mcuscoped-<host>-<port>.pid` and holding the pid as ASCII decimal. The record is
   written by `mcuscoped` itself, not only by `mcu daemon start`, so a bare `mcuscoped`
   is stoppable (on a windowless Windows interpreter that is the only stop path there
   is). It never overwrites a record naming a live process, and is removed on exit,
   including on `SIGTERM`, only while it still names this process. It is a locator, not a
   lock: the single-writer guarantee is the capture lock above, and a daemon that loses
   the claim race runs unrecorded rather than stealing the record. `docs/ARCHITECTURE.md`
   holds the race rules.

### 3.3 Configuration

Config lives at `platformdirs.user_config_dir("mcuscope")/config.toml`
(`~/.config/mcuscope/config.toml` on Linux,
`%LOCALAPPDATA%\mcuscope\mcuscope\config.toml` on Windows); the default db path uses
`platformdirs.user_data_dir("mcuscope")`. `mcuscoped --config PATH` (or env
`MCUSCOPED_CONFIG`) selects a different file, which is how multiple setups are kept; the
flag wins over the variable. `--host ADDR` and `--port N` override `server.host` and
`server.port` for one run without touching the file, which is what a systemd unit or a
second bench daemon uses. `--open` opens the web UI in the default browser once the
server is up, `--sim` runs the bundled simulator in-process (section 8), and `--version`
prints the version and the interpreter. On the client side, `mcu daemon start` waits up
to 20 s for the daemon to answer; `MCUSCOPE_START_TIMEOUT` (seconds, floored at 0.5)
overrides that for a cold or network filesystem.

All keys optional; a missing file is valid (defaults, no ports), so a first run needs
no setup beyond starting the daemon and opening the UI:

```toml
[server]
host = "127.0.0.1"      # bind 0.0.0.0 for LAN access (set a token!)
port = 8765

[storage]
db_path = ""            # default: <user_data_dir>/mcuscope/capture.db
retention_days = 10     # two successive weekends
max_db_bytes = 0        # 0 = no size cap; when set, the oldest lines are trimmed
min_sessions = 5        # newest N sessions never expire by age (0 = age only)
auto_session = true     # open a session per daemon run, so the floor above has runs
                        # to protect even when nobody names one by hand

[[ports]]
alias = "board"                          # name used by clients
device = "/dev/serial/by-id/usb-STM..."  # or COM7, /dev/ttyACM0, socket://127.0.0.1:9000
# serial_number = "066BFF3..."           # alternative to device: resolve via USB
                                         # serial number at each (re)connect,
                                         # stable on both Linux and Windows
baud = 115200
autoconnect = true

[update]
check = true            # ask PyPI once a day whether a newer release exists (3.6);
                        # MCUSCOPE_UPDATE_CHECK disables it without a config file
```

The access token is **not** a config key (see 3.1); a `server.token` key found in the
file is ignored with a warning pointing at `MCUSCOPED_TOKEN`. Unknown keys and unknown
sections are ignored without complaint.

Value rules the loader enforces:

- Bounds: `server.port` 1..65535, `storage.retention_days` >= 1,
  `storage.max_db_bytes` 0 (no cap) or >= 1048576, `storage.min_sessions` >= 0,
  `ports[].baud` >= 1, `ports[].alias` matching `[A-Za-z0-9][A-Za-z0-9_.-]{0,31}`.
- TOML types are not coerced. A value of the wrong type in `[server]`, `[storage]` or
  `[update]` fails the load and the daemon refuses to start, naming the file and the key.
  The same mistake inside a `[[ports]]` entry warns and keeps that key's default, so one
  bad entry does not cost the whole file. A non-string `alias` skips the entry instead:
  coercing it would attach a port under a key no string lookup reaches.
- A value of the right type but out of range warns, falls back to the **default**, and
  the daemon starts. Falling back rather than clamping, because both out-of-range keys
  that matter govern deletion: a `retention_days` clamped to 1 would delete nine days of
  capture the value was written to keep, and a sub-1 MiB `max_db_bytes` clamped to the
  floor still trims to 90% of it. The default is the only value that deletes nothing the
  operator did not ask to delete. The 1 MiB cap floor is the loader's, not just the
  write-back API's: a hand-edited file is exactly the path that never sees the API's
  validation, and the same constant governs both.
- A `[[ports]]` entry with no alias, an invalid alias, or neither `device` nor
  `serial_number` is skipped with a warning. Types are settled before that last check, so
  a wrong-typed `device` is nulled first and then skips the entry, rather than passing the
  check as truthy and becoming a port that retries on nothing for the daemon's lifetime.
- The file is read as UTF-8 and a leading byte-order mark is tolerated (PowerShell's
  `Out-File -Encoding utf8` writes one). A `db_path` beginning with `~` is expanded
  against the user's home directory.

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
- The endpoints validate at least as strictly as the loader (alias grammar, device or
  serial_number required, bounds on port, baud, retention, cap and session floor), so the
  UI can never write an entry the loader would skip. Several bounds are deliberately
  tighter than the loader's: `host` 1..255 characters, `db_path` at most 1024,
  `retention_days` 1..3650, `min_sessions` 0..1000, `baud` 1..100000000, `max_db_bytes` 0
  or 1048576..4398046511104, at most 64 ports. An interactive UI should refuse nonsense;
  a hand-edited file is held to the looser loader bounds above so an out-of-range value
  degrades to a warning rather than to a daemon that will not start.
- Saved config vs running state: edits take effect live where possible
  (`retention_days`, `max_db_bytes`, `min_sessions`, `auto_session`, and the ports list
  on next attach), but `server.host`,
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
  "storage": {"db_path":..., "retention_days":..., "max_db_bytes":...,
  "min_sessions":..., "auto_session":...}, "update": {"check": bool}, "ports": [{...}],
  "token_set": bool, "restart_required": bool}`. Never includes a token value.

`PUT /config/server {host, port}` /
`PUT /config/storage {db_path, retention_days, max_db_bytes, min_sessions, auto_session}` /
`PUT /config/update {check}`
: Update one section. Returns `{"ok": true, "restart_required": bool}`. A non-zero
  `max_db_bytes` below 1 MiB is refused, so a mistyped cap cannot trim a capture to
  nothing the moment it is saved; the loader holds a hand-edited file to the same floor,
  warning and keeping the default. Turning `auto_session` on mid-run opens a session
  immediately; turning it off leaves the running one to close normally, since ending it
  early would fragment the run for no benefit. `update.check` applies live in both
  directions (`restart_required` is always false): switching it off stops the next
  request being made, switching it on resumes on the cached schedule.

`PUT /config/ports {ports: [{alias, device?, serial_number?, baud?, autoconnect?}]}`
: Replace the saved ports list. Returns `{"ok": true, "restart_required": false}`
  (ports apply live; the daemon does not auto-attach on save).

### 3.4 REST API

All request/response bodies are JSON. Errors carry an appropriate HTTP status plus
`{"error": "message"}`, and no other shape: **400** for a request the handler rejects,
**422** for a field outside its declared type or bound (the message names the field and the
value), **401** / **429** for the token guard, **403** for the Host and same-origin guards
and the loopback-only endpoints, **503** when the capture's subscriber cap is reached,
**500** for an unhandled fault. 400 and 422 are distinct classes and a client may branch on
them: a 422 is never a request the daemon could serve later. Times in queries are either
absolute unix seconds (float) or relative via `last_ms`.

Every request is checked against a **Host allowlist** before it reaches a route: the `Host`
header must name an IP literal, `localhost` (or `localhost.localdomain` / `ip6-localhost`),
or the address the daemon was configured to bind. Anything else is 403. This is the
DNS-rebinding defence, and it runs whether or not an `Origin` is present, because a rebound
page's same-origin requests send none, so the `Origin`-vs-`Host` comparison of 3.1 cannot
provide one. Both refusals carry the same message, `cross-origin request refused`. A daemon
reached by a hostname it was not configured to bind therefore refuses every request: behind
a proxy, or under a `.local` name, set `server.host` to that name.

`timeout_ms`, and `/assert`'s `min_window_ms`, are capped at **300000** (5 minutes)
wherever they appear; larger is a 422. A long soak is watched with repeated calls rather
than one held request, so a stalled client cannot hold a port's command lock or a
subscriber slot indefinitely.

`GET /status`
: `{"version": ..., "pid": n, "uptime_s": ..., "db_path": ..., "db_size_bytes": ...,
   "db_content_bytes": n, "db_max_bytes": n, "lines_trimmed": n, "write_errors": n,
   "ws_dropped": n, "session": {...} | null,
   "update": {"latest": "0.2.0", "available": true, "checked_at": ts, "url": "..."} | null,
   "ports": [{"alias": "board", "device": ..., "baud": ..., "connected": true,
   "lines_rx": n, "lines_tx": n, "rx_dropped": n}]}`
  `update` is the release check (3.6), null until a check has succeeded (disabled,
  offline, or too soon after start).
  `session` is the running session (including the daemon's automatic one, distinguished
  by its `auto` flag) or null when none is open.
  `db_size_bytes` is disk usage (database plus its `-wal`). `db_content_bytes` is live
  content, allocated pages minus the freelist, and it is the figure `db_max_bytes` is
  enforced against: SQLite keeps freed pages for reuse rather than returning them all at
  once, so the two differ after a trim and comparing the wrong one makes a working cap read
  as broken. `db_max_bytes` is the size cap in force (0 = none) and `lines_trimmed` counts
  the oldest lines it has removed. `rx_dropped` is the running count of received lines a port could not capture:
  shed under back pressure (SPEC 3.2 drop-oldest), over the line cap, or refused by the store.
  `write_errors` counts that last case from the store's side across every port, so one
  failure moves both counters and they must not be summed. Either non-zero means the
  capture has holes. `ws_dropped` is the client side of the same question: rows a slow
  WebSocket subscriber missed (`/ws`). The capture still holds them, so unlike `rx_dropped`
  and `write_errors` it is recoverable by re-fetching.
  `pid` is the serving process itself, which is what a fallback kill must target: the
  recorded pid can be a launcher shim's instead (Windows venv launchers spawn the
  interpreter as a child).

`POST /shutdown`
: Ask the daemon to shut down gracefully (lifespan runs: ports stop, the automatic
  session closes, the store is flushed). Returns `{"ok": true}` and exits shortly
  after. Loopback clients only (403 otherwise, token or not): stopping the daemon is a
  local operator action. This is `mcu daemon stop`'s primary channel; it exists because
  Windows has no graceful signal that crosses console boundaries, so a REST call is the
  only clean stop that reaches a detached daemon there. Servers embedding the app
  without wiring a shutdown callback (tests) answer 400 instead.

`GET /ports` / `POST /ports {alias, device?, serial_number?, baud=115200}` /
`DELETE /ports/{alias}`
: List, attach, detach. One of `device` or `serial_number` is required (400 otherwise);
  `serial_number` is resolved to a device through pyserial `list_ports` (3.2). `alias` must
  match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$`, and `baud` is 1..100000000 - the same ceiling
  `PUT /config/ports` enforces (3.3.1), so an entry the config loader would reject cannot be
  attached live either. Either bound violated is a 422. Attaching with an existing alias
  replaces that attachment (this is how a baud change is done). Attach returns
  `{"port": {...}}`; detach returns `{"ok": true}`, or 400 for an unknown alias.

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

`GET /config` / `PUT /config/server` / `PUT /config/storage` / `PUT /config/update` /
`PUT /config/ports`
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
   "line_id": 12345}                            // lines.id of the response row, null on timeout
  ```

`GET /lines?port=&chan=&match=&since_id=&since_ts=&last_ms=&id_to=&limit=100&order=desc`
: Query the capture. `match` is a Python regex applied to `raw`. `chan` may repeat.
  Returns `{"lines": [{"id":, "ts":, "port":, "dir":, "chan":, "seq":, "raw":}, ...],
  "truncated": bool}`. `match` is bounded by `MAX_MATCH_LEN` (200 characters; longer is a
  400). `limit` is clamped to **0..1000**, a value outside that range brought into it rather
  than refused. `limit=0` returns no rows: that is how a follower asks for "no backfill,
  stream from here". `truncated` still reports whether rows exist beyond those returned, so
  it is true for a non-empty window at `limit=0`.

`GET /can/frames?port=&id=&last_ms=&since_id=&id_to=&limit=100`
: Decoded CAN view. Returns `{"frames": [{"line_id":, "ts":, "tick_ms":, "can_id":,
  "ext":, "rtr":, "dlc":, "data_hex":}, ...], "truncated": bool}` - the `truncated` and
  `limit` contract of `/lines`, but under its own key, because the rows are frames and not
  lines. `id` accepts hex like `0x1A3` or `1A3`.

`POST /wait {port, match, timeout_ms=2000, send=null, chan=null, since="now"}`
: The key AI primitive. Optionally send `send` first: if `send` looks like a monitor
  command (client sets `send_mode`: `"cmd"` or `"raw"`, default `"cmd"`), route it
  through the seq machinery. Then block until a line matching regex `match`
  (optionally restricted to channel `chan`) arrives with `lines.id` greater than the
  position captured at call start, or timeout. Returns
  `{"status": "match" | "timeout", "line": {...} | null, "waited_ms": ...,
    "cmd_result": {...} | null}`. `since` has exactly one defined value, `"now"`; any other
  value is a 400 rather than a silently different window. It is a field so a future
  retrospective mode has somewhere to land.

  Both `/wait` and `/assert` also report `dropped`: rows the live feed shed for that
  request while a match was running. The scan happens off the loop, so the capture keeps
  broadcasting during it, and a burst past the subscriber queue drops the oldest, which can
  be the very line being waited for. A non-zero `dropped` means the window has holes: a
  `timeout`, or a `forbid` that did not match, has not been judged over what it claims to
  cover, and the caller should retry rather than treat it as a negative result.

`POST /assert {port, expect=[], forbid=[], timeout_ms=0, min_window_ms=0, send=null,
send_mode="cmd", chan=null, session=null, last_ms=null}`
: One pass/fail verdict over a capture window. Where `/wait` answers "did this line
  appear?", this answers "did this run pass?": several conditions at once, negative ones
  (`forbid`) included, reduced to a single result a caller can branch on without reading
  the log. At least one pattern is required; each is bounded by `MAX_MATCH_LEN` (200
  characters; longer is a 400), and `expect` and `forbid` together by 16 patterns per call.
  The bound is on the total, and violating it is a 400 that says so (over 16 in one list
  alone trips the field bound first and is a 422). It is a total because each pattern costs
  one query retrospectively, or one search per line live, and that cost is per call rather
  than per direction.

  Two modes. With `timeout_ms = 0` the assertion is **retrospective**: already-stored lines
  are judged, scoped by `session` (an unknown name is an error here, not an empty scope
  that would vacuously satisfy every `forbid`), `last_ms`, `port` and `chan`. Each pattern
  becomes one bounded `raw REGEXP ?` query that stops at its first hit, so cost scales with
  the pattern count, not the window size. With `timeout_ms > 0` it is **live**: the window
  opens at call time, optionally sends `send` first, and judges rows as they are stored.

  The mode selectors are exclusive, and a field belonging to the other mode is a `400`
  rather than being ignored: `session` and `last_ms` require `timeout_ms = 0`, `send` and
  `min_window_ms` require `timeout_ms > 0`. Accepting one silently would answer over a
  window the caller did not select while the verdict still reads authoritative - and a
  verdict is the one response nobody re-reads the log to check.

  A live window closes as soon as every `expect` has matched - absence cannot be proven
  early, so `forbid` is judged over whatever window actually elapsed, and an assertion with
  no `expect` runs the full timeout. `min_window_ms` decouples the two by holding the
  window open for a stated span after the expectations are met ("boot within 20 s, and stay
  clean for at least 10"); without it the `forbid` verdict silently covers only the time
  the expectations happened to take. It requires `timeout_ms` and may not exceed it. A
  matching `forbid` ends the window immediately: the verdict is already decided.

  Returns `{"status": "pass" | "fail",
  "expect": [{"pattern":, "matched": bool, "line": {...} | null}, ...],
  "forbid": [...same shape...], "checked_lines":, "elapsed_ms":}`.

`POST /marker {port=null, text}`
: Insert an annotation row (chan `marker`, `dir` `-`). `text` is 1..4096 characters, 422
  outside that: it is bounded like a session note and not by the 255-byte device write cap
  (3.1), since nothing is sent to the device. Returns `{"line_id": ...}`.

`POST /purge {session|before_ts|id_from/id_to|all, dry_run=false}`
: Delete captured lines deliberately, rather than waiting for retention. Exactly one
  selector is required. Retention only ever truncates the oldest end of the capture; a
  purge removes exactly the span asked for, hole in the middle included. `dry_run` reports
  the count without deleting: a purge is not recoverable, so the number has to be available
  before the delete and not only after it. Returns
  `{"deleted":, "id_from":, "id_to":, "dry_run":}`. Deleting is chunked and commits per
  chunk, and freed pages are returned to the filesystem where the database was created with
  incremental auto-vacuum.

`GET /sessions/{id|name}/export`
: Download one session as a **standalone capture database**: same schema, ids preserved,
  the session row carried across. The archive of a run is therefore queryable with exactly
  the same tools as the live capture instead of being a dead format. Built on a worker
  thread into a temp file with the live capture ATTACHed and read via `INSERT ... SELECT`,
  streamed, then removed. `{id|name}` resolves as elsewhere (a name takes the newest match);
  an unknown reference, or a build that fails, is a 400. The response is an
  `application/vnd.sqlite3` attachment named after the session, sanitized to a filename
  valid on every supported OS (Windows reserved device names included).

`GET /sessions?limit=` / `POST /sessions {name, note}` / `POST /sessions/stop` /
`DELETE /sessions/{id}?data=false`
: Sessions name a span of the capture so one run can be queried and exported on its own.
  A session is stored as an id range over the single capture timeline, not as a column on
  every line: nothing is written per row, existing captures need no migration, and
  scoping rides the primary key. The cost is that sessions cannot overlap or nest -
  starting one closes the running one. Starting and stopping each write a `marker` row,
  so a run's boundaries are visible in the terminal. `GET` returns recent sessions with
  the number of lines still stored for each (retention can remove a finished run's lines,
  and it then reads as 0 rather than claiming rows that are gone) plus the running one.
  `DELETE` forgets the label only; `?data=true` also deletes the lines it covers and
  reports `lines_deleted` (the field is always present, 0 when only the label went). The two
  are separable on purpose: forgetting a mislabelled run must not destroy what was recorded,
  and destroying a recording deserves saying so.

  A session object is `{"id": n, "name": ..., "note": ..., "started_ts": ...,
  "ended_ts": ... | null, "start_id": n, "end_id": n | null, "auto": bool}`. List rows carry
  an extra `"lines": n`, the rows still stored in its span; the running session as reported
  by `active` below and by `/status` does not. `GET` returns
  `{"sessions": [...], "active": {...} | null}`, `limit` defaulting to 50 and clamped to
  0..1000; `POST /sessions` and `POST /sessions/stop` return `{"session": {...}}`; `DELETE`
  returns `{"ok": true, "lines_deleted": n}`. `DELETE` addresses a session by id alone and
  never by name, unlike `/export`, so a lookup for a missing id cannot land on a session
  merely named that number and delete its lines.

  **Automatic sessions.** With `storage.auto_session` (default on) the daemon opens a
  session named `auto-<local timestamp>` for its own run and closes it at shutdown, so
  "the newest N sessions" means "the newest N daemon runs" without anyone remembering to
  name one. This is what makes `min_sessions` mean anything: the normal way to use
  MCUscope - daemon up, an agent issuing commands - names no sessions at all, so the floor
  would otherwise protect nothing. Sessions carry `auto: true|false`, and:

  - Starting a named session closes the automatic one; stopping the named one opens a
    fresh automatic session, so the capture is always covered by exactly one.
  - `POST /sessions/stop` reports "no session is running" when only an automatic session
    is open. It is not the caller's to stop - it belongs to the daemon run - and this
    keeps `session start` / `session stop` a matched pair.
  - An automatic session that recorded no device traffic (only `sys` rows and markers the
    host itself wrote, which carry `dir` `-`; a firmware `!m` arrives on `dir` `rx` and
    does count) is dropped when it closes. A daemon started with no board attached is not a run, and a
    list full of those would bury the ones that are. Its lines stay; only the label goes.

  `/lines`, `/can/frames`, `/plot/series` and `/plot/export` accept `session=<id|name>`
  (a name resolves to the newest match). An unknown reference matches nothing rather than
  widening to the whole capture, so a typo cannot hand back every line ever stored.

`/lines`, `/can/frames`, `/plot/series` and `/plot/export` accept `id_to=<line id>`, an
**inclusive** upper bound: only rows at or below that line id are returned. It exists so a
client can fetch or export exactly what a paused surface shows, by recording the highest
line id it had ingested at the moment of pause and passing it back. Note the deliberate
asymmetry with `since_id`, which is an exclusive cursor ("everything after what I have"),
where `id_to` is a freeze ("everything up to and including what I show"). Every surface
that can be paused records that bound and sends it: an export from a paused surface that
runs to the live edge is a defect, not a variation.

With `session=`, the effective upper bound is the smaller of `id_to` and the session's
`end_id`. With `last_ms`, the window ends at the bound rather than at the request: when an
effective upper bound is in force (from `id_to`, or from a session that has ended),
`last_ms` counts back from the timestamp of the newest line at or below it; with no upper
bound it counts back from now, as before. Intersecting a frozen id range with a
now-anchored window otherwise returns almost nothing, and this also settles what
`last_ms` combined with an *ended* session means, which previously returned an empty
window rather than that session's tail.

`/plot/export` refuses a selection over **1000000** rows with a 400 naming the count and the
limit, rather than truncating it: narrow the window with `session=`, `last_ms=` or `id_to=`.
The export is streamed, so its headers are already sent by the time the cap would bite and
truncation cannot be signalled in band - and a short CSV is byte-indistinguishable from a
complete one, which is the failure a run's archive can least afford.

`GET /ws?port=`
: WebSocket; streams every new line row as it is stored (optionally filtered by port).
  Each message is a **JSON array** of one or more row objects: the daemon coalesces rows
  that are already queued for a subscriber into a single frame, so a burst costs one
  encode and one write instead of one per line. Clients must iterate the array. Used by
  `mcu tail -f` and the web UI.

  The handshake can be refused before any frame: **close 1008** for a Host, same-origin or
  token failure (3.1), **close 1013** when the capture's subscriber cap is reached. 1013 is
  a capacity refusal and not an auth one, so a client retries rather than re-prompting for a
  token. `/wait` and a live `/assert` answer that same cap with **503**.

  After 20 s with no rows the daemon sends an **empty array** as a keepalive. A client
  that vanished without closing its TCP connection is only detected when a write to it
  fails, and on a quiet capture there may be no write for hours; the idle frame bounds
  that detection by the network's own timeouts instead of by whether the target happens to
  be talking. Clients need no special handling: iterating an empty array does nothing.

  A frame may also begin with a **gap object**, `{"gap": n}`, meaning n rows were shed for
  this subscriber before the rows that follow. The daemon drops the oldest queued row
  rather than blocking the capture when a subscriber stops reading, so a slow client loses
  data; without this it lost it silently, and a client cannot infer the gap from a jump in
  `id` because `port=` filtering makes such jumps legitimate. A client that does not
  recognise the object skips it (it has no `id`); one that does should re-fetch from its
  last seen id. `ws_dropped` on `/status` is the lifetime total across all subscribers.

### 3.5 Storage schema

```sql
CREATE TABLE lines(
  id     INTEGER PRIMARY KEY,
  ts     REAL    NOT NULL,             -- unix epoch, host receive/send time
  port   TEXT    NOT NULL,             -- alias; '' for daemon-level sys/marker rows
                                       -- (a firmware !m marker carries its port)
  dir    TEXT    NOT NULL CHECK(dir IN ('rx','tx','-')),
  chan   TEXT    NOT NULL CHECK(chan IN ('debug','cmd','resp','event','marker','sys')),
  seq    INTEGER,                      -- for cmd/resp rows
  raw    TEXT    NOT NULL              -- full line, terminator stripped
);
CREATE INDEX idx_lines_ts ON lines(ts);
CREATE INDEX idx_lines_chan_id ON lines(chan, id);   -- id, not ts: /lines orders by id
CREATE INDEX idx_lines_port_id ON lines(port, id);   -- /lines?port=, and the per-port counts

CREATE TABLE sessions(
  -- AUTOINCREMENT, not a bare rowid: a plain rowid is reused after the highest row is
  -- deleted, so a session id held by a client (an export URL, `mcu purge --session 4`)
  -- could come to name a different run. The same guarantee lines.id already carries.
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT    NOT NULL,
  note       TEXT    NOT NULL DEFAULT '',
  started_ts REAL    NOT NULL,
  ended_ts   REAL,                       -- NULL while the session is running
  start_id   INTEGER NOT NULL,           -- first lines.id in the session (inclusive)
  end_id     INTEGER,                    -- last lines.id (inclusive); NULL while running
  auto       INTEGER NOT NULL DEFAULT 0  -- opened by the daemon for its own run
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

A fourth table, `plot_points`, belongs to the same schema and is created with it; it is
defined with the plot ingest it serves, in 9.2.

**Connection PRAGMAs**, set in this order on open: `auto_vacuum=INCREMENTAL`,
`journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`. The order is load-bearing:
`journal_mode=WAL` materialises the database header, after which `auto_vacuum` can no
longer be changed. A capture created before this daemon set it therefore keeps its freed
pages forever and plateaus at its high-water mark instead of shrinking after a trim.
`foreign_keys` matters as much: SQLite defaults it **off**, per connection, and both
`ON DELETE CASCADE` clauses above depend on it. Deleting a `lines` row must remove its
`can_frames` and `plot_points` children, which is how retention (3.2) and purge (3.4)
reclaim anything. With the pragma off SQLite raises nothing, the children are orphaned,
and the size cap can never converge while the rows all still read as deleted.

The connection also registers a deterministic `regexp(pattern, text)` SQL function, backed
by the `regex` engine with a per-call timeout; `match=` on the list endpoints compiles to
it.

**Migration of an existing capture** happens in place on open and is idempotent: missing
tables and indexes are created, `sessions.auto` is added if absent, and `sessions` is
rebuilt once to acquire `AUTOINCREMENT`, which `ALTER TABLE` cannot add. The rebuild
preserves every id and seeds `sqlite_sequence` to the high-water mark, so an id is not
reissued across it. A superseded index is dropped only after its replacement exists, so an
interrupted upgrade never leaves the table with neither.

### 3.6 Release check

The daemon may check whether a newer MCUscope has been published, so a bench running a
version from six months ago is not the last to know. It is a background task with no
bearing on anything else the daemon does:

- **Source and cadence**: `GET https://pypi.org/pypi/mcuscope/json`, at most once per
  24 h. The result (`{latest, checked_at}`) is cached under
  `platformdirs.user_cache_dir("mcuscope")/update.json`, so the interval survives
  restarts: a daemon started twenty times in an afternoon still makes one request. A
  failed request is logged at debug level, changes nothing, and is retried in an hour.
  The first check of a run is deferred 10 s, so a short run makes no request at all; the
  request times out after 5 s and a response body over 4 MiB is abandoned. The cache is
  advisory: missing, corrupt, non-finite or future timestamps only mean the next check
  happens sooner.
- **Opt-out**: config `[update] check = false`, or `MCUSCOPE_UPDATE_CHECK` in the
  environment, which wins over the config file in both directions and needs no config
  file to exist (this is what CI and the test suite use). Unset, empty, or `1` / `true` /
  `yes` / `on` allow the check; every other value vetoes it, including one that is not
  recognised, because for the one switch whose purpose is not phoning home from a private
  bench, resolving a typo to "make the request" is the wrong way to be wrong. Disabled
  means no request is made at all, and the veto is re-applied when `update.check` is
  switched on at runtime.
- **Reporting**: only `GET /status`'s `update` field, and the UI badge it drives (9.1).
  A disabled check reports `null` even with a cached result from an earlier run: off means
  "stop telling me about releases", not "keep showing yesterday's answer".
  Nothing is written to the capture: an "update available" row would be an annotation on
  a hardware debug log that has nothing to do with the hardware.
- **Only plain releases count.** A version that is not `N(.N)*` (a pre-release,
  a local build) never reports `available: true`, in either direction: a notice the
  user cannot act on with `pip install -U mcuscope` is noise. When the newest published
  version is not a plain release, `latest` is reported as `null` with
  `available: false`, and the successful round trip still resets the 24 h timer.

---

## 4. CLI: `mcu`

Thin HTTP client of the daemon. Global options: `--json` (machine output),
`--port/-p ALIAS` (defaults to the only attached port; error if ambiguous),
`--url` / env `MCUSCOPE_URL`, `--token` / env `MCUSCOPE_TOKEN` (3.3), and `--version`
(prints the client version and the interpreter; honours `--json`). Env
`MCUSCOPE_START_TIMEOUT` overrides how long `mcu daemon start` waits, defined in 3.3.

Exit codes (contract for AI use): `0` success/match, `1` error (bus ERR, HTTP error,
bad usage), `2` timeout, `3` daemon unreachable.

`mcu assert` reads `1` as **assertion failed** rather than "could not answer": a window
that closes with an expectation unmet is a verdict, not an inability to reach one, so it
never exits `2`. Every other command keeps `2` for timeouts.

`mcu daemon status` reports an absent daemon as exit `3` with "not running" rather than as
an error, so the check and the contract agree. Interrupting a `-f` follow with Ctrl-C is
exit `0`, since the stream was unbounded by request; Ctrl-C anywhere else is `1`.

| Command | Behavior |
|---|---|
| `mcu status` | Daemon + port health |
| `mcu ports` / `mcu attach DEV [--baud N] [--alias A]` / `mcu detach A` | Port management |
| `mcu cmd "i2c rd 48 2" [--timeout MS]` | Send monitor command, print response data (or ERR to stderr) |
| `mcu send "raw text"` | Raw line, no response wait |
| `mcu tail [-n N] [-f] [--chan C] [--match RE]` | Recent lines / follow via WS; human format `HH:MM:SS.mmm chan| raw` |
| `mcu lines [--last-ms MS] [--chan C] [--match RE] [--limit N] [--since-id N] [--session S]` | Query capture (the AI workhorse); every filter is optional |
| `mcu wait --match RE [--timeout MS] [--send CMD] [--raw] [--chan C]` | The wait primitive; prints matching line. `--raw` sends `--send` verbatim instead of as a command |
| `mcu assert [--expect RE]... [--forbid RE]... [--session S \| --last-ms MS \| --timeout MS [--min-window MS]] [--send CMD] [--raw] [--chan C]` | The verdict primitive; exit `0` pass, `1` fail |
| `mcu session start NAME [--note T]` / `stop` / `list [--limit N]` | Name a span of the capture |
| `mcu session export NAME -o FILE.db` / `mcu session delete NAME [--data] [-y]` | Archive a run as a standalone capture; delete a label (and with `--data` its lines) |
| `mcu purge (--session S \| --before-days N \| --id-from A --id-to B \| --all) [--dry-run] [-y]` | Delete captured lines deliberately; always previews the count, prompts unless `-y` |
| `mcu can tx ID [DATA] [--ext] [--rtr N]` | Sugar for `cmd "can tx ..."` |
| `mcu can dump [--id ID] [--last-ms MS] [-n N] [-f]` | Decoded CAN frames from capture; `-n 0` with `-f` means no backfill, follow only |
| `mcu can stat` / `mcu can filter ...` | Pass-through sugar |
| `mcu devices` | List serial devices the host can see, with VID/PID/serial |
| `mcu i2c scan` / `mcu i2c rd ADDR N [--reg HEX]` / `mcu i2c wr ADDR DATA` | Sugar; `--reg` uses `wrrd` |
| `mcu spi xfer CS DATA` | Sugar |
| `mcu gpio set NAME 0|1` / `mcu gpio get NAME` / `mcu adc read NAME` | Sugar |
| `mcu mark "text"` | Insert marker |
| `mcu log export [--last-ms MS] [--chan C] [--match RE] [--limit N] [--session S] [-o FILE]` | Dump matching lines as JSONL or text |
| `mcu daemon start [--config FILE] [--sim] [--timeout S] [--token T]` / `stop` / `status` | Convenience: spawn/kill mcuscoped as a detached process, cross-platform (start_new_session on POSIX, DETACHED_PROCESS on Windows); a systemd user unit is also provided as a Linux convenience |
| `mcu ai-guide` | Print a compact usage guide written for an AI agent (see 6) |

With `--json`, every command prints exactly one JSON object (the API response,
lightly wrapped), no prose.

The exception is a command that emits rows one at a time: it prints JSONL, one object per
line, because a `-f` form is an unbounded live stream that no single object could hold, and
a bulk dump would have to be buffered whole to wrap it. Today that is `mcu log export`,
`mcu tail` and `mcu can dump`. All stay parseable line by line, which is why notes and
warnings go to stderr in every case, `--json` or not.

On these commands a fatal error appends its `{"error", "exit_code"}` object as the final
JSONL line rather than replacing the stream. A follower that has already consumed rows needs
an in-band statement of why the stream stopped, and stderr is not in the stream.

The list is enumerated here *and* pinned by a test, because an enumeration is what failed
before: the sentence was corrected for `mcu tail` while `mcu can dump`, which had the
identical shape, went unlisted for another round. A new per-row emitter fails that test
until it is added here deliberately.

Phase 7 adds `mcu plot channels` and `mcu plot export` (see section 9.2).

---

## 5. Firmware monitor module

### 5.1 Files and portability rules

```
firmware/monitor/monitor.h          public API + shim declarations (the contract), the
                                    MON_WEAK portability macro, and a clearly marked
                                    internal section shared by the two .c files (5.1
                                    forbids a private header)
firmware/monitor/monitor.c          core: line assembly, parse, dispatch, response/event formatting
firmware/monitor/monitor_cmds.c     built-in v1 command handlers (can/i2c/spi/gpio/adc/ping/info)
firmware/monitor/port_template/monitor_port_template.c   every shim stubbed with TODOs
firmware/monitor/INTEGRATION.md     step-by-step integration into an existing STM32 LL project
firmware/monitor/README.md          what the module is and how the files fit together
```

Core rules: C99, no dynamic allocation, no HAL/LL/CMSIS includes anywhere in
`monitor.c`/`monitor_cmds.c`, no floating point, static buffers only, main-loop
context only. Target footprint: roughly 4 KB flash (estimated; not measured on a Cortex-M
toolchain). RAM is 1268 bytes of `.bss`, measured with gcc `-O2 -std=c99` on x86-64, and
roughly 1.0 to 1.1 KB on Cortex-M where the pointer-bearing objects shrink: three
~256-byte buffers (RX line, response payload, outgoing line), a 64-byte RX staging
buffer, the 4-stream plot registry and the 8-slot app-command registry. The port layer's
CAN RX queue is on top of that.

### 5.2 Public API (contract; implement exactly this)

```c
// monitor.h
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdarg.h>

#define MONITOR_LINE_MAX 255
#define MONITOR_PROTO_VERSION 1
// Largest OK payload a handler can return and still be sendable: "<SEQ OK " is up to
// 10 bytes at seq 65535, plus the trailing LF.
#define MON_OK_PAYLOAD_MAX (MONITOR_LINE_MAX - 10)

// Weak-symbol portability for the default bus shims (5.3). Where the toolchain has no
// weak support MON_WEAK is empty, the defaults become strong symbols, and providing your
// own shim alongside one is a duplicate-symbol link error; use an #ifdef-selected stub
// there (INTEGRATION.md section 4).
#if defined(__GNUC__) || defined(__clang__)
#define MON_WEAK __attribute__((weak))
#elif defined(__ICCARM__) || defined(__CC_ARM)
#define MON_WEAK __weak
#else
#define MON_WEAK
#endif

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
// drains the CAN RX queue into events, and rebroadcasts due plot definitions.
// Cheap when idle.
void monitor_poll(void);

// --- extending the command set (application code) ---
// argv[0] is the command name; write the OK payload into resp (no "OK" prefix,
// no newline). Return 0 for OK, or a MONITOR_ERR_* code.
// resp_max is the buffer size, NOT the sendable payload size: the response goes out as
// "<SEQ OK <payload>\n", whose prefix is up to 10 bytes, so a handler that fills the
// whole buffer produces a line the emitter must reject with ERR 8 rather than truncate.
// Clamp any variable-length payload to MON_OK_PAYLOAD_MAX.
typedef int (*monitor_handler_t)(int argc, char **argv,
                                 char *resp, size_t resp_max);
bool monitor_register(const char *name, monitor_handler_t fn);   // static table, N=8 extra slots

// Emit an async event line "!<fmt...>" from main-loop context.
void monitor_eventf(const char *fmt, ...);

// Emit a marker (protocol 2.5): "!m @<tick> <text>", the tick taken from the port's
// tick_ms() automatically. NULL or empty text emits nothing. Main-loop context only.
void monitor_mark(const char *text);

// Lines the port layer refused to send (SPEC 5.2). Monotonic during a run; only
// monitor_init() clears it.
uint32_t monitor_tx_dropped(void);

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
// The body is validated against the whole 2.5 channel-spec grammar at registration, not
// just the widths this parser needs, and a body that fails is MONITOR_ERR_BADARG: a
// stream the host would refuse must not register on the target and then look fine there.
// One gap remains by design: an out-of-range scale exponent such as "*1e999" is
// in-grammar and only the host rejects it.
// Re-registering a sid with a different body is MONITOR_ERR_BADARG; the same body is a
// no-op. def->body is cached as a raw pointer, not copied, so it must have static
// lifetime (a string literal or equivalent), never a stack buffer.
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

The scan response is clamped to `MON_OK_PAYLOAD_MAX` and truncated on a whole address
token rather than failing: SDA stuck low makes all 112 probed addresses appear to ACK, and
a short list is a more useful answer to that fault than `ERR 8`. One command line carries
at most 128 payload bytes (`MON_MAX_DATA`) for any `i2c wr`/`rd`/`wrrd` or `spi xfer`.

### 5.4 Parser notes

- Line assembly: accumulate into a static buffer until LF; on overflow, discard until
  next LF, then respond `ERR 8 overflow` if a seq was parseable from the discarded
  prefix, else stay silent.
- Ignore lines not starting with `>`.
- Tokenize in place (replace spaces with NUL); max 12 tokens.
- Reject rather than truncate: a 13th token, a byte above 0x7F, or an embedded NUL fails
  the whole line with `ERR 2 badarg`, silently if no seq could be recovered from it.
- The seq must be decimal 1 to 65535. A line whose seq does not parse is dropped in
  silence, since there is nothing to address a reply to. A valid seq with no command
  token is `ERR 1 badcmd`.
- A bare CR is discarded anywhere in the line, so a CRLF sender needs no configuration.
- `monitor_poll` issues at most one `uart_read` per call, into a 64-byte staging buffer,
  and clamps a returned length larger than that buffer: a shim that returns "bytes
  available" rather than "bytes copied", or an int-returning driver whose -1 becomes
  SIZE_MAX, must not be able to walk the parser into adjacent SRAM.
- Dispatch: two-level lookup, first token then optional second token, over one static
  table of `{ "can", "tx", handler }`-style rows; registered app commands match on
  first token only.
- Responses are formatted into the outgoing line buffer and pushed via `uart_write`
  in one call.

---

## 6. AI integration

Two artifacts, both part of v1:

1. `mcu ai-guide`: prints a compact reference covering what the daemon is, the exit-code
   contract, `--json`, and one example per major command, with the send-and-wait and
   lines-query patterns emphasized. This lets an agent that only knows "run
   `mcu ai-guide`" self-serve the details on demand instead of bloating CLAUDE.md.
2. `docs/CLAUDE_SNIPPET.md`: a short block (a dozen-odd lines) the owner pastes into
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
it in-process: `mcuscoped --sim` autoconnects to it as port `sim` - the zero-hardware
demo path. It speaks the full monitor protocol over one of three transports:

- `sim://<name>`: the simulator core behind a link in the same process, with no socket
  and no serving thread. This is what `mcuscoped --sim` attaches, and what the host test
  suite drives. It is not served by `serial_for_url`: a daemon reaches it only when the
  simulator's link opener was supplied to it, and a `sim://` device attached to any other
  daemon fails to open.
- TCP (cross-platform, the standalone default): a listener on `127.0.0.1` (port via
  `--tcp-port`, default 9900, `0` for ephemeral with the chosen port printed); a daemon
  attaches with `device = "socket://127.0.0.1:<port>"`. This is how `mcu-sim` is reached
  from another process.
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
  counter payload) and echoes any transmitted frame back with id+1 after 20 ms, the id
  wrapping within its own range, so `can tx 7FF` echoes as id 0. Alongside the heartbeat
  runs a standing multi-id bus, on every transport, so the decoded CAN view has realistic
  traffic: 0x200 at 2 Hz (dlc 2), extended 0x18A at 1 Hz (dlc 8), 0x321 at 5 Hz (dlc 1),
  and remote frame 0x400 at 0.5 Hz (dlc 8), the data frames carrying a rolling counter.
- Emits a debug line every 2 s (`sim alive n=<count>`), and a burst of debug lines
  immediately after any `gpio set` (to exercise interleaving).
- `mark <text>`: answers `OK` and emits a firmware marker (`!m @<tick> <text>`), the
  simulator's stand-in for `monitor_mark()`, so the marker path is exercisable end to end
  with no hardware. Empty text is `ERR 2 badarg`.
- Flags to inject faults: `--drop-response N` (swallow the response to the Nth
  command), `--garbage` (occasionally emit binary junk). `--symlink PATH` gives the
  `--pty` slave a stable name. RTR and extended-id coverage needs no flag: both are on
  the standing CAN bus above.
- `--plot`: exercise both plot formats: ad-hoc `!p` lines at 20 Hz with two channels
  (`sine` and `noisy`, the second being the first plus noise), and a typed stream
  (`!pd 0 tri:s2*0.01:V ramp:u2 ftest:f4` with `!ps` samples at 20 Hz, ftest being a
  slow sine so f4 decode is visually verifiable), including the 5 s `!pd`
  rebroadcast. A `--plot-late-def` flag delays the first `!pd` by 5 s to test the
  undecodable-sample path. Two further typed streams exercise the digital/enum panel:
  `!pd 1 state:u1:=0=IDLE,1=ARMED,2=RUN` stepping every ~1 s, and
  `!pd 2 gpio:u1:/led,irq,pwm_en` as packed bits at mixed rates.
- `--flood N`: emit N extra plain debug lines per second, catching up on whatever is
  owed since the last serve pass so the requested rate is met regardless of poll
  timing. This is how the capture path and the web UI's high-rate behaviour are
  exercised without a real board that can saturate a link. The catch-up is capped at 5000
  lines per serve pass, so a long scheduling stall bounds the recovery rate rather than
  producing one enormous write.

The simulator doubles as executable documentation of the protocol and lets the owner try
the whole system with zero hardware on either OS: `mcuscoped --sim` attaches to it in
process, and a standalone `mcu-sim` is attached over its TCP socket (or pty) exactly as a
real port would be.

---

## 8. Testing strategy

About 800 tests over 24 files in `host/tests/`, roughly 4 minutes, no hardware and no
daemon subprocess by default. `docs/ARCHITECTURE.md` "What the tests attach to" is the
authority on which tier attaches to what and why; the tiers themselves are:

- `host/tests/test_protocol.py`: pure unit tests for line classification, command
  formatting, response parsing, `!can` decoding, seq lifecycle including timeout and
  late-response handling.
- `host/tests/test_e2e.py`: a fixture stands the daemon up in process (uvicorn on a
  background thread, ephemeral HTTP port, temp db) with its port attached to the
  simulator over `sim://`, then exercises the REST API and WS end to end: cmd ok/err/
  timeout paths, wait with and without send, lines queries, can dump, marker, WS tail,
  sim fault flags. No subprocess, so it runs identically on Linux and Windows.
- `host/tests/test_cli.py` is the CLI tier and the only one that spawns a subprocess: it
  runs the **installed `mcu` console script**, because `python -m mcuscope.cli` is not
  the artifact a user runs (different program name in every message, CWD on `sys.path`).
  A declared but missing console script fails `test_scaffold.py` rather than skipping.
- `host/tests/test_reconnect.py` covers the port going away and returning.
- The serial transports keep their own tests, because `sim://` exercises neither: the TCP
  listener and one whole-stack run through pyserial's `socket://` handler in
  `test_sim_tcp.py`, and the POSIX-only `--pty` path in `test_sim_pty.py` (skipped on
  Windows). A stack test that means "this device never connects" uses an unopenable
  device name, which is a real failure, not a stand-in for one.
- The rest are grouped by concern rather than by module: `test_hardening.py` and
  `test_security.py` (hostile input, bind policy), `test_regressions.py` (one test per
  confirmed defect class, see `docs/REVIEW.md`), and per-feature files for assert,
  sessions, plot, config, pidfile and the update check.
- Web UI JavaScript: `host/tests/test_webui_js.py` runs `node --test` over 16 `.mjs`
  files in `host/tests/webui_js/`, against the shipped `webui/*.js` modules under a hand
  written DOM stub. No npm packages, no browser driver; skips cleanly without node 18+.
  Logic reachable only through a laid-out canvas is out of the stub's range and belongs
  in a DOM-free module instead, as `timewindow.js` does for the time-to-pixel projection.
- Firmware: `monitor.c`/`monitor_cmds.c` must compile with a host compiler.
  `firmware/tests/` holds a host-side harness (fake shims, feed lines in, assert
  responses) of 31 cases, wired into pytest by `test_firmware_monitor.py` through a
  makefile and skipping cleanly with no compiler. It is built and run twice, plain and
  under ASan/UBSan, which is what makes the reject-rather-than-truncate paths in 5.4
  worth asserting.
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
(must work offline): one `index.html`, one `style.css`, and vanilla-JS ES modules split
by panel (`app.js` plus `api.js`, `state.js`, `terminal.js`, `plots.js`, `digital.js`,
`can.js`, `cmdbar.js`, `settings.js`, `statusbar.js`, `theme.js`, `chrome.js` for the
shared colour store, colour picker and window selector, `freeze.js` for the pause-all
surface registry, `pane.js` for the pane model and `timewindow.js` for the time-to-pixel
projection). No framework. Logic reachable only through a laid-out canvas lives in the
DOM-free modules (`pane.js`, `timewindow.js`, `freeze.js`), because the test DOM stub
cannot lay one out and untestable drawing code is where the bugs hid. The original
"roughly 1200 lines total" guidance has been overtaken by the digital/enum panel and the
plot work; treat the no-build-step, no-network rule as the hard constraint and the size as
advisory. Dark theme default (it is a terminal, after all).

Layout is a terminal column beside a resizable right sidebar holding the CAN table and
the plot/digital panels: a CAN / Plots / Both switch, hide (with a reopen tab), an expand
toggle that widens the sidebar for chart work, and draggable dividers that double-click
back to their defaults.

Panels:

- **Status / setup bar**: daemon version and uptime; one chip per port showing alias,
  device, baud, connected state. "Attach" opens a dialog: device dropdown populated
  from `GET /devices` (show description and by-id path), baud dropdown (9600, 19200,
  38400, 57600, 115200, 230400, 460800, 921600, 1M, 2M, 3M, plus a custom field),
  alias text field. Detach button per port; a disconnected chip also offers **reconnect**
  (`POST /ports/{alias}/reconnect`), which skips the remaining backoff wait after a
  replug. A light/dark theme toggle sits in the bar. Errors from the API shown inline.
- **Terminal view**: one or more independently-filtered terminal panes laid out side
  by side. Add a pane or close one at any time (minimum one pane), so the operator can
  watch, say, "board-a CAN events" next to "sim debug" next to "everything". Each pane
  owns its filter controls (port selector, channel checkboxes, client-side regex match)
  and its autoscroll state. A single shared toolbar control selects the time base for all
  panes at once - host receive time, MCU tick, or relative from a common zero anchor (see
  9.2) - and drives the plot x axis too, alongside pause-all and clear-all.
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
- **Pause-all is one state over every freezable surface** (panes, charts, the digital
  panel), not a fan-out to three independent flags:
  - It governs surfaces created *after* it too: a pane added, or a chart built for a
    stream that first appears, while the UI is frozen comes up frozen.
  - Its label follows the surfaces, so it cannot read "resume all" while anything is
    live; resuming one surface on its own is enough to change it back.
  - Clear-all empties the views without resuming them. Pause is intent, and clearing is
    not a request to start moving again. It also re-zeros the shared relative-time and tick
    anchor at that instant, and is the only thing that moves that zero.
  - A frozen surface keeps showing what it froze for as long as it stays frozen, whatever
    the client-side buffers do underneath it.
  - Every surface records the highest line id it had ingested when it froze, and each
    export from it passes that as `id_to` (3.4), so a CSV from a paused chart is the frozen
    window and not the live edge. A surface that can freeze without one is a defect.
  - The high-rate guard below is deliberately **not** part of this state. It is automatic
    rather than intent, and folding it in would let a burst relabel the button "resume all"
    when the user had paused nothing.
- **High-rate guard**: the status bar shows a live lines/s readout, and above 2000
  lines/s the panes stop being fed (with hysteresis at 800, and a refill from the shared
  buffer when the flood subsides). The terminal is the only unbounded consumer: CAN and
  plots are bounded aggregations and keep updating throughout. The rate window is driven
  by a timer rather than by arrivals, so the guard cannot latch on when the flood stops.
  Status-bar readouts that come and go must not move the things beside them: the lines/s
  figure sits in a reserved, fixed-width box (tabular figures), and the "terminal paused"
  notice is a separate badge downstream of the port chips, because both used to shove the
  chips sideways every time the traffic changed.
- **Update notice**: when `GET /status` reports `update.available`, the status bar shows
  a badge naming the new version, linking to the project page, with the upgrade command
  in its tooltip (see 3.6). Dismissing snoozes rather than silences, on a ladder of one
  day, one week, one month, then permanently for that version; a newer version starts the
  ladder again. The state is per browser (localStorage), since it is a reading preference
  rather than daemon configuration.
- **Capture size**: the status bar shows the current capture size (and the cap, when one
  is set), so a size cap is set against a real number rather than a guess. `rx_dropped`
  surfaces as a warning on the port chip, since a capture with holes otherwise looks
  clean; `write_errors` surfaces as a second badge, for lines received and then lost
  before storage, which is the worse of the two.
- **Command box**: single input with a cmd/raw mode toggle. cmd mode posts to
  `POST /cmd` (timeout field, default 1000 ms) and renders the response inline
  (ok/err/timeout distinct); raw mode posts to `POST /send`. Up/down arrow history,
  persisted in localStorage.
- **CAN panel**: live table keyed by (port, CAN id, standard/extended), built client-side
  from `!can` events on the WebSocket: id (hex, ext/rtr flags), dlc, latest data, message
  count, estimated period in ms (EWMA of inter-arrival), age since last seen. As with plot
  channel names (9.2), an id is unique only within a port, so two boards both sending
  `0x100` get two rows. Reset clears the table; a `csv` button downloads exactly what is on
  screen, built client-side rather than through `/plot/export`. This gives the classic
  CAN-tool "latest state per id" view.
- **Marker**: text field plus button posting to `POST /marker`; markers render as
  distinct divider lines in the terminal view. Firmware markers (`!m`, section 2.5) render
  identically, with their `!m [@<tick>] ` wire prefix stripped for display and their tick
  feeding the shared time base like any other event's.
- **Session control**: a record button in the status bar starts and stops a named
  session. The daemon's automatic session does not read as "running" here: it was not
  started by anyone, it covers the whole daemon run, and treating it as running would
  leave the button permanently offering "stop" with no way to name a run.
- **Settings page**: edits the saved config via the 3.3.1 endpoints, so a fresh
  install is fully configurable from the browser. Sections: server (bind host,
  port), storage (db path, retention days, size cap, session floor, automatic
  sessions), updates (the 3.6 opt-out, applied live, noting that `MCUSCOPE_UPDATE_CHECK=0`
  overrides it), recorded sessions, an access token field, and the saved ports list
  (add/edit/remove rows of alias, device, serial number, baud and auto-attach; device
  dropdown fed by `GET /devices`, or a serial_number field). The storage section shows the current capture size next to the cap, so a cap
  is chosen against a real number. The sessions section lists recent runs with their
  line counts and offers per-run **export** (downloads a standalone capture database)
  and **delete** (removes that run's lines, after a confirmation naming the run and the
  count). Also shows the config file path, an "auth: token set / not set" indicator
  (read-only), and a persistent "restart daemon to apply" badge while
  `restart_required` is true. The attach dialog gains a "save to config" checkbox that
  updates the saved ports list alongside the runtime attach.

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
CREATE INDEX idx_plot_line ON plot_points(line_id);   -- the cascade's side of the FK
```

- New endpoints: `GET /plot/channels` (distinct names with sid, unit, scale, type
  where known from the definition cache, last value, point count, and the `port` the
  newest sample came from) and
  `GET /plot/series?name=&port=&last_ms=&since_id=&id_to=&limit=10000&decimate=N` (history;
  `decimate` > 1 reduces a long window by **min/max** decimation: buckets of N points,
  each contributing its lowest and highest sample, so a transient still shows as a spike
  instead of aliasing away between kept samples. A bucket yields up to 2 points, so the
  reduction is about N/2). Live data comes from the existing WebSocket; no new
  streaming path. `limit` clamps to 0..100000 and `decimate` floors at 1, as on every list
  endpoint (3.4); `limit=0` returns no rows and is a deliberate "follow only, no backfill"
  request, not an error.
- **Channel names are unique only within a port.** `plot_points` stores no port of its
  own; each row is attributed through the line it came from. With two boards attached,
  both declaring `temp`, an unfiltered `/plot/series?name=temp` therefore returns both
  boards' samples interleaved, with non-monotonic ticks, under whichever unit and scale
  the later `!pd` declared. Pass `port=` on `/plot/channels` and `/plot/series` to scope
  to one board. A future revision should key channels by (port, name) throughout;
  until then the `port` field on `/plot/channels` is what makes the collision visible.
- CSV export (required, not optional): `GET /plot/export?names=&last_ms=&id_to=&format=long|wide`
  streaming CSV. `long` is `ts,tick_ms,sid,name,value` one point per row; `wide`
  requires all requested names to share one sid and emits `ts,tick_ms,<name>,...`
  one sample line per row. A selection over 1000000 rows is refused with 400 rather than
  truncated, so a half-written export is never mistaken for the whole window. Exposed as a
  per-panel export button (current window, checked channels) and CLI
  `mcu plot export --names a,b --last-ms N [--wide] -o file.csv`. The button sends `wide`
  from a stream chart, whose channels share one sid, and `long` from the ad-hoc chart and
  the digital panel, whose lanes may span streams so `wide` is not valid for them.
- CLI also gains `mcu plot channels` (list) for discoverability.
- UI plot panel: **one chart per stream** (sid), plus one chart for ad-hoc `!p`
  channels, stacked vertically with a shared, synchronized x axis (linked cursor; the
  visible range is set by the window selector, and there is no drag zoom: the charts are
  right-anchored on live data, so pausing and exporting the frozen window as CSV is the
  path to a closer look). Streams may have very different sample rates, and every point carries
  its own timestamp, so per-stream charts are the default organization, not a
  correctness requirement. Within each chart: channel checkboxes (auto-discovered
  from incoming events and `/plot/channels`, showing units), selectable time window
  (5 s, 30 s, 5 min), pause/resume, cursor value readout with unit, and a per-channel
  swatch that recolours the trace, persisted per browser and shared with the digital
  lanes. Client keeps a ring buffer per channel (cap around 100k points) and shows at most
  64 analog channels and 64 digital lanes, saying so in the panel count when a cap is hit,
  since a device emitting rotating channel names would otherwise grow the DOM forever. Channels with very different ranges
  get independent y scales (the y axis is left undrawn; values are read from the legend),
  and traces are stepped (hold-last), not linearly interpolated.
- Time base: a single control shared with the terminal selects **host receive time**, **MCU
  tick**, or **relative** (relative time and tick both zero at a common reset point), and it
  drives both the pane timestamp column and the plot x axis at once, so the two views always
  read the same clock. The plot cursor is linked across all charts (shared x) and can also be
  driven by hovering a line in the terminal, which places every chart's cursor at that line's
  time.
- **Digital / enum panel**: enum and packed-bit channels (2.5) do not belong on an
  auto-ranged y axis, so they render as logic-analyser lanes below the charts, in the same
  scroller and on the same time base. Bits draw as square waves, enums as a bus envelope
  with X-crossings and the label centred in each segment; packed lanes are grouped under
  their parent channel name. One vertex per value change, not per sample. Its header
  mirrors a chart's (collapse, lane count, time window, pause, `csv`) and it is a freeze
  surface like any other, with the same cursor linkage to the charts and the terminal.
  Per-lane show/hide and colour, both keyboard-operable.
- Overlaying channels from different streams on one chart is nice-to-have:
  implement only if trivial, otherwise leave as **[P2]**.
- **[P2] Markers on the charts.** Draw `marker` rows as vertical annotation lines across
  every chart, sharing the x axis and the linked cursor, with the text on hover or as a
  rotated label. This is the payoff for firmware markers (`!m`, section 2.5) carrying an
  optional MCU tick: "the fault happened here" is worth far more against the trace than
  against the scrollback. Placement follows the shared time base, so a marker with a tick
  places exactly in **MCU tick** mode while one without (`mcu mark`, a session boundary,
  or firmware with no clock) can only be placed by host receive time; in tick mode those
  need either omitting or interpolating from the neighbouring samples, and the choice
  should be explicit rather than silent. Needs a way to fetch markers for a window
  (`/lines?chan=marker` already serves it) and a decision on whether `plot_points` or a
  marker-specific endpoint feeds the chart.

## 10. Later phases (design intent, do not build in v1)

- **[P2] Flash + reset**: config gains `[tools]` with command templates
  (`openocd`/`st-flash`/`probe-rs`); daemon endpoints `POST /flash {port, file}` and
  `POST /reset {port}` that pause the serial port, shell out, resume, and log a `sys`
  row; CLI `mcu flash FILE`, `mcu reset`. This enables the autonomous
  edit-build-flash-test loop.
- **[P2] MCP wrapper** (section 6).
- **[P2] DBC decoding**: optional `dbc` path per port; frames decoded **at query time**,
  not stored, and returned by `GET /can/frames?decode=1` and `mcu can dump --decode`;
  `cantools` behind an optional `mcuscope[dbc]` extra. Storing decoded text alongside
  frames was the earlier intent and is rejected: the only shape that would make it
  searchable through `/lines`, `/wait` and `/assert` needs a seventh `chan` value, which
  the CHECK constraint in 3.5 cannot take by migration. Design note, including the
  interactions that must be priced first: `docs/DBC_DECODING.md`. Register-map decoding
  shared this bullet and shares none of its machinery; it is unscoped.
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
