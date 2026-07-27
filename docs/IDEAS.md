# Ideas

Candidate features, ordered by usefulness for **testing hardware**: bring-up, regression runs, long soaks, chasing intermittent faults, and being driven by an AI agent that cannot see the bench.

Nothing here is committed work, and this file is subordinate to the two authoritative documents.
`docs/SPEC.md` section 10 and the Phase P2 backlog in `docs/IMPLEMENTATION_PLAN.md` already own several nearby features with fixed design intent (see "Already planned" below); an idea here only becomes work when the owner asks.

Tiers are by usefulness, not by order of implementation, though tier 1 is roughly the cheapest path to a usable test rig. Each entry carries an effort estimate (small / medium / large) and a value call (high / medium / speculative). Dependencies between entries are stated where they exist.

## Already planned: do not duplicate these

Four backlog items sit close enough to the ideas below that the boundary needs stating.

- **Flash and reset** (SPEC 10, top of the P2 backlog) is fully designed: `[tools]` command templates in config, `POST /flash` and `POST /reset` that pause the port, shell out and resume, CLI `mcu flash FILE` and `mcu reset`. Reproducible runs from a known state are therefore already the plan, not an idea. The port pause/resume interacts with the reconnect machinery, so it is not the trivial job it looks like.
- **DBC decoding** (SPEC 10) is scoped to decoded signal text stored alongside frames plus `mcu can dump --decode`. Feeding CAN signals into plot channels as trendable engineering units is a larger scope than that; if it is wanted, reconcile the two deliberately rather than carrying both descriptions.
- **pytest HIL fixtures** (SPEC 10) answer the same job as the scripted test runner below. Decide which is the product before building either: the cleanest split is the runner as the engine and the pytest plugin as a thin adapter over it.
- **MCP wrapper** (SPEC 6, P2 backlog) is the agent-native interface. Worth a deliberate decision rather than silence: the CLI plus `--json` already serves an agent well, so the case for MCP is discoverability and fewer subprocess round trips, not capability.

## Shared foundations

Three components that several entries below each need. Building any of them once, deliberately, is worth more than three private versions.

- **Daemon-initiated commands.** Today the daemon only relays a client's command; it never issues one on its own initiative. Pollers, firmware-identity stamping and any post-flash re-ping all need this. It is the real foundation of tier 1.
- **A notable-event classifier.** One shared definition of "notable" (error line, detected reset, limit or heartbeat violation, port reconnect, session boundary). `mcu since`, triggers, stats highlighting and session diff all want the same list.
- **A line-template masker.** Masking numbers and hex to reduce a line to its message shape. Needed by template clustering, session diff and the script recorder.

## Tier 1: the core test rig

Listed in ship order: derived channels produce data, the assert extensions judge it, the runner sequences it, and the last three keep a run diagnosable and labelled.

### Derived channels: pollers and regex scraping

One feature with two front ends, since both share the config schema, the name and unit handling, the parse step and the plot ingest.
*Active*: `mcu poll add 'adc read 0' --every 500ms --as vbus` has the daemon issue a command on a timer and feed the response into `plot_points` as a named channel.
*Passive*: a per-port rule such as `scrape = ['temp=(?P<temp_c>[\d.]+)']` extracts numeric capture groups from ordinary debug lines at ingest.
Today `plot_points` is fed only by decoding firmware-emitted `!p`/`!pd`/`!ps` frames, so a board whose firmware only answers queries or just prints values cannot be trended at all - which is most firmware, including the board currently on the bench. Needs daemon-initiated commands for the active half.

- Effort: medium. Value: high.

### Assert extensions: numeric limits and heartbeats

Two additions to the existing verdict primitive, sharing its exit-code contract.
`mcu assert --channel vbat --min 3.2 --max 4.3` gives a verdict over stored `plot_points`, reporting violation count and worst excursion with its timestamp: `/assert` today takes only expect/forbid regex lists, so "the rail never sagged below 3.2 V during the twelve-hour run" is not expressible.
`mcu assert --every RE --max-gap-ms N` fails if a matching line ever goes silent longer than N, reporting worst gap and jitter: expect/forbid can prove a line appeared, not that a 1 Hz status message never stopped or drifted, which is how soak failures actually present.
Two ordering constraints: the limits half is useless until derived channels exist to populate the channels, and the gap check in *live* mode does not fit the current assert engine (a live window closes once every expect matches, whereas gap watching needs a continuously evaluated timer), so ship the retrospective SQL scan first.

- Effort: small. Value: high.

### Scripted test runner with machine-readable verdicts

A file of steps (send, expect, forbid, wait, delay, assert) run by `mcu run test.toml`, emitting pass/fail plus JUnit XML, with each run becoming a named session so a failure is a queryable capture rather than lost scrollback.
The primitives already exist - `wait`, `assert`, sessions, the exit-code contract - this is the layer that turns them into a regression suite runnable before every flash and wireable into CI.
Include `--repeat N` / `--until-fail` with per-iteration session labels and a failure-rate summary from the start: chasing an intermittent means running the same script fifty times, and that loop is a few lines here versus a separate feature later.
See the note on the P2 pytest plugin above before starting.

- Effort: medium. Value: high.

### `mcu doctor`: link diagnosis for first contact

One command that distinguishes the failure layers an agent otherwise flails on: daemon unreachable, daemon up but no port attached, port attached but device node absent, device present but silent, device talking but emitting non-ASCII garbage (wrong baud - report observed byte statistics and suggest common rates), or protocol replies present but version-mismatched.
First contact with a new board is when a blind agent has the least context, and today it gets exit code 3 or an empty `mcu lines` with no way to tell which layer is broken.
Each verdict carries a one-line remediation hint.

- Effort: small. Value: high.

### Firmware identity stamped on sessions

On attach and on reconnect, the daemon issues `ping`/`info` and records the project name, protocol version and firmware-version token into the session metadata; `mcu session list` then shows which build each run was on.
This solves the classic bench failure mode of a week-old capture nobody can map back to a commit.
The pieces are there - `cmd_info` already emits `up=` plus a `mon_info_extra` hook, `ping` returns the project name, and the sessions table has a `note` column - so the only new machinery is daemon-initiated commands. Re-stamping after a *detected reset* additionally needs the tick model in tier 2, so leave that for v2.

- Effort: small. Value: high.

### Session pinning and capture-budget projection

`mcu session pin` marks a session immune to retention, and the daemon warns (sys row plus a `/status` field) when the current ingest rate projects the size cap or age window to start trimming a still-running session.
The size cap trims oldest live content, so a high-rate overnight soak can silently eat its own first hours - exactly the data a morning triage needs - and nobody finds out until a query comes back short.
Retention already has a `min_sessions` floor to hang the exemption logic on.

- Effort: small. Value: high.

## Tier 2: keeping an unattended run diagnosable and safe

Where a twelve-hour run, an unattended bench and a fault that fires once in fifty boots stop being tractable by eye.

### Tick-to-host time model and reset detection

The daemon continuously fits MCU tick against host arrival time from event lines it already receives, exposing offset and drift, and flags any tick regression as a sys row: "reset detected at 03:41:12 (tick went 88214321 -> 142)".
Unexpected watchdog resets during overnight runs currently have to be inferred by eye; this makes them a queryable, assertable event (`mcu assert --forbid 'reset detected'`) and lets pre- and post-reset data share one time base.
Prerequisite for boot checkpoints, and for the reset half of firmware identity.

- Effort: medium. Value: high.

### Reliability counters in `mcu stats`

Per-session reconnect count, disconnect durations, command latency percentiles, error-code histogram, dropped and malformed line counts.
Everything needed is already stored (cmd and resp rows carry seq and ts; drops and disconnects write sys rows), so this is a week of work, not a project, and it is what you actually want to see after a soak.
Two details: `rx_dropped` in the port status is a cumulative in-memory counter since attach, so *per-session* drop counts must come from the sys rows instead; and no reconnect counter exists in the port status dict at all today, which is a five-line addition to `host/mcuscope/serial_link.py` worth doing regardless of this entry.

- Effort: small. Value: high.

### `mcu snapshot`: one-shot diagnostic bundle

Freeze the current state of everything into one file: last N lines per channel, port and daemon status, latest value per plot channel, recent latest-per-id CAN, active config, firmware identity.
For a blind agent this is the "look at the bench" primitive and the state-side complement of `mcu since`; for a human it preserves the evidence when the board "just did the weird thing", instead of five separate queries whose windows drift apart.
Cheaper than it looks: `/plot/channels` already returns last value, tick, timestamp and count per channel, `/status` covers daemon and ports, and `GET /config` exists. Only latest-per-id CAN needs a new (small) GROUP BY query.

- Effort: small. Value: high.

### `mcu since`: pull-based attention digest

One command returning everything notable since the caller's last cursor: new error lines, detected resets, limit or heartbeat violations, session starts and stops, port reconnects, as one compact JSON delta.
An AI agent cannot keep a live tail open while it compiles or thinks; this is its cheap "did anything happen while I was away?" primitive, with an explicit cursor so nothing is missed or double-reported.
Half the plumbing exists - `/lines` and `/can/frames` already take `since_id`/`since_ts` - so the new work is the notable-event classifier. Ship with the events that exist today (error lines, reconnects, session boundaries) and add resets and violations as those land.

- Effort: medium. Value: high.

### Triggers: capture around an event

A daemon-side rule: when a line matches `/FAULT|ERR/` or a channel crosses a limit, insert a marker, snapshot the surrounding window, and optionally POST to a webhook.
For an overnight soak this is the difference between "it failed at some point" and a bookmarked window waiting in the morning.
The push dual of `mcu since`, over the same classifier; markers ride existing machinery (`POST /marker` and the `marker` channel are implemented).
Deliberately *not* "stop the session on trigger": sessions cannot overlap or nest, so stopping mid-soak leaves the rest of the capture unlabelled. Marker plus session pinning is the right reflex.

- Effort: medium. Value: high.

### Safety interlocks: guard rules that act

Daemon-side rules with a bounded-latency local action: when a channel crosses a limit or a line matches, run a configured command (`gpio set psu_en 0`, a PSU output-off shell-out, a probe reset) and log a sys row saying what tripped and what was done.
Triggers notify and bookmark; this protects the hardware, which matters precisely because the primary operator may be an AI agent that is mid-compile, rate-limited, or simply wrong when a current rail runs away on an unattended bench.
Reuses the derived-channel machinery for the condition and SPEC 10's `[tools]` command templates for the action.

- Effort: medium. Value: high.

### Capture replay: a stored session as a virtual port

Feed a previous session's stored lines back through the ingest path, optionally time-compressed, as a virtual port, so scrape rules, checkpoint regexes, triggers, stats and runner scripts can be developed and debugged against a real capture with no board and no live run.
This also buys retroactive scraping: derived channels only extract at ingest, so a rule written after a twelve-hour soak cannot plot that soak's data - replay closes the gap without repeating the soak.
The simulator already proves the daemon runs against a sourceless port; this is the same trick with recorded rather than synthetic data.

- Effort: medium. Value: high.

### Template clustering in `mcu stats`

Mask numbers and hex in captured lines to cluster them into message shapes, then report distinct templates with counts, first and last occurrence, and rate-over-time buckets, highlighting templates new in this session.
This is the triage primitive an agent needs on a million-line soak capture: fifteen template rows instead of paging `mcu lines`.
Host-side only, no new dependency (a drain-style masker is around a hundred lines), and it is the shared masker that session diff and the script recorder also need.

- Effort: medium. Value: high.

## Tier 3: deeper analysis and specific bench shapes

### Boot checkpoint timeline

Named checkpoints in config (a regex per milestone: clocks up, sensors probed, app loop entered).
Each boot the daemon records time-to-checkpoint and whether the sequence completed, with `mcu boot list` showing per-boot durations and incomplete boots.
Intermittent bring-up failures ("hangs at sensor init one boot in fifty") become a countable table instead of a scrollback hunt, and an agent can bisect on it. Needs reset detection to know where a boot begins.

- Effort: medium. Value: high.

### Session diff: golden-run compare

`mcu session diff GOOD BAD` compares two sessions: message shapes present in one but not the other, error-code count deltas, per-channel line-rate changes, and timing shifts of matched milestone lines.
This answers the most common regression question, "what changed since the last good run", without a human paging through two captures.
Sits on the existing session id ranges and SQLite queries, and shares the template masker.

- Effort: medium. Value: high.

### Raw instrument ports for correlated capture

An attach mode for any line-emitting serial or TCP source (bench PSU, DMM in logging mode, a second logger) that ingests into the same timestamped capture under its own channel label.
Real faults correlate across instruments ("brownout on the supply log 40 ms before the MCU reset"), and today that correlation is done by hand across two tools' clocks; combined with scrape rules, a PSU current readout becomes a plot channel beside the firmware's own signals.
Smaller than it sounds, because most of it already works: attach performs no handshake and never sends anything unsolicited, and `classify` already stores non-protocol lines as `debug`. The real gaps are narrow - an instrument line that happens to start with `>`, `<` or `!` is misclassified into cmd/resp/event, and nothing stops a client sending commands to an instrument port.

- Effort: small. Value: high, if there is a second instrument on the bench.

### `gpio watch`: pin-change events into digital traces

A monitor command that arms change notification on a named pin, emitting `!gpio <tick> <name> <0|1>` from main-loop polling, ingested into the existing digital/enum panel.
Watching a fault pin, interrupt line or handshake signal is a constant bring-up need that otherwise takes a logic analyser, and SPEC 2.5 already reserves `!gpio` for exactly this.
Edge resolution is bounded by the poll rate: fine for handshakes and fault flags, not for bus decoding.

- Effort: medium. Value: high, with that caveat.

### Command provenance on tx rows

Tag every stored tx line with its origin: web UI, CLI, poller, runner script, trigger action, or agent via an optional client identity header.
When a human and an agent share a bench, or pollers issue commands in the background, the capture cannot currently answer "who sent the command right before the board reset" - the first question in any shared-bench post-mortem.
One nullable column plus a header. Cheap now, and it becomes near-mandatory the moment pollers, triggers or interlocks start issuing commands of their own.

- Effort: small. Value: medium.

### Cross-port propagation asserts

For two-board benches, measure and assert latency from a matching line on port A to a matching line on port B: `mcu assert --from-port a --match TX_RE --to-port b --match RX_RE --max-ms 50`, reported as a latency distribution over the window rather than bare pass/fail.
Multi-port capture already exists; this turns it into a comms-path test primitive for CAN, RS-485 or radio bring-up.
Know the accuracy bound: timestamps are host arrival times stamped per read burst, so anything below a few milliseconds is noise. Right tool for the 50 ms example, wrong tool for tight timing.

- Effort: medium. Value: medium.

### Monitor conformance self-test for new firmware ports

`mcu conformance` drives the attached target through the protocol contract: ping/info shape, every error path in the code table, maximum-length lines, seq wrap, malformed-input tolerance, event formats.
The monitor module is meant to be ported to arbitrary MCUs via hand-written shims, and today the only check of a new port is ad-hoc poking - the gcc tests in `firmware/tests/` validate the reference code, not somebody's integration on real silicon.
Turns "did I wire the shims up correctly" into a one-command verdict at the start of every new-board bring-up.

- Effort: medium. Value: medium.

### Session-to-script recorder

Record the commands issued during an interactive bring-up session (already stored as tx rows) and emit them as a replayable step file with relative timing.
Reproducing an intermittent fault usually means "do exactly what I did for the last twenty minutes, fifty more times", and this generates that script instead of asking anyone to write it; it feeds the scripted runner, but the recorder is the new part.
The flaw to design around: observed responses cannot become verbatim expectations, since they carry ticks, counters and measurements that differ every run. It needs the template masker to generalise them, which makes that a hard dependency and puts this behind template clustering.

- Effort: medium. Value: medium.

## Tier 4: speculative

### Self-contained HTML run report

`mcu report --session S -o report.html` renders a single-file artifact: plot thumbnails, error and reset counts, assert verdicts, checkpoint timings, markers, firmware identity.
Soak results today live inside a SQLite file only MCUscope can read; this makes a run shareable with a colleague or attachable to an issue with no tooling on their side.
The stretch is the charting: staying dependency-free means generating inline SVG rather than pulling in a plotting library.

- Effort: medium. Value: speculative.

### Visual evidence hook

An optional per-bench capture command (an ffmpeg one-liner, `libcamera-still`, anything) that the daemon shells out to on a trigger or a marker, storing the image path as a sys row tied to the capture timeline.
The one thing this stack cannot give an agent or a remote colleague is the state of the physical bench - LED colours, a display's contents, smoke - and "photograph the bench when the fault fires" is how overnight intermittents actually get diagnosed.
Deliberately a configured shell-out, like flash and reset, to stay dependency-free and cross-platform.

- Effort: small. Value: speculative.
