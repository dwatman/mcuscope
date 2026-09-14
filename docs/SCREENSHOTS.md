# Reproducing the README screenshot

`docs/img/webui.png` is a live capture of the zero-hardware demo.
It needs refreshing whenever the UI changes visibly.
The obvious approach does not work, and the traps below cost three attempts.

> **Linux/X11 only.** The recipe uses `wmctrl` and ImageMagick's `import -window`, neither of which exists on Windows, and the config path it names is the XDG one.
> On Windows the equivalent is: run the same isolated-config demo, open the browser in kiosk mode, then capture the window with `Alt+PrtSc` or the Snipping Tool and resize to the same width.
> Everything in "Why not headless" and the numbered content notes still applies on either OS.

## Why not headless

`firefox --headless --screenshot` fires at the page load event and has no delay option.
The UI is a WebSocket app, so the shot comes out reading "connecting..." with zero lines and an empty CAN table.
There is no flag that fixes this; drive a real browser instead.

## Recipe

1. **Run the demo against an isolated config**, never your own.
   `mcuscoped --sim` with no `--config` picks up `~/.config/mcuscope/config.toml`, which on the owner's machine attaches a real `charger-board` port, and that port name then appears in a public image.
   Point `--config` at a throwaway TOML with its own `db_path` and no `[[ports]]`.

2. **Let it accumulate.** The analog window is 30 s, so capture before that and the trace is a sliver against the right edge. Half a minute of run time is enough.

3. **Open it in kiosk mode on the real display**, with a fresh profile so no session-restore bar appears:

   ```bash
   firefox --profile /tmp/ffshot --kiosk http://127.0.0.1:8799/ui/
   ```

   Kiosk gives a clean full-screen page with no browser chrome to crop out.

4. **Capture that window only**, never the root window, or every other window on the desktop lands in the image:

   ```bash
   W=$(wmctrl -l | awk '/MCUscope/ {print $1; exit}')
   wmctrl -i -a "$W"; sleep 2
   import -window "$W" shot.png
   ```

5. **Downscale and palette-reduce.** A 3200x1800 capture is ~600 KiB, and 256 colours costs nothing visually on a flat dark UI:

   ```bash
   convert shot.png -resize 1600x900 -colors 256 -strip docs/img/webui.png
   ```

   That is about a 5x saving (764 KiB to 156 KiB), which matters in a repo whose entire packed history is under 1 MiB.

## Composing the frame

The sidebar opens on "Both" (CAN and Plots), and the divider positions are not persisted, so drag them by hand if the frame needs it.
A fresh browser profile starts with one terminal pane.

`mcuscoped --sim` runs the simulator's `--demo` set: four CAN ids, one analog chart (typed `stream 0`) and the digital/enum panel, with no source edit.
