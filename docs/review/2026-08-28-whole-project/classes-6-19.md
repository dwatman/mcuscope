# Review round 2 - registry sweeps, classes 6 and 19

Repo: /home/daniel/git/mcuscope, HEAD fd76735 ("POST /ports held to the config-write bar").
Read-only. node v22.23.2, python via `uv run` from `host/`.
Probe scripts live in /tmp/claude-1000/review-r2/ (probe1.mjs, probe2.mjs, probe_inf2.py, probe_inf4.py, probe_cfg.py, probe_fw.c, probe_fw2.c). Nothing was written inside the repo.

---

## Class 6: non-finite values reaching chart arrays

Invariant: nothing pushes NaN or Infinity into a uPlot data array; one such value blanks the series.
Sweep: list every producer writing into plot/digital data arrays; each must gate on `Number.isFinite` at its own boundary.

Enumeration command (mechanical, all 15 files under `host/mcuscope/webui/`):

```
grep -n "xsHost\|xsTick\|\.vs\b\|chart\.ys\|ys\.get\|ys\.set\|\.frozen" plots.js digital.js freeze.js api.js state.js
grep -n "\.push(\|\[i\] *=\|\.set(\|setData\|data\[" plots.js digital.js
```

Only `plots.js` and `digital.js` hold chart/lane arrays; the other 13 webui modules contain no write into one (`can.js` holds a table, `terminal.js` a row buffer, `api.js` the shared line buffer, `state.js` `pushBuffer` for rows, `statusbar.js`/`settings.js`/`chrome.js`/`cmdbar.js`/`freeze.js`/`pane.js`/`theme.js`/`timewindow.js` none).

**Site count returned by the sweep: 19** (12 array-mutation sites plus the 7 upstream value/x producers that feed them).

### Array-mutation sites

| # | Site | Verdict |
|---|------|---------|
| 1 | plots.js:412 `chart.xsHost.push(hx)` | **complies** - gated at plots.js:406 (`Number.isFinite(x.host) && Number.isFinite(x.tick)`) in the same function, before the monotonic bump. |
| 2 | plots.js:413 `chart.xsTick.push(tx)` | **complies** - same gate, plots.js:406. |
| 3 | plots.js:430 `chart.ys.get(name).push(val)` | **complies** - every `val` reaching here came through one of the three value producers (13, 14/15, 16), each of which rejects non-finite at its own boundary. Verified by probe P2 below: an `f4` sample of `7F800000` never lands in `ys`. |
| 4 | plots.js:438 `chart.ys.get(name).push(null)` (gap fill for a channel absent from this sample) | **exempt because** the literal `null` is uPlot's own gap marker, not a number; `rangeNum` skips it. |
| 5 | plots.js:444-445 `xsHost/xsTick/ys.splice(0, drop)` (block trim) | **exempt because** it only removes elements; it cannot introduce a value. |
| 6 | plots.js:456-457 `addChannel` backfill `new Array(n).fill(null)` | **exempt because** it writes only `null` (site 4's reasoning). |
| 7 | plots.js:873-874 `chart.frozen` pause snapshot (`arr.slice()`) | **exempt because** it is a copy of arrays already gated at 1-3; a slice cannot introduce a value. |
| 8 | digital.js:66 `lane.xsHost.push(hx); lane.xsTick.push(tx); lane.vs.push(val)` | **complies** - x gated at digital.js:37 with the same two-clause check as plots.js:406 (and a comment saying why a non-finite x is permanent in a lane). `val` reaches here only through `routePoints` (plots.js:263-272), whose two callers are `plotIngest` (live, gated by 14/15) and `plotSeed` (gated by 16). Digital values are additionally always integer-valued by construction: the bits branch pushes `(bits >> b) & 1` and the enum branch an integer field. |
| 9 | digital.js:70 lane block trim `splice(0, drop)` | **exempt because** removal only. |
| 10 | digital.js:99 `l.frozen = {xsHost: slice(), xsTick: slice(), vs: slice()}` | **exempt because** copy of gated arrays. |
| 11 | digital.js:144 `lane.frozen = {xsHost: [], xsTick: [], vs: []}` (lane born after a freeze) | **exempt because** it writes empty arrays. |
| 12 | plots.js:712-715 `currentData` (the array actually handed to `uplot.setData`) | **complies** - it slices the gated arrays and substitutes `new Array(n).fill(null)` for a channel with no array; it performs no arithmetic, so it cannot manufacture a non-finite value. Note the *time-mode* transforms (`rel`, `tick`) shift only the display labels (`fmtPlotX`, plots.js:621-628) and never the data, so no scaled copy of the x array exists. |

### Upstream value / x producers

| # | Site | Verdict |
|---|------|---------|
| 13 | plots.js:66-70 `parsePlotValue` (ad-hoc `!p` value and `*scale` factor) | **complies** - `Number.isFinite(v) ? v : null` at :69, rejecting an in-grammar literal that overflows (`1e999`). Mirrors `protocol.parse_plot_value` (protocol.py:557). |
| 14 | plots.js:184-201 `decodePlotField` (typed field, `f4` branch) | **complies** - `Number.isFinite(f) ? f : null` at :195. |
| 15 | plots.js:216-228 `decodePlotSample` (bits lane / enum / analog after `*scale`) | **complies** - re-checks at :227 after the scale multiply, which is the second historical instance in this class (a finite sample carried to Infinity by a large scale). Bits and enum branches carry integers. |
| 16 | plots.js:291-311 `mergeSeedSeries` (the `/plot/series` history seed) | **complies** - `if (!Number.isFinite(pt.value)) continue;` at :301, with a comment stating exactly why it cannot rely on the daemon (see class 19 finding V2). |
| 17 | plots.js:406 `addSample` x gate | **complies** - this *is* the gate. |
| 18 | digital.js:37 `digitalIngest` x gate | **complies** - this *is* the gate. |
| 19 | plots.js:408-409 and digital.js:63-64 monotonic bump (`lastHost + 1e-4`) | **complies** - operates on an already-gated finite value plus a finite constant; it runs after the gate in both files, which is the ordering the plots.js:400-405 comment calls out. |

### Class 6 result

**19 sites, 0 violates, 0 suspected.** Every producer gates at its own boundary; the two x-side gates and the three value-side gates are all present, and the seed path carries a fourth.

Probe P2 (`/tmp/claude-1000/review-r2/probe2.mjs`, run under `node`):

```
ev("!pd 0 t:f4"); ev("!ps 0 3E8 7F800000"); ev("!ps 0 3E9 3F800000");
-> JS ys after inf then 1.0: [ [ 't', [ 1 ] ] ]
```

The Infinity sample is dropped whole; only the finite one reaches the array.

**Cross-reference:** the *daemon* stores the same sample as `inf` (class 19, V2/V3). That is not a class-6 violation, because site 16 gates it out before it reaches a chart array, but it is what makes V2 more than cosmetic.

---

## Class 19: two engines validating one thing

Invariant: a check performed in two places uses the same implementation, or the looser side is not a check at all.
Sweep: list every validation duplicated between client and daemon, or between host and firmware, and name the single implementation both use. Where they cannot share code, diff the mirror against the original clause by clause.

Enumeration: every mirror in `host/mcuscope/webui/*.js` against `host/mcuscope/protocol.py` + `server.py` + `config.py`; every hand-written validator in `firmware/monitor/*.c` against `protocol.py`; plus the CLI's `--match` path and the config-loader/API pair.

**Site count returned by the sweep: 43.**

### A. Browser vs daemon - the plot grammar (plots.js vs protocol.py)

| # | Mirror pair | Verdict |
|---|-------------|---------|
| 1 | plots.js:66 `parsePlotValue` vs protocol.py:541 `parse_plot_value` | **complies** - grammar regex identical (`-?\d+(\.\d+)?([eE][+-]?\d+)?`, anchored both sides); both reject non-finite after parse. JS `\d` is ASCII-only, matching `[0-9]`. |
| 2 | plots.js:72 `parsePlotAdhoc` vs protocol.py:562 `parse_plot_adhoc` | **complies** - `!p` token, `>= 3` parts, `isDecimalToken` + `> 0xFFFFFFFF` tick bound, name length 16, name regex, duplicate-name rejection, empty-points rejection: all six clauses present both sides. |
| 3 | plots.js:93 `parseChannelSpec` vs protocol.py:659 `_parse_channel_spec` | **complies** - field count 2..3, name validity, type membership (JS uses a null-prototype object so `"toString" in PLOT_TYPES` is false), `*scale` parse, empty-unit rejection, scale-on-enum/bits rejection, `_ENUM_TYPES`/`_BITS_TYPES` membership: all present. |
| 4 | plots.js:129 `parseEnumLabels` vs protocol.py:629 `_parse_enum_labels` | **VIOLATES (confirmed)** - see finding **V1**. |
| 5 | plots.js:150 `parseBitLanes` vs protocol.py:649 `_parse_bit_lanes` | **complies** - empty-name-to-null mapping, per-lane name validity, non-empty, `<= width*8`, at-least-one-named: all five clauses present. |
| 6 | plots.js:157 `parsePlotDef` vs protocol.py:594 `parse_plot_def` | **complies** - single-ASCII-digit sid (`/^\d$/`, ASCII in JS), per-spec validity, cross-field name uniqueness over channel *and* lane names in one namespace (plots.js:169-180), non-empty. |
| 7 | plots.js:184 `decodePlotField` vs protocol.py:707 `_decode_field` | **VIOLATES (confirmed)** - see finding **V2**. The daemon is the looser side. |
| 8 | plots.js:203 `decodePlotSample` vs protocol.py:721 `decode_plot_sample` | **VIOLATES (confirmed)** - see finding **V3** (post-`*scale` finiteness). Every other clause (arity 4, `!ps` token, sid match, hex tick regex, tick bound, value count, per-field decode, bits/enum/analog routing) matches. |

### B. Browser vs daemon - other decoders

| # | Mirror pair | Verdict |
|---|-------------|---------|
| 9 | can.js:24 `parseCanEvent` vs protocol.py:376 `parse_can_event` | **complies** - diffed clause by clause: 5 parts + `!can` token; `isDecimalToken` tick + `> 0xFFFFFFFF`; flags `-` or `/^[xr]+$/` (matches `any(c not in "xr")`); id `^(0[xX])?[0-9a-fA-F]{1,16}$` (matches `parse_hex_int`'s optional prefix, non-empty, 16-digit cap, charset); **the id-vs-flags range check is present** at can.js:39 (the previously filed miss, now fixed); RTR single decimal digit + `> 8`; `-` payload; even-length hex + `<= 16` chars (matches `hex_to_bytes` odd-length refusal plus `len(data) > 8`). |
| 10 | state.js:168 `isDecimalToken` vs protocol.py:152 `is_decimal_token` | **complies** - ASCII charset (JS `\d`) plus the 20-digit cap, both present; the header comment at state.js:162-166 names the bound explicitly. Exported so four decoders share the one JS implementation. |
| 11 | state.js:160 `inTickRange` vs the `TICK_MS_MAX` checks in protocol.py | **complies** - `>= 0 && <= 0xFFFFFFFF`, plus a `Number.isFinite` clause the Python side gets from `int()`. |
| 12 | state.js:183-197 `computeTick` marker branch vs protocol.py:900 `parse_marker` | **complies** - `@([0-9]+)` (ASCII `\d` in JS, matching the `[0-9]` the Python comment insists on), the 20-digit cap via `isDecimalToken`, the 32-bit range via `inTickRange`, and non-empty text via the trailing `\s+\S`. The one grammar difference (JS accepts `!m\t@5 x`, Python's `partition(" ")` does not) is unreachable: the branch runs only for a row the daemon already classified `chan == "marker"`, which it does through `parse_marker`. |
| 13 | state.js:198-207 `computeTick` `!ps` branch | **exempt because** it is explicitly *not* a mirror: it delegates to plots.js's decoder through the `hooks.plotSampleTick` seam (state.js:199-205 records the two clauses a hand-written copy lost before this was made a delegation). One implementation, nothing to drift. |
| 14 | terminal.js:330-380 pane regex filter vs the daemon's `store._make_regexp` | **complies** - the `MAX_MATCH_LEN = 200` cap mirrors server.py:77 exactly. The daemon's `timeout=` has no JS equivalent, and terminal.js substitutes an equivalent containment (a wall-clock budget that drops the pattern, marks the box invalid and never re-runs that source), documented at terminal.js:333-339. This is a different implementation of the same intent, which the invariant allows when they cannot share code, and it is the stricter of the two in the failure direction (it refuses rather than freezes). |
| 15 | api.js:234 `GET /lines?match=^!pd ` | **exempt because** the pattern is fixed and executed only on the daemon; there is no client-side mirror of it. |

### C. Browser vs daemon - config and request-body bounds

The daemon is authoritative here and its refusal is surfaced in the dialog, so these are lower-severity than A/B - but each is a *bound omitted beside a check that was copied*, which is the exact shape the registry says to look for.

| # | Mirror pair | Verdict |
|---|-------------|---------|
| 16 | settings.js:382-385 `collectPorts` baud vs server.py:278 `ConfigPortEntry.baud` (`gt=0, le=MAX_BAUD`) | **VIOLATES (confirmed)** - see finding **V8**. Lower bound copied, `MAX_BAUD` omitted. |
| 17 | statusbar.js:413 `submitAttach` baud vs server.py:171 `PortAttach.baud` | **VIOLATES (confirmed)** - see finding **V8**. Same omission, second site. |
| 18 | settings.js:401 `saveServer` port vs server.py:246 `ConfigServerBody.port` (`ge=1, le=65535`) | **complies** - both bounds present and equal. |
| 19 | settings.js:419-421 retention_days vs server.py:251 (`ge=1, le=3650`) | **complies** - both bounds present and equal. |
| 20 | settings.js:423 capMb vs server.py:254 `max_db_bytes` (`ge=0, le=1<<42`) | **VIOLATES (confirmed)** - see finding **V9**. Lower bound copied, upper omitted. |
| 21 | settings.js:426-428 min_sessions vs server.py:255 (`ge=0, le=1000`) | **complies** - both bounds present and equal. |
| 22 | cmdbar.js:115 command timeout vs server.py:182 `CmdBody.timeout_ms` (`gt=0, le=MAX_TIMEOUT_MS`) | **VIOLATES (confirmed)** - see finding **V10**. |
| 23 | port alias (`statusbar.js:411`, `settings.js:372` - non-empty only) vs server.py:162 `_ALIAS_RE` | **exempt because** the client performs no grammar check at all, only a presence check. The looser side is not a check on the same thing, which is the invariant's own escape clause. |
| 24 | `db_path` (settings.js, unchecked) vs server.py:250 `max_length=1024` | **exempt because** no client-side check exists. |

### D. CLI vs daemon

| # | Mirror pair | Verdict |
|---|-------------|---------|
| 25 | cli.py:411-424 `_follow_match` engine vs store.py:340-383 `_make_regexp` | **complies** - the engine (`regex`, not stdlib `re`) and the per-call `timeout=` are both present, `FOLLOW_MATCH_TIMEOUT_S = 0.25` equals `store.MATCH_TIMEOUT_S = 0.25`, and the duplication of the constant is stated at cli.py:406-408 with its reason (not pulling the SQLite stack into the CLI). Both filed misses in this class (engine, then `timeout=`) are closed. |
| 26 | cli.py `--match` length vs server.py:77 `MAX_MATCH_LEN` | **exempt because** the length cap is not the containment: store.py:346 states outright that `MAX_MATCH_LEN` "does not help either, since 7 characters suffice", and the CLI carries the mechanism that *is* the containment (the timeout, site 25). The daemon additionally 400s the backfill leg of the same command. |
| 27 | cli_output.py:171 `math.isfinite` guard | **complies as written, but is a symptom** - it is a downstream hand-patch for V2 (see finding V2's note on three separate consumers each patching one missing origin check). |
| 28 | pjstream.py:147 `math.isfinite` filter | Same as 27: **complies as written, symptom of V2**. |

### E. Host vs firmware (firmware/monitor/*.c vs protocol.py)

| # | Mirror pair | Verdict |
|---|-------------|---------|
| 29 | monitor.c:269 `valid_plot_name` vs protocol.py:537 `_valid_plot_name` | **complies** - `[A-Za-z_][A-Za-z0-9_.]*`, length 1..16, both bounds present. |
| 30 | monitor.c:285 `valid_enum_label` vs protocol.py:488 `_LABEL_RE` | **complies** - `[A-Za-z0-9_.]{1,16}`, charset and both length bounds present. |
| 31 | monitor.c:302 `valid_plot_scale` vs protocol.py:499 `_PLOT_VALUE_RE` | **complies** - identical grammar. The overflow-to-Infinity clause (`parse_plot_value`'s `math.isfinite`) is deliberately absent and documented at monitor.c:300-301 as "caught host-side"; the host rejects the whole definition, so the two agree on the outcome. |
| 32 | monitor.c:331 `valid_enum_body` vs protocol.py:629 `_parse_enum_labels` | **complies** - `=` required, sign-based rejection on an unsigned type (`if (v < eq && *v == '-') { if (!signed_type) return false; }`, monitor.c:345-350 - the clause the *browser* mirror lost, see V1), the 20-digit bound at monitor.c:351, digits-only body, label validity. |
| 33 | monitor.c:366 `valid_bits_body` vs protocol.py:649 `_parse_bit_lanes` | **complies** - per-lane name validity, `named > 0`, `lanes <= 8*width`. |
| 34 | monitor.c:390 `valid_field_tail` vs protocol.py:659 `_parse_channel_spec` | **complies** - scale grammar, empty-unit rejection, the "at most three `:`-separated parts" rule, scale-on-enum/bits rejection, `t0 != 'f'` for enum (equals `_ENUM_TYPES`), `t0 == 'u'` for bits (equals `_BITS_TYPES`). |
| 35 | monitor.c:434 `parse_plot_body` vs protocol.py:594 `parse_plot_def` | **VIOLATES (confirmed)** - see finding **V4**. Every per-field clause was copied; the cross-field name-uniqueness clause was not. |
| 36 | monitor.c:642 `emit_can_event` vs protocol.py:342 `format_can_event` | **VIOLATES (confirmed)** - see finding **V5**. |
| 37 | monitor.c:626 `monitor_mark` vs protocol.py:880 `format_marker` | **VIOLATES (confirmed)** - see finding **V6**. |
| 38 | monitor.c:140 `mon_parse_hex_u32` vs protocol.py:120 `parse_hex_int` | **complies** - optional `0x`/`0X` prefix, non-empty, charset; the firmware bounds by 32-bit overflow where the host bounds at 16 digits and range-checks per caller. Firmware is the stricter side, which the invariant permits. |
| 39 | monitor.c:162 `mon_parse_dec_u32` vs protocol.py:152/173 `is_decimal_token`/`parse_seq_token` | **complies** - ASCII digits only, non-empty, bounded by 32-bit overflow (stricter than the host's 20-digit cap plus range check). |
| 40 | monitor.c:123 `mon_hex_decode` vs protocol.py:110 `hex_to_bytes` | **complies** - odd-length rejection, charset, plus a `max` bound the host applies per caller. |
| 41 | monitor.h:31 `MONITOR_LINE_MAX 255` vs protocol.py:35 `MAX_LINE_BYTES 255` | **complies** - equal, and monitor.c:439's `strlen(body) > MONITOR_LINE_MAX - 6` correctly reserves the exact `"!pd X "` prefix so a registered definition is always emittable. |
| 42 | monitor.c:552 `monitor_plot` sid check (`'0'..'9'`) vs protocol.py:605 (`len == 1 and in "0123456789"`) | **complies** - same set. |

### F. Host-internal (config loader vs API models)

Not literally "client vs daemon", but the same class, the same failure mode, and reached by the same sweep.

| # | Mirror pair | Verdict |
|---|-------------|---------|
| 43a | config.py:331 port `baud` (`_as_int(..., 1, _INT_MAX)`) vs server.py:278 `ConfigPortEntry.baud` (`le=MAX_BAUD`) | **VIOLATES (confirmed)** - see finding **V7**. |
| 43b | config.py:274-281 `retention_days`, `min_sessions`, `max_db_bytes` vs the same fields in `ConfigStorageBody` | **exempt because** the loader's loose upper bounds are deliberate and documented at config.py:267-278: falling back to a default would silently delete data the value was written to keep. The intent is stated, the divergence is one-directional, and the loader is the loose side by design. |
| 43c | config.py:37 `ALIAS_RE` vs server.py:162 `_ALIAS_RE` | **complies** - byte-identical pattern strings, and server.py:161 points at config.py for the rationale. It is a duplicated *literal* rather than a diverged check; worth noting only because nothing enforces that they stay equal. |

### Class 19 result

**43 sites. 10 violates, all CONFIRMED by probe, 0 suspected.**

---

## Findings

### V1 - `plots.js parseEnumLabels` accepts `-0` on an unsigned channel; the daemon and the firmware both reject it

- Site: `host/mcuscope/webui/plots.js:144` - `if (!signed && v < 0) return null;`
- Original: `host/mcuscope/protocol.py:642` - `if not signed and val_s.startswith("-"): return None`, with the comment "The sign, not the value: monitor.c rejects any '-' on an unsigned channel".
- Third implementation: `firmware/monitor/monitor.c:345-350`, which also tests the sign character.
- The mirror copied the character-set check (`/^-?\d+$/`) and the digit-count bound next to it (added last round), but converted the sign test into a *value* test. `Number("-0")` is `-0`, and `-0 < 0` is `false`, so the browser accepts a definition both other implementations refuse.
- Probe (`/tmp/claude-1000/review-r2/probe1.mjs` + python one-liner):

```
JS lanes for '-0=OFF' on unsigned u1: [ 'st' ]
parse_plot_def(-0 on u1): None
parse_plot_def(0 on u1) ok: True
```

- Consequence: exactly the failure mode the registry records for this function. `!pd 0 st:u1:=-0=OFF,1=ON` builds an enum lane in the browser and charts a stream the daemon dropped whole, so `/plot/series`, `/plot/channels`, `mcu plot` and `/plot/export` have never heard of it, and a page reload makes the lane vanish.
- Fix shape: test the token, not the number (`if (!signed && valStr.startsWith("-")) return null;`).

### V2 - `protocol._decode_field` has no finiteness check; its browser mirror does

- Site: `host/mcuscope/protocol.py:707-718` - the `is_float` branch returns `float(struct.unpack(">f", raw)[0])` unguarded.
- Mirror: `host/mcuscope/webui/plots.js:189-195` - `return Number.isFinite(f) ? f : null;`, with an eight-line comment explaining that `7F800000` is "an ordinary firmware divide-by-zero".
- The daemon is the looser side, and the check is real on the other side, so the invariant is not satisfied by "the looser side is not a check at all".
- Probes:

```
# /tmp/claude-1000/review-r2/probe_inf2.py (store + Starlette render)
stored: [{'line_id': 2, 'ts': 1000.0, 'tick_ms': 1000, 'value': inf}]
GET /plot/series RENDER FAILED: ValueError Out of range float values are not JSON compliant: inf
GET /plot/channels RENDER FAILED: ValueError Out of range float values are not JSON compliant: inf

# /tmp/claude-1000/review-r2/probe_inf4.py (live daemon over HTTP, via tests.support.Stack)
/plot/channels                 -> 200 {"channels":[{"name":"t",...
/plot/series?name=t            -> 200 {..."points":[{"line_id":6,...,"value":null}]}
/plot/export?names=t&format=long  -> 200  ts,tick_ms,sid,name,value
                                          1000.0,1000,0,t,inf
/plot/export?names=t&format=wide  -> 200  ts,tick_ms,t
                                          1000.0,1000,inf
```

- Consequence, three parts:
  1. The daemon persists `inf`/`nan` into `plot_points` where the browser would have dropped the sample.
  2. Its own two surfaces then disagree about the same stored point: the REST endpoints report `null` (pydantic's `ser_json_inf_nan` default rescues them - Starlette's raw `JSONResponse` would have raised, as the first probe shows), while `/plot/export` writes the literal token `inf` into the CSV.
  3. Three separate downstream consumers carry their own hand-patch for it (`cli_output.py:171`, `pjstream.py:147`, `plots.js mergeSeedSeries:301`), each added at a different time. The seed-path comment at plots.js:296-300 names this exact gap in the daemon.
- Fix shape: one clause in `_decode_field` (`if not math.isfinite(value): return None`), after which the three downstream patches become belt-and-braces rather than the only line of defence.

### V3 - `protocol.decode_plot_sample` does not re-check finiteness after applying `*scale`

- Site: `host/mcuscope/protocol.py:755-757` - `if chan.scale is not None: decoded *= chan.scale` then `points.append(...)` with no re-check.
- Mirror: `host/mcuscope/webui/plots.js:222-228` - re-checks at :227, with a comment saying the earlier decode-time check is not enough because a large scale factor can carry a finite sample to Infinity.
- Distinct from V2: this one bites integer channels too, which V2's `is_float` branch never touches.
- Probe:

```
d2 = parse_plot_def('!pd 0 t:u4*1e300')
decode_plot_sample('!ps 0 3E8 FFFFFFFF', d2) -> PlotSample(..., points=(('t', inf),))
```

- Consequence: identical to V2 (stored `inf`, `null` over REST, `inf` in the CSV export).
- Fix shape: the same `math.isfinite` clause after the multiply, matching plots.js:227 exactly. Note that fixing V2 alone does *not* fix V3.

### V4 - `monitor.c parse_plot_body` does not enforce name uniqueness; `parse_plot_def` rejects the whole definition for it

- Site: `firmware/monitor/monitor.c:434-503`. There is no `seen` set and no cross-field comparison anywhere in the function (`grep -n "dup\|uniq\|seen" firmware/monitor/*.c` returns one unrelated hit in `monitor_cmds.c:388`).
- Original: `host/mcuscope/protocol.py:608-622` - "SPEC 2.5: every emitted name in one definition is unique, channel names and bit lane names in one namespace", enforced over `[chan.name] + chan.lanes`.
- The mirror is faithful about every *per-field* clause (name grammar, type token, width, scale, unit slot, enum body, bits body) and drops the one clause that spans fields - the same "faithful about the neighbours of the missing clause" shape the registry records.
- Probe (`/tmp/claude-1000/review-r2/probe_fw.c`, `probe_fw2.c`, compiled against `firmware/tests/fake_shims.c`):

```
dup-name   monitor_plot -> 0 ; tx=!pd 0 dup:u2 dup:u2
                                !ps 0 3E8 0001,0002
lane-clash monitor_plot -> 0 ; tx=!pd 1 flags:u1 x:u1:/flags,b1
                                !ps 1 3E8 01,02
# host verdicts on the same two bodies
parse_plot_def dup:u2 dup:u2 -> None
parse_plot_def lane clash    -> None
```

- Both the plain duplicate and the lane/channel namespace collision register successfully on the target (return 0) and are emitted, and the host drops both definitions whole.
- Consequence: the exact scenario `parse_plot_body`'s own header comment (monitor.c:430-433) says the whole-grammar check exists to prevent - "a body the host refuses is registered forever and its samples land as generic events, with nothing visible on the target but a 0 return". The stream is silently undecodable for the life of the firmware image, with no error on either side.
- Fix shape: one pass over the collected names (channel names plus non-empty lane names) inside `parse_plot_body`, returning -1 on a repeat. `MON_PLOT_MAX_FIELDS` bounds the pass, so it is O(n^2) over a handful of entries.

### V5 - `monitor.c emit_can_event` has no id-vs-flags range check; `format_can_event` refuses the same frame

- Site: `firmware/monitor/monitor.c:642-671` - `o = emit_hex_u32(o, f->id);` with no bound on `f->id` against `f->ext`.
- Original: `host/mcuscope/protocol.py:346-355`, added specifically "so format and parse accept the same set", after the simulator's `can tx` echo emitted `!can ... 800 ...` for a standard frame and the daemon stored it as a generic event.
- The firmware is the other producer of the same line. It clamps `dlc` (`f->dlc > 8 ? 8 : f->dlc`, monitor.c:662 and :666) but not the id, so the *neighbouring* bound was copied and this one was not.
- Probe (`probe_fw.c`):

```
can 0x800 std tx=!can 0 - 800 AA
parse_can_event 800 std -> None
```

- Consequence: a CAN driver reporting an 11-bit-overflow id (or an extended frame with `ext` unset by a shim bug) emits a line the daemon keeps as a generic event with no `can_frames` row. The frame is invisible to `mcu can`, to `GET /can/frames` and to the web UI's CAN table, and no counter anywhere records it. `monitor_can_filter_pass` does not range-check either.
- Fix shape: `if (f->id > (f->ext ? 0x1FFFFFFFu : 0x7FFu)) return;` at the top of `emit_can_event`, mirroring protocol.py:350-355.

### V6 - `monitor.c monitor_mark` accepts text `format_marker` refuses

- Site: `firmware/monitor/monitor.c:626-638` - the only guard is `if (!text || !*text) return;`.
- Original: `host/mcuscope/protocol.py:880-897`, which refuses two more inputs, one of them explicitly because the alternative is "silent corruption rather than a failure".
- Two clauses missing:
  - **Tick sigil in the text.** On a port with no `tick_ms` callback, `monitor_mark("@1234 hello")` emits `!m @1234 hello`, which `parse_marker` reads back as `Marker(text='hello', tick_ms=1234)` - a tick nobody set. protocol.py:892-895 refuses exactly this: "Refusing is the only honest round trip."
  - **Whitespace-only text.** `monitor_mark("   ")` emits `!m    `, which `parse_marker` returns `None` for, so the line is stored as a generic event rather than a marker. protocol.py:888 uses `text.strip()`; the firmware uses `!*text`.
- Probe (`probe_fw2.c`, port with `.tick_ms = 0`):

```
mark sigil, no clock: tx=!m @1234 hello
mark blank:           tx=[!m    ]
parse_marker('!m @1234 hello') -> Marker(text='hello', tick_ms=1234)
```

- Consequence: a marker timestamped with a number lifted out of its own text, on precisely the ports that have no clock to timestamp it with; and a wasted line for whitespace-only text.
- Fix shape: skip leading spaces/tabs and reject an all-whitespace `text`; reject `text` whose first word matches `@[0-9]+`. Both are a few lines against the existing `is_dec_digit`/`skip_digits` helpers.

### V7 - `config.py` accepts a `baud` the write-back API refuses

- Site: `host/mcuscope/config.py:331` - `baud=_as_int(entry, "baud", PortConfig.baud, f"ports.{alias}", 1, _INT_MAX, ...)`.
- Original: `host/mcuscope/server.py:278` - `baud: int = Field(default=115200, gt=0, le=MAX_BAUD)` with `MAX_BAUD = 100_000_000`.
- Lower bound copied (and its rationale documented: `baud = true` became 1 baud), upper bound absent, with no counterpart to the deliberate-looseness note that covers `retention_days` and friends at config.py:267-278.
- Probe (`/tmp/claude-1000/review-r2/probe_cfg.py`):

```
loader accepted: baud= 999999999 retention= 100000
ConfigPortEntry REFUSED: Input should be less than or equal to 100000000
```

- Consequence: a hand-edited config file loads and the port attaches, but `PUT /config/ports` - which the settings dialog issues with the *whole* ports list, including entries the user never touched - is then refused 422 for a value already in the file. The user cannot save any port change until they find and fix an entry the daemon happily started with. The same round trip exists for `retention_days` (loader `_INT_MAX`, API `le=3650`, UI message "retention must be 1-3650 days"), though that side is documented as deliberate.
- Fix shape: bound `baud` at `MAX_BAUD` in the loader too, and hoist the constant so the two cannot drift.

### V8 - client-side baud check omits `MAX_BAUD` (two sites)

- Sites: `host/mcuscope/webui/settings.js:383` (`!Number.isFinite(baud) || baud < 1`) and `host/mcuscope/webui/statusbar.js:413` (`!Number.isFinite(baud) || baud <= 0`).
- Original: `server.py:171` `PortAttach.baud` and `server.py:278` `ConfigPortEntry.baud`, both `gt=0, le=MAX_BAUD`.
- Probe:

```
PortAttach(alias='p1', device='/dev/x', baud=200000000) -> REFUSED: Input should be less than or equal to 100000000
```

- Consequence: low. The daemon is authoritative and its 422 is rendered into the dialog's error element, so the user sees a refusal - just the daemon's wording rather than the dialog's, after a round trip. Recorded because it is the registry's stated pattern (a bound omitted while the check beside it was copied), and because settings.js:364-366 asserts "the daemon applies the same validation the config loader does", which V7 shows is itself not true.

### V9 - client-side size-cap check omits the upper bound

- Site: `host/mcuscope/webui/settings.js:423` - `if (!Number.isFinite(capMb) || capMb < 0)`.
- Original: `server.py:254` - `max_db_bytes: int = Field(default=0, ge=0, le=1 << 42)`.
- Probe: `ConfigStorageBody(..., max_db_bytes=(1<<42)+1) -> REFUSED: Input should be less than or equal to 4398046511104`.
- Consequence: low, as V8. Note the sibling fields in the same function (`retention_days`, `min_sessions`) both carry their upper bound, so this is a single omission in a set of three.

### V10 - client-side command timeout omits `MAX_TIMEOUT_MS`

- Site: `host/mcuscope/webui/cmdbar.js:115` - `if (!Number.isFinite(timeout) || timeout <= 0) { timeout = 1000; ... }`.
- Original: `server.py:182` - `timeout_ms: int = Field(default=1000, gt=0, le=MAX_TIMEOUT_MS)` with `MAX_TIMEOUT_MS = 300_000`.
- Probe: `CmdBody(port='p1', cmd='x', timeout_ms=400000) -> REFUSED: Input should be less than or equal to 300000`.
- Consequence: low, but slightly worse-shaped than V8/V9 because the client also arms `AbortSignal.timeout(timeout + 5000)` from the same unbounded value, so an over-large entry leaves the strip on "..." for the client-side abort window after the daemon has already refused.

---

## Notes for the round

- Class 6 is clean. All four gates the class exists for (parse, typed decode, post-scale, seed) are present, plus the two x-side gates added since the last round. Nothing further to do.
- Class 19's live instances cluster in two places the last round did not reach: the **firmware** side of the host/firmware pair (V4, V5, V6 - three producers, none of which mirrors the emit-side check its host counterpart carries), and the **finiteness** clause that the browser has and the daemon does not (V2, V3).
- V1 is a *regression in kind* for `parseEnumLabels`: last round's fix added the digit-count bound this function was missing, and the sign check sitting immediately below it is wrong in the same "copied the neighbour, not the clause" way.
- The four `_decode_field`-adjacent hand-patches (cli_output, pjstream, mergeSeedSeries, plus the `ser_json_inf_nan` rescue) are worth calling out as a pattern: when three consumers each grow the same guard, the guard belongs at the origin.
