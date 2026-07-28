# DBC decoding: design note

**Status: designed, not scheduled.** Nothing here is committed work.
This document exists so the decisions below are not re-derived, and so the two traps in it are not walked into.
`docs/SPEC.md` section 10 remains the authority on scope; this note records why that entry now reads the way it does.

## Scope

Decode CAN payloads into named signals using a DBC file, so `id=100 data=1A2B0000` reads as `EngineSpeed 1675 rpm`.

In scope:

- An optional `dbc` path per port, in config and on port attach.
- Decoded signals returned by `GET /can/frames` and printed by `mcu can dump --decode`.
- Graceful degradation when the DBC is absent, unparseable, or the frame's id is unknown.

Out of scope, deliberately:

- Feeding decoded signals into plot channels as trendable engineering units.
  That wants the host-side channel registry first (`docs/IDEAS.md`, "Shared foundations"), and it is a different, larger feature.
- Register-map decoding, which SPEC 10 once bundled into the same bullet but which shares no machinery with DBC.

## Decision 1: decode at query time, not at ingest

SPEC 10 originally said "decoded signal text stored alongside frames".
That has been amended, because the reason to store it does not survive contact with the schema.

The argument for storing was that decoded text would become searchable through `/lines`, `/wait` and `/assert`.
It would not, and the two ways to make it so are both bad:

- **A `decoded` column on `can_frames`** is a one-line entry in `_MIGRATIONS` (`host/mcuscope/store.py:91`), but the match endpoints search `lines.raw` and nothing else (SPEC 3.4).
  The column would be invisible to exactly the endpoints that motivated it.
- **A synthetic `lines` row per decoded frame** would be searchable, but needs a seventh channel value, and `chan` carries a CHECK constraint enumerating six (`host/mcuscope/store.py:36`).
  SQLite cannot ALTER a CHECK constraint, and `_apply_migrations` only does ADD COLUMN (`host/mcuscope/store.py:119`), so this is a full rebuild of the hot table.
  It would also roughly double the row count on a CAN-heavy capture, which lands on retention, the size cap, session id ranges, WebSocket fan-out and exports at once.

Three further reasons query time is right for this codebase specifically:

- The single async writer's whole design is a batch going in as three `executemany` calls (`host/mcuscope/store.py:338`).
  Per-frame Python decode in that path taxes every CAN burst for a feature most captures never use.
- Bench DBCs churn during firmware development, unlike the frozen vehicle DBCs the format was built for.
  Stored text goes stale precisely in this tool's use case; query-time decode re-reads last week's capture with today's DBC for free.
- `export_session_db` copies `can_frames` with an explicit column list (`host/mcuscope/store.py:691`).
  A stored column silently drops from exports unless threaded through by hand. Query time never creates the obligation.

## Decision 2: cantools, as an optional extra

Use `cantools` (MIT, 42.0.3 at time of writing) rather than parsing DBC by hand.

Every cantools release from 36 to 42 declares `python-can` as a hard dependency, even though DBC parsing only needs `bitstruct` and `textparser`.
The transitive weight is mild (python-can's own core deps are `wrapt`, `packaging`, `typing_extensions`), but it is still weight the base install should not carry for a feature most users will not touch.
Ship it as an extra, `mcuscope[dbc]`, and report "DBC support not installed" when it is absent.
Tests skip cleanly without it, the same way the firmware tests skip without a C compiler.

Writing the parser instead is two to three days and a permanent correctness liability.
The `BO_`/`SG_`/`VAL_` grammar is easy; the bit math is not.
Motorola big-endian start-bit numbering is the most reimplemented-wrongly code in the CAN world, and signed/unsigned, scale/offset, multiplexing (`m0`/`M`), extended multiplexing (`SG_MUL_VAL_`) and value tables all follow behind it.

## Interactions to price before starting

These are the parts a naive implementation misses.

- **`/can/frames` runs synchronously on the event loop** (`host/mcuscope/server.py:959` calls `query_can_frames` with no await).
  Decoding up to 1000 frames per request there is loop-blocking work, and `mcu can dump -f` polls at 5 Hz (`host/mcuscope/cli.py:849`).
  The established pattern is `query_lines_safe` on `match_executor` (`host/mcuscope/store.py:840`), but that pool also serves user regexes and the serial-reader join, so parking decode on it is a decision, not a freebie.
- **The web UI is the flagship CAN surface, not the CLI.**
  SPEC 9.1 is titled "terminal, setup, decoded CAN view", and `host/mcuscope/webui/can.js` already implements the panel.
  A CLI-only `--decode` leaves the UI showing raw hex beside a CLI showing signal names.
  Either accept that gap explicitly for one release, or price the endpoint plus the JS.
- **The config surface is wider than `PortConfig`.**
  The dataclass is five fields (`host/mcuscope/config.py:72`), but a `dbc` field also threads through `load_config`, the SPEC 3.3.1 write-back API, the `PortAttach` body in `server.py`, `/status`, and the port dialog in `webui/settings.js`.

## Gotchas

- **DBC files are frequently cp1252 or latin-1, not UTF-8.**
  Decode with an explicit fallback or loading dies on the first non-ASCII unit string.
- **Vehicle DBCs run to megabytes and thousands of messages.**
  Load once and cache keyed by path plus mtime; never per request.
- **Truncated payloads must degrade, not raise.**
  Real buses send frames shorter than the DBC's message length, and this codebase's rule for malformed CAN is already "return None, store the line anyway" (`host/mcuscope/protocol.py:302`).
- **A decoded twelve-signal message far exceeds the 255-byte line the wire protocol assumes.**
  `fmt_frame` (`host/mcuscope/cli.py:84`) needs a real layout decision, not a longer f-string.
- **One DBC path per port is correct here, despite multi-DBC being normal in vehicle work.**
  A port is one serial link to one MCU relaying one bus, so per-port already is per-bus.
  A list is a cheap TOML migration later if a bench ever needs it.
- **The bit-31 extended-id flag is a non-issue with cantools**, which returns `frame_id` pre-masked with `is_extended_frame` separate.
  It only bites the hand-written parser path.

## Effort

| Scope | Estimate |
|---|---|
| Query-time decode, CLI and REST only, UI deferred | 1.5 to 2 days |
| The same, plus the web UI panel | add roughly half a day |
| Ingest-time storage as SPEC originally read | 3 to 4 days, and rejected above |
| Hand-written DBC parser instead of cantools | add 2 to 3 days, and rejected above |

The 1.5 to 2 days covers a new `host/mcuscope/dbc.py` (load, cache, match, decode, format, never raise), the config and server surface above, the CLI flag and `--json` shape, off-loop decode, a sample `.dbc` fixture decoded against the simulator's 0x100 heartbeat, and a with/without-cantools test matrix (Intel and Motorola layouts, signed, scale/offset, multiplex, value-table labels, unknown id, truncated payload).

## When to build

Not now, and not on speculation.

The honest case against it: the in-scope deliverable is readable signal text at a terminal, which mostly serves a human, while the version that serves a blind AI agent is signals as trendable numbers feeding numeric asserts.
That second version is the part held out of scope above.
So the cheap half is the less valuable half, and the tier 1 items in `docs/IDEAS.md` pay off on every bench whether or not it has CAN on it.

Build this when a concrete bench project turns up with a `.dbc` file in hand.
