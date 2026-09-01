# MCUscope measurement leg - 2026-09-01

Isolation: throwaway dir `/tmp/mcuscope-review/`, config `config.toml`
(`[server] host="127.0.0.1" port=8611`, `[storage] db_path=".../capture.db"`).
Daemon: `uv run mcuscoped --config .../config.toml --sim --port 8611`, backgrounded
via `nohup ... &`. Real daemon process was the child python (`.venv/bin/mcuscoped`),
not the `uv run` wrapper; killed the child directly with `kill -TERM`.
All `mcu` calls used `MCUSCOPE_URL=http://127.0.0.1:8611`.

## Pre-flight: help text

`uv run mcuscoped --help`: flags are `--version -c/--config --host --port --token
--sim --plotjuggler/--pj [HOST:PORT] --open --ignore-capture-lock`. **No way to pass
sim args (e.g. `--plot`) through `mcuscoped --sim`** - it takes no sim-forwarding
flags at all.

`uv run mcu-sim --help`: standalone sim has `--tcp-port --pty --symlink
--drop-response N --garbage --plot --plot-late-def --flood N`. `--plot` is
documented as required to "emit ad-hoc !p and typed !pd/!ps plot streams at 20 Hz".

**Finding (not a bug, just worth noting): `mcuscoped --sim` emits `!p`/`!ps`/`!pd`
plot lines by default**, with no `--plot` flag needed and no way to disable it
short of not using `--sim`. This contradicts the standalone `mcu-sim --help` text
which implies `--plot` is opt-in. Either the in-process sim used by `--sim` always
runs with plotting on, or the standalone default differs from the in-process
default. Not exercised further since it made decode testing possible without extra
setup; worth a doc/behaviour check by someone who owns sim.py.

`uv run mcu ai-guide`: printed full guide, matches SPEC section 4 command shapes.

## 1. Volume commands (capture was already ~7000+ lines within seconds of daemon
start because of the always-on plot stream at 20 Hz + CAN heartbeat; the ~2 min
wait for >1000 lines was not needed)

| command | exit | time | result |
|---|---|---|---|
| `mcu lines --limit 3000` | 0 | 0.497s | 3000 lines printed, stderr note "results truncated at 3000 rows; older matches exist (raise --limit or use --since-id)" |
| `mcu lines --limit 3000 --json` | 0 | 0.459s | stdout parses as **one** JSON object; `len(lines)==3000`; `truncated: true` (correct - more rows exist) |
| `mcu log export -o /tmp/x.jsonl --json` | 0 | 0.874s | stdout: `{"file":..., "lines": 12322, "bytes": 1609529, "truncated": false}`; `wc -l x.jsonl` = 12322, exact match |
| `mcu log export --limit 10` (no `-o`, literal form from task) | 0 | - | writes JSONL/text rows straight to **stdout** (10 rows), stderr truncation note - correct, no anomaly |
| `mcu tail -n 1500` | 0 | 0.414s | 1500 lines, stderr truncation note (same wording as `lines`) - reasonable since older rows exist beyond the -n window |

No anomalies. All commands well under the 2 s slow-command threshold. Row counts
and JSON structure all matched.

## 2. `--from`/`--to`

Picked `A=23:01:00.000`, `B=23:01:10.000` from a prior `lines --limit 3000` sample
(range 23:00:56.107-23:01:25.258).

| command | exit | result |
|---|---|---|
| `mcu lines --from A --to B --limit 5000` | 0 | 1026 rows; **verified all 1026 timestamps satisfy A <= ts <= B** (python check, 0 out of range) |
| `mcu lines --from B --to A --limit 100` (from later than to) | 0 | empty stdout, empty stderr - no error, silently returns nothing |
| `mcu lines --to 00:00:00 --limit 100` | 0 | empty stdout, empty stderr |
| `mcu lines --from 23:59:59 --limit 100` | 0 | empty stdout, empty stderr |
| `mcu lines --from garbage --limit 100` | 1 | stderr: `Error: Invalid value for '--from': expected HH:MM[:SS[.mmm]] or YYYY-MM-DDTHH:MM:SS, got 'garbage'` - correct usage error |
| `mcu lines --from 2026-09-01T12:00:00 --limit 100` | 0 | 100 rows returned, but they are the **newest** 100 rows in the from-bound window (timestamps 23:04:12-23:04:13, i.e. captured 3+ minutes after the from-bound started), not the oldest. Truncation note says "older matches exist" - consistent with "returns most-recent-first, drops older ones", matches the general `--limit`/`truncated` contract in SPEC. Not flagged as a bug, but worth noting the naive reading of "give me from noon onward, limit 100" plausibly expects the *earliest* 100, not latest 100; SPEC doesn't state the order explicitly. |

**Possible anomaly (soft):** `--from` later than `--to` silently returns an empty
result with exit 0 rather than a usage error. SPEC section 4 doesn't call out this
combination, so this is a judgement call, not a clear defect - flagging for
someone who owns the CLI to decide if a 400/usage-error would be friendlier than
a silent empty capture window (an agent could easily mistake "no lines" for "no
events happened" when it actually mis-ordered its own bounds).

## 3. `--decode` / `--changes` / `--names`

Confirmed SPEC line 998 ("In `--json` mode the decoded text is in `decoded` and
**replaces** `raw`") - this explains why `--json --decode` rows show identical
`raw` and `decoded` strings; this is spec-compliant, not a bug (initially looked
suspicious until checked against SPEC.md:998).

- `mcu lines --limit 20 --decode`: `!pd` rows correctly dropped from output;
  `!ps` rows rendered as `s<sid> name=value ...` (e.g. `s0 tri=-4.72V ramp=30629
  ftest=0.825311`, `s1 state=ARMED`, `s2 gpio=pwm_en`/`gpio=-`); enum label and
  unit-suffix and bit-lane-join (`|`, `-` for none) all confirmed working.
  Ad-hoc `!p` lines rendered as `p:sine,noisy sine=... noisy=...` (a `p:`
  name-list prefix instead of `s<sid>`, since ad-hoc streams have no stream id -
  reasonable, not explicitly pinned by SPEC wording but consistent in spirit).
- `mcu lines --limit 20 --changes`: fewer rows printed than without `--changes`
  (12 of 20 possible slots used); each printed row differs from that stream's
  previous rendered value - confirmed by eye, no obvious duplicate values printed
  back-to-back for the same stream.
- `mcu lines --limit 20 --names vbat`: correctly returns rows with no `vbat`
  channel matched (sim has no `vbat` channel; only 3 unrelated `!can` rows shown,
  no plot samples) - `--names` with an unknown name drops all plot samples as
  documented ("drops samples with none left").
- `mcu lines --limit 5 --decode --json`: exactly one JSON object,
  `len(lines)==5`, every row has both `raw` and `decoded` keys holding the same
  decoded string (per spec).
- `mcu log export --decode --limit 10` (stdout) and `--decode --json --limit 10
  -o file`: same decode behaviour confirmed in export path; JSONL file rows also
  carry matching `raw`==`decoded`.
- `mcu tail -n 20 --decode --changes --names state`: only `state` plot field
  rendered when present; unrelated `!can` lines pass through unfiltered (as
  expected - `--names` only touches plot-sample rendering).
- `mcu tail -n 5 --json`: confirmed **JSONL**, not one object - 5 separate lines,
  each independently `json.loads`-able (matches SPEC section 4 "tail is a
  per-row emitter" contract). Fields present: `id, ts, port, dir, chan, seq, raw`
  (no `decoded` key without `--decode`, correct).
- `timeout 5 uv run mcu tail -f --decode --changes`: exit 124 (killed by
  `timeout`, expected for an unbounded follow), 322 lines emitted in 5 s, decoded
  format matches the non-follow path, no parse errors, no interleaved
  garbage/partial lines.

No anomalies found in decode/changes/names beyond the two notes above (both
resolved as spec-compliant on reading SPEC.md, not bugs).

## 4. `mcu status` / `mcu status --json` / `mcu ports`

```
mcuscoped 0.3.0  up 36s  db /tmp/mcuscope-review/capture.db
  session: auto-2026-09-01_22-59-26 (id 1, running)
  sim        sim://demo  @115200  connected target=sim  rx=3697 tx=1
```
`target=sim` present on the port line as expected.

`mcu status --json` field names on `ports[0]`: `write_failures`,
`last_write_error`, `last_write_error_ts`, `write_failing_since`, `target` - all
present, all null/0 as expected for a healthy connected sim port. Also present:
`version, pid, uptime_s, db_path, db_size_bytes, db_content_bytes, db_max_bytes,
lines_trimmed, write_errors, writer_alive, ws_dropped, capture, session, update,
plotjuggler, ports`.

`mcu ports`: `sim        sim://demo  @115200  connected target=sim` - matches
`status` port line minus rx/tx.

No anomalies.

## 5. `cmd` / `can tx` / retry / `--json`

| command | exit | time | notes |
|---|---|---|---|
| `mcu cmd ping --retry-ms 500` | 0 | - | `monitor 1 sim` |
| same `--json` | 0 | - | one JSON object: `{"status":"ok","seq":3,"data":"monitor 1 sim","latency_ms":9.49,"line_id":53327}` |
| `mcu can tx 100 AA --retry-ms 300` | 0 | - | empty stdout (no response data for a CAN tx, expected) |
| same `--json` | 0 | - | one JSON object, `status: ok`, `data: ""` |
| `mcu cmd "nonsense" --retry-ms 300` | 1 | 0.303s | stderr `ERR 1 badcmd unknown nonsense` |
| same `--json` | 1 | - | one JSON object: `{"status":"err","seq":7,"err_code":1,"err_name":"badcmd","err_detail":"unknown nonsense","latency_ms":7.9,"line_id":53462}` |

Verified "one attempt" for a non-busy error: measured baseline overhead of a bare
`uv run mcu status`/`mcu cmd ping` (no retry) is ~0.30s (python/uv startup cost),
and `cmd nonsense --retry-ms 300` (3 repeated runs) was consistently ~0.29-0.30s
too - i.e. it did **not** burn the full 300 ms retry budget on a `badcmd` error,
confirming retry only applies to `ERR 6 busy`, not to arbitrary errors. Exit codes
match SPEC (0 success, 1 error). Every `--json` invocation printed exactly one
JSON object on stdout, stderr empty.

No anomalies.

## 6. `mcu plot channels`

`mcu plot channels` and `--active 5`: identical channel sets both times (9
channels: ftest, irq, led, noisy, pwm_en, ramp, sine, state, tri) because the sim
streams continuously at 20 Hz, so every channel has `age=0s` and none get
filtered by a 5 s activity window - expected given the sim's behaviour, not
useful for exercising the "hides stale ones" path (would need to stop a stream to
see a channel dropped, out of scope here).

`--json` forms: one JSON object each, `channels` array with 9 entries, fields
`name, port, sid, type, unit, scale, kind, labels, group, bit, last_value,
last_tick, last_ts, count`. Enum channel (`state`) correctly carries
`labels: [[0,"IDLE"],[1,"ARMED"],[2,"RUN"]]`; bit channels (`irq/led/pwm_en`)
carry `kind: "bit", group: "gpio", bit: N`; ad-hoc channels (`sine/noisy`) carry
`sid: null, type: null` as expected for a stream with no `!pd` definition.

No anomalies.

## 7. Sessions across a daemon restart

- `mcu session start bench-x` -> exit 0, `session 2 started: bench-x`.
- `mcu status` -> `session: bench-x (id 2, running)`.
- `kill -TERM <child python pid>` (89296): process (and its `uv run` wrapper
  89285) both exited within 1 s (checked every 0.5 s, gone by the 2nd check).
- Restarted daemon with **identical** `--config ... --sim --port 8611`: new
  child pid 95834.
- `mcu status` after restart: `session: bench-x (id 2, running)` - **session
  correctly resumed**, not silently dropped or auto-closed.
- `mcu lines --chan sys -n` (literal form from task instructions): **exit 1**,
  `Error: No such option: -n` - `-n` is a `mcu tail` flag, not a `mcu lines`
  flag; this is a correct usage error, not a daemon bug (a probe defect on my
  part, per the task's own instruction to treat a 4xx/usage error against my own
  probe as a probe issue).
- `mcu lines --chan sys --limit 20`: contains the expected sequence -
  `daemon start` / `port sim connected` / `port sim target` (first boot),
  `daemon stop`, `daemon start`, **`resuming session: bench-x`**, `port sim
  connected`, `port sim target` (second boot). Exactly as SPEC/task expects.
- `mcu session stop` -> exit 0, `session 2 ended: bench-x (lines 61294-69780)`,
  correctly reports bench-x (not the new auto session that had already opened
  implicitly... actually no new auto session existed until stop closed bench-x).
- `mcu session list` -> 3 sessions listed, newest first: a new
  `auto-2026-09-01_23-11-07` (running, opened automatically after bench-x
  stopped), `bench-x` (ended, 8487 lines), the original
  `auto-2026-09-01_22-59-26` (ended, 61292 lines). Consistent with session
  bookkeeping.

No anomalies.

## 8. `mcu lines --match "^!e"`

Exit 0, empty stdout, empty stderr - correctly empty since the sim never emits
lines matching `^!e` (no `!e...` error/exception lines exist in this run). Matches
task expectation.

## 9. Teardown

`kill -TERM` on the second daemon's child pid (95834): both it and its `uv run`
wrapper (95830) exited within 1 s. Final `ps aux | grep -E
"mcuscoped|mcu-sim|mcu_sim"` (excluding the grep itself): **no matches** - clean,
nothing left running that this review started.

## Summary of anomalies

1. **(soft, judgement call)** `mcu lines --from X --to Y` with `X` later than `Y`
   returns an empty result set with exit 0 and no stderr note, rather than a
   usage error - silent-empty could be misread by an agent as "nothing happened"
   rather than "your bounds were backwards". SPEC doesn't specify the expected
   behaviour for this combination either way.
2. **(informational, not a defect against SPEC)** `mcuscoped --sim` always
   streams `!p`/`!ps`/`!pd` plot data at 20 Hz with no flag to control it, which
   differs from `mcu-sim --help`'s framing of `--plot` as opt-in for the
   standalone simulator. No CLI/API surface exposes turning it off short of not
   using `--sim`. Nothing in SPEC or the CLI table promises `--sim` is
   plot-free, so not filed as a bug, just flagged for whoever owns `sim.py`.

Everything else measured (volume commands, `--decode`/`--changes`/`--names`,
`status`/`ports`, `cmd`/`can tx`/retry/`--json` shape, `plot channels`, session
resume across a clean restart, `!e` match) matched SPEC section 4, 3.2, and the
`!e` paragraph in 2.5 exactly, all commands fast (<1 s, well under the 2 s
threshold), all `--json` output was exactly one JSON object (or valid JSONL for
the three documented per-row emitters), and exit codes matched the 0/1/2/3
contract throughout.
