# CLAUDE_SNIPPET.md

Paste the block below into your `~/.claude/CLAUDE.md` (or a project `CLAUDE.md`) so
Claude Code knows the hardware debug bridge is available and how to drive it.

```markdown
## Hardware debug bridge (mcu-interface)

A local serial-to-hardware bridge is available via the `mcu` CLI (daemon `hwbridged` on
127.0.0.1:8765). Use it to talk to the attached MCU.

- Check it first: `mcu status` (exit 3 means the daemon is not running).
- Learn the full interface on demand: `mcu ai-guide`.
- Typical loop: `mcu cmd '<command>'` to send and get the response, `mcu wait <regex>`
  to send-and-wait for an async line, `mcu lines`/`mcu tail` to query the capture.
- Always pass `--json` for machine-readable output. Exit codes: 0 ok/match, 1 error,
  2 timeout, 3 daemon unreachable.
```
