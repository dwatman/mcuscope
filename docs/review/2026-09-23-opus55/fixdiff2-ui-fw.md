# Fix-diff leg 2: web UI and firmware

HEAD: `b889e2b8fb848866054da6ba09cd2e651d9f69c7` (diff `b994076..4462986`).

Scratch lives under `~/tt-data/mcuscope-2026-09-24/fixdiff2/ui-fw/`:

- Mutation copy: `copy/`. Pre-fix firmware: `before/`.
- Chromium drive: `drive.py`. Run-alone script: `alone.py`.
- C driver: `copy/firmware/tests/drv.c`. Fuzz: `evh.c` plus `fuzz3.py`, reused from fixbatch-firmware.

Other reviewers were editing `cli.py` in the working tree during this leg, so its line numbers below are from `git show HEAD:`.

## Findings

### U-1 (low): an over-long `!p` still goes out as a bare `!p <tick>`, which the AI guide and INTEGRATION say cannot happen
- Where:
  - `firmware/monitor/monitor.c:367` and `host/mcuscope/sim.py:877` skip only a marker's `@tick`.
  - `cli.py:2982-2984` (HEAD) tells agents "with nothing left past its header, the notice arrives alone".
  - `INTEGRATION.md:455`, in the `!p` paragraph: "If nothing would be left past the type, only the notice goes out".
- Defect: a `!p` line cut back to its tick is still sent. SPEC 2.3's reason for the new rule, "a bare header decodes as nothing", applies to it too: `protocol.parse_plot_adhoc("!p 5")` returns None.
- Scenario:
  - The app calls `monitor_eventf("p %lu v=%f", tick, 1e300)` on standard newlib, where `%f` prints about 300 digits.
  - The wire carries `!p 5` followed by `!e event p overflow`.
  - `!p 5` is stored as a generic event row, which the guide told the agent would not appear.
- Confirmed:
  - Driven in `drv.c` under ASan: the output is `!p 5\n!e event p overflow`.
  - `sim._cut_event("!p 5 v=" + "1"*300)` returns `['!p 5', '!e event p overflow']`.
  - `test_sim_error_codes.py:84-87` pins the tick as kept ("a `!p` keeps its tick"), so the code does it on purpose. The docs are what disagree.
- Fix (owner should pick):
  - (a) Also treat a `p` type's decimal tick as header, in `event_end` and `_cut_event`, with SPEC 2.3 saying "for `m` its `@<tick>`, for `p` its tick".
  - (b) Keep the code, and reword `cli.py` and `INTEGRATION.md:455` to say "a `!p` keeps its tick line".

### U-2 (low): a divider that paging has filled stays in the pane, now between contiguous rows
- Where: `pane.js:176` (`historyIdTo` pages past a leading divider) and `terminal.js:546` (the page is prepended ahead of the divider, which stays).
- Defect: after the page lands, the divider still says rows are missing at a place where the pane now shows every row. SPEC 9.1:1627 says paging "fills the hole the divider names", yet the divider remains.
- Scenario:
  - A paused pane filtered to a rare channel is rebuilt after a reconnect, so its oldest row is `gap: 3000 lines not loaded`.
  - Scrolling to the top loads ids 790-989 above that divider.
  - The pane now reads row 989, then "3000 lines not loaded", then row 990: an outright false statement. The shed variant ("40 lines shed by the live stream") is misleading in the same way.
  - Before this round the walk stopped at the divider, so the divider was true.
- Confirmed: the batch's own test asserts this state. `terminal_history.test.mjs:267` has `pane.rows[HISTORY_PAGE].chan === "gap"` sitting between page ids 790-989 and row 990. The batch report's Doubts note it as well.
- Fix: in `loadHistoryPage`, drop the leading `chan === "gap"` rows before `pane.rows.unshift(...step.rows)`.
  - The page starts at `oldest.id - 1`, so it covers exactly the hole directly below the oldest line.
  - A further hole is marked by `planHistoryPage`'s own divider.

### U-3 (low): the "type is exactly `m`" guard has no test in C or in the sim
- Where: `monitor.c:367` (`i == 2`) and `sim.py:877` (`first == "m"`).
- Defect: an event whose type starts with `m` ("mode", "motor") must keep a leading `@5` token as text, and no suite pins this.
- Scenario: a later edit that drops `i == 2`, or writes `first.startswith("m")`, makes `monitor_eventf("mode @5 <long>")` send only its notice and lose the line. Both suites stay green.
- Confirmed by mutation in the copy:
  - `if (g_out[1] == 'm')` survives `make run`.
  - `first.startswith("m")` survives `test_sim_error_codes.py` and `test_sim.py` (79 passed).
  - Positive control: the `@\S*` mutant fails `test_sim_error_codes.py` in the same copy.
  - The fuzz (`fuzz3.py`, whose heads include `mm @5`) would catch it, but the fuzz is not in the suite.
- Fix: add `!mode @7 ` + 300 x `y` to `test_event_overflow_cut` and `test_a_cut_that_keeps_only_the_header_sends_only_the_notice`, expecting `!mode @7` and the notice.

### U-4 (nit): staging fold notices are never merged, and the trim comment overstates what stays
- Where: `api.js:732-735`.
- Defect:
  - Every trim keeps its earlier `{gap}` notices, so after k trims only `BUFFER_MAX - k` lines stay, not `BUFFER_MAX` as the comment says.
  - At k >= BUFFER_MAX (about 2.5M rows staged) the final run is never flushed. It is dropped with only the `console.warn`, and every later staged row runs an O(k) trim.
- Scenario: a first backfill that stalls for minutes (`api()` has no timeout) behind a 10k lines/s link.
- Confirmed: reasoned from the loop arithmetic (L = k + lines, excess = L - BUFFER_MAX).
- Fix: when the previous kept row is a fold notice, add to its `gap` instead of pushing a new one (one line). This removes the growth, the silent endpoint and the `ponytail:` note.

### U-5 (nit): `decimateColumns`' window defaults are test-only, and they bring back the fixed bug
- Where: `timewindow.js:139` (`xmin = xs[lo], xmax = xs[hi - 1]`).
- Defect: the only production caller passes the window. The defaults exist for six test calls, and they are exactly the slice sizing that P-2 removed.
- Scenario: a future caller (a digital or export decimation) omits the window and silently gets coarse columns after a silence.
- Fix: make both parameters required, and pass `xs[0], xs.at(-1)` in the six test calls.

### U-6 (nit): `mcu ai-guide` does not name the new `!e can bus <n> dropped`
- Where: `cli.py:2980-2984` (HEAD), the `^!e` entry. SPEC 2.5:321 lists the notice, and the guide does not.
- Fix: add one line beside the overflow example. `cli.py` is outside this slice; this is for the CLI owner.

### U-7 (nit, pre-existing): `i2c scan`'s budget is one byte short of the read budget
- Where: `monitor_cmds.c:267` sizes the list as `resp_max = MON_OK_PAYLOAD_MAX`, a size that includes the NUL. `read_into_resp` (`:90`, new) uses `MON_OK_PAYLOAD_MAX + 1`.
- Defect: two readings of one budget, which is class 48's "one budget, one check".
- Scenario: a shorted bus at seq 65535 lists 81 addresses (252 bytes). 82 addresses (exactly 255 bytes) would fit.
- Confirmed: arithmetic, and `test_i2c_scan_bus_shorted` pins 0x08..0x58 (81 addresses).
- Fix: clamp the scan to `MON_OK_PAYLOAD_MAX + 1`, and extend the pinned list to 0x59.

## Needs a human in a browser

- Firefox, window under 860 px, sidebar hidden at a wide width: the charts and the CAN table draw. Widening past 860 px shows the reopen tab.
- Firefox: 4 session exports in flight, then a 5th from Settings. It saves SQLite, not JSON.
  - Firefox's `network.http.response.timeout` is 300 s, so a queued and building export that sends no headers for 5 min may fail.
- Firefox: navigate the UI tab away, upgrade and restart the daemon, press Back: the "daemon updated: reload" badge shows.
- Safari (if supported): the UI loads at all. The `\p{...}` and `\P{Emoji}` regex in `state.js` breaks module load where it is unsupported.

## Checked, nothing found

- Every new or changed JS test file, run singly with `node --test`: 24 files, all green.
  - Every test in them run alone with `--test-name-pattern`: 142 runs, each 1 pass (`alone.py`).
  - `module_load_order`, `api_ws_gap` and `smoke` are green.
- Staging trim (`api.js`):
  - A fold notice lands ahead of the next kept row, including across a capture token (the old capture's notice is zeroed by `armStaging`).
  - The `markShed` cap never undercounts.
  - The drain treats notices as segment breaks. A pane clear cut still hides the divider for rows covered by the clear.
  - No new per-row work: `isShedNotice` runs only in the trim.
- Visibility gates (`plots.js:1441`, `can.js:588`, `app.js:199`): timer and visibilitychange only, never per row.
  - Chromium drive, all without page errors:
    - 800 px with a saved hide: sidebar 800 px, 1 uPlot, CAN moving.
    - 1400 px hidden: 0 px, 0 uPlots, CAN idle.
    - 1400 px shown: 360 px, 1 uPlot, CAN moving.
- Decimation over the drawn window:
  - The `winMin`/`winMax` passed match `windowFor`/`timeWindow`.
  - Chromium, a real page fed by `plotIngest`: 1 kHz, 600 s of silence, then 30 s in a 30 s window on 342 px. Result: 692 points, max gap inside the window 0.79 px.
- `fitDrawSpan` and `Y_INCRS`:
  - uPlot registers custom incrs in its `fixedDec` map only when they are new.
  - Its own -32..31 steps are built with the same `+\`${m}e${e}\`` literal, so the decimal counts for normal ranges are unchanged, and steps past 1e32 are integers, where `roundDec` is a no-op.
  - Class 6: no non-finite value reaches the arrays, and the null label only affects the axis.
- `adhocOnce` and `canOnce` (class 76): both parsers are pure functions of `raw`. Their results are read only: `canIngest` copies primitives, and `routePoints` builds new arrays.
- `userText`:
  - Chromium: the session name `run<U+2060>x ❤️ <U+3164>end` renders escaped, with the emoji kept and bidi-isolated.
  - A ZWJ inside an emoji sequence is escaped, which follows the ruling.
- Version stamp: Chromium shows the meta as `0.5.0` against `/status` `0.5.0`, with the badge hidden. `_stamped_index` replaces only index.html.
- `wait=1`: the server declares `wait` on `/sessions/{ref}/export` and `/bundle`, and SPEC 3.4:866 matches. Only the session `.db` navigation adds it.
- `#cmdPort` title matches `cmdbar.js` `autoAlias`/`submitCmd`.
- Firmware C suites: `make run`, `asan`, `families` and `families-asan` are green (286/286, 18/18).
- Strict ARM matrix (arm-none-eabi-gcc 13.3), 256 compiles, 0 failures. Positive control: a narrowing `unsigned char q = v` errors.
  - Flags: `-Os -Wall -Wextra -Wformat=2 -Wconversion -Wsign-conversion -Wshadow -Wcast-align -pedantic -Werror`.
  - Matrix: 32 `MON_NO_*` subsets x `MON_CAN_BUSES` 1/9 x M0+/M4F x both files.
- Footprint, re-measured on HEAD, matches SPEC 5.1 and INTEGRATION:
  - Objects, M0+ -Os: `monitor.o` 4283/989 to 4461/990, `monitor_cmds.o` 2264 to 2246.
  - Linked (`fp/measure.sh`):
    - core: M0+ 4602, M4F 4616, M0+ -O2 6250.
    - plot: M0+ 6836, M4F 6884, M0+ -O2 9482.
    - full: nano 9672/1492 (M0+) and 9616 (M4F); std newlib 38236 and 31373.
    - Family savings: 1140, 476, 108, 152 and 148 B. Two buses: +12 B RAM.
- C against sim cut: 40 000 fresh lines (seeds 11 and 12), 0 mismatches under ASan. Positive control: the pre-fix C gives 1210 mismatches.
- CAN undeclared-bus notice:
  - Driven: bus 3 on a 2-bus build gives `!e can bus 3 dropped`, then the bus-1 frame.
  - Latch, init re-arm and the bus number are each mutation-killed.
  - `g_out` is not held across `drain_can`. No host parser treats `!e can` specially, and the web UI's `!can` prefix test does not match it.
- `read_into_resp`:
  - `2n < min(resp_max, MON_OK_PAYLOAD_MAX + 1)` leaves room for the digits plus the NUL.
  - The wire limits (i2c 64, spi 120 bytes) stay under it.
- `mon_buf_init(size 0)`: every appender call checks `p < end` first, so nothing is written. `i2c scan` with 0 breaks before writing.
- Sim filter against firmware `monitor_can_filter_pass` and SPEC 2.4: both kinds, `x`, plain, `all`/`none` agree. `protocol.py` is untouched.
- SPEC 2.3, 2.5, 5.1, 9.1 and 9.2 hunks match the code, except 9.1:1627 (U-2) and the `!p` wording (U-1).
- The daemon (port 8741, own config, db and `MCUSCOPE_*_DIR`) was killed by PID. Playwright used ephemeral contexts, so no profile dirs were left.
