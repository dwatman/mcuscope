# Configuring MCUscope

MCUscope runs with no configuration.
Read this page to change a default, keep a port across restarts, run a second daemon, or reach the daemon from another machine.

## The config file

The file is optional: without it the daemon uses its defaults and starts with no ports attached.
The settings page in the web UI creates and edits it, and keeps any comments you wrote by hand.

`mcu config path` prints where it is:

- **Linux**: `~/.config/mcuscope/config.toml`
- **Windows**: `%LOCALAPPDATA%\mcuscope\mcuscope\config.toml`

[`config.example.toml`](../host/contrib/config.example.toml) lists every key with its default and a comment.
Copy from it what you need.
A key the daemon does not know is reported at startup and otherwise ignored.

To use a different file, start the daemon with `mcu daemon start --config PATH`, or set `MCUSCOPED_CONFIG`.

## Common changes

### Reattach a board at every start

Tick **Save to config** in the web UI's attach dialog, or add the port by hand:

```toml
[[ports]]
alias = "board"
device = "/dev/ttyACM0"      # or COM7, /dev/serial/by-id/usb-..., socket://127.0.0.1:9900
baud = 115200
```

- Use `serial_number = "066BFF3..."` in place of `device` to find the board by its USB serial number. That stays valid when the device name changes.
- Set `identify = false` for firmware without the monitor module. It skips the `ping` the daemon sends on connect.
- Set `eol = "crlf"` (or `"none"`) if the firmware's console wants a line ending other than LF on what you send.

### Keep captures longer, or cap the disk they use

The capture is one SQLite file, `capture.db`:

- **Linux**: `~/.local/share/mcuscope/capture.db`
- **Windows**: `%LOCALAPPDATA%\mcuscope\mcuscope\capture.db`

```toml
[storage]
db_path = ""              # empty: the default location above
retention_days = 10       # lines older than this are deleted
min_sessions = 5          # the newest 5 ended sessions are kept however old they are
auto_session = true       # record a session for each daemon run
max_db_bytes = 0          # 0: no size cap
```

- With `max_db_bytes` set, the oldest lines are trimmed once the capture passes that size.
- A relative `db_path` is resolved against the config file's directory.
- `mcu status` prints the database path in use.

### Turn off the update check

Once a day at most, the daemon asks PyPI whether a newer release exists.
It shows the answer in `mcu status` and the web UI.
With the check off, the daemon makes no outbound request.

```toml
[update]
check = false
```

`MCUSCOPE_UPDATE_CHECK=0` in the daemon's environment does the same with no config file.

### Stream to PlotJuggler by default

```toml
[plotjuggler]
enabled = true
dest = "127.0.0.1:9870"
```

`mcu pj on --save` writes the same thing. See [PlotJuggler](USAGE.md#plotjuggler).

## Daemon flags and environment variables

| `mcuscoped` flag | Effect |
|---|---|
| `-c`, `--config PATH` | use this config file |
| `--host ADDR`, `--port PORT` | override `[server]` for this run |
| `--token TOKEN` | require this access token from other machines (see [LAN access](#lan-access)) |
| `--sim` | attach the built-in simulator as port `sim` |
| `--plotjuggler [HOST:PORT]`, `--pj` | stream plot points to PlotJuggler for this run |
| `--open` | open the web UI in the browser once the server is up |
| `--ignore-capture-lock` | start even if another daemon appears to own the capture |

`mcu daemon start` takes only `--config`, `--sim`, `--open` and `--timeout`.
For the rest, set `[server] host` and `port` and `[plotjuggler] enabled` in the config file, and the token in `MCUSCOPED_TOKEN`.

| Variable | Read by | Effect |
|---|---|---|
| `MCUSCOPED_CONFIG` | daemon and `mcu` | config file path |
| `MCUSCOPED_TOKEN` | daemon | access token to require |
| `MCUSCOPE_URL` | `mcu` | address of the daemon to talk to |
| `MCUSCOPE_TOKEN` | `mcu` | access token to present |
| `MCUSCOPE_UPDATE_CHECK` | daemon | `0` or `1`, overrides `[update] check` |
| `MCUSCOPE_START_TIMEOUT` | `mcu` | seconds `mcu daemon start` waits for the daemon (default 20) |
| `MCUSCOPE_CONFIG_DIR`, `MCUSCOPE_DATA_DIR`, `MCUSCOPE_CACHE_DIR` | both | replace the default config, data and cache directories |

## How mcu finds the daemon

Every `mcu` command, `mcu daemon start` included, uses the first of these that is set:

1. `--url`.
2. `MCUSCOPE_URL`.
3. The `[server]` host and port in the config file (`MCUSCOPED_CONFIG`, else the default file).
4. `http://127.0.0.1:8558`.

## More than one daemon

Running several daemons at once is supported: one per bench, each with its own config file, port number and capture.

```toml
# bench2.toml
[server]
port = 8559
[storage]
db_path = "bench2.db"
```

```bash
mcu daemon start --config bench2.toml
mcu --url http://127.0.0.1:8559 status
```

Two daemons cannot share one capture database.
`mcuscoped` locks the file at startup and refuses to start if another daemon holds it, naming that daemon's pid.
The operating system holds the lock, so a crashed daemon leaves nothing to clean up.
`--ignore-capture-lock` is only for a filesystem without working file locks.

## Start at login (Linux)

The repository has a systemd user unit, [`mcuscoped.service`](../host/contrib/mcuscoped.service).
It expects `mcuscoped` in `~/.local/bin`, where uv and pipx put it by default.
Download it from the link above, then:

```bash
mkdir -p ~/.config/systemd/user
cp mcuscoped.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now mcuscoped
```

## LAN access

By default the daemon listens on `127.0.0.1` only, so nothing outside your machine can reach it.
To use it from another machine, bind every interface and set an access token:

```toml
[server]
host = "0.0.0.0"
```

`mcuscoped --host 0.0.0.0` does the same for one run.

The token is never stored in the config file, so nothing that edits the config can change it.
The daemon reads `MCUSCOPED_TOKEN` and the client reads `MCUSCOPE_TOKEN`: the names differ by one letter.
Give it to the daemon at start, preferably in the environment, where it does not show in the process list:

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"   # generate one
MCUSCOPED_TOKEN=that-token mcu daemon start                    # preferred: the daemon inherits it
mcuscoped --token that-token                                   # foreground daemon, token in the process list
```

PowerShell sets the variable as its own statement:

```powershell
$env:MCUSCOPED_TOKEN = 'that-token'; mcu daemon start
```

With a token set:

- Clients on other machines must present it: `mcu --token ...` or `MCUSCOPE_TOKEN`. The web UI asks for it and remembers it.
- Clients on the daemon's own machine never need it.
- Wrong tokens are rate limited per client address.

Without a token, a daemon bound to the network serves its API to anyone who can reach it, with no authentication.
It prints a warning at startup, and the config can then be edited only from the daemon's own machine.

## Trust boundary

The daemon is built for a single-user machine.
On a host shared with other accounts, every local account has full control of it, including which devices it opens, and can bind port 8558 first to stand in for it.
