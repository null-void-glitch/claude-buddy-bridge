# claude-buddy-bridge

Lets a [claude-desktop-buddy-cardputer](https://github.com/y88huang/claude-desktop-buddy-cardputer)
device drive **Claude Code CLI** sessions instead of (or alongside) the
Claude Desktop app, over the same Bluetooth LE (Nordic UART Service)
protocol the device uses to talk to Claude Desktop.

USB serial was tried first since the firmware's source reads/writes the
same JSON protocol over `Serial` too — but on the actual hardware this
was tested against, `Serial` never produced or consumed a single byte
over USB (confirmed with a triggered debug print that never reached the
host), so this bridge uses BLE, the transport known to work.

## How it works

- `bridge/daemon.py` is a small always-on process that connects to the
  Cardputer over BLE and tracks session state (counts, recent activity,
  token usage, pending permission prompts), sending a heartbeat JSON
  packet to the device every ~10s or on change.
- Claude Code hooks (`PreToolUse`, `PostToolUse`, `SessionStart`,
  `SessionEnd`) talk to the daemon over a local Unix socket
  (`~/.claude-buddy-bridge.sock`) to report activity and, for
  `PreToolUse`, block until you press Y/N on the device.
- If the daemon isn't running, every hook fails open — Claude Code's
  normal permission prompt and behavior are unaffected.

## Setup

```bash
sudo pacman -S python-bleak   # or: pip install -r requirements.txt in a venv
```

### 1. Pair the device (one-time, via `bluetoothctl`)

The Nordic UART Service characteristics require an encrypted link, so the
device needs to be bonded at the OS level before this daemon can connect
— BlueZ handles the passkey exchange itself:

```bash
bluetoothctl
scan on
# wait for a device named "Claude-XXXX" to appear, note its address
pair AA:BB:CC:DD:EE:FF
# confirm/enter the passkey shown on the Cardputer's screen
trust AA:BB:CC:DD:EE:FF
scan off
exit
```

### 2. Find the device and run the daemon

```bash
./buddy scan                          # prints the BLE address once paired
./buddy daemon --address AA:BB:CC:DD:EE:FF
```

Leave it running in a terminal, tmux pane, or as a systemd user service.

## Wiring up Claude Code

Merge `claude-settings.snippet.json` into `~/.claude/settings.json` (or a
project's `.claude/settings.json`), adjusting the absolute paths to match
where you cloned this repo. This registers the four hooks that feed the
daemon and gate tool permissions through it.

Restart Claude Code CLI sessions after editing settings.json.

## Manual commands

```bash
./buddy status              # query device status (battery, stats, etc.)
./buddy name "Mochi"        # rename the pet
./buddy owner "Alex"        # set the owner name shown on device
./buddy species 3           # switch to a built-in species by index
./buddy unpair              # clear BLE bonds (e.g. before re-pairing to Desktop)
./buddy push-character characters/bufo   # upload a custom character folder
```

`push-character` expects a directory containing a `manifest.json` (same
format the upstream `tools/prep_character.py` produces) plus its asset
files; it drives the firmware's `char_begin`/`file`/`chunk`/`file_end`/
`char_end` upload sequence over BLE.

## Notes / limitations

- Only one permission prompt is shown on the device at a time; if
  multiple Claude Code sessions need approval simultaneously, they queue
  and are shown one after another.
- Activity lines and token counts are read from the session's transcript
  JSONL best-effort — Claude Code doesn't guarantee that file's format
  across versions, so treat these as approximate, cosmetic display data,
  not something else depends on.
- `PreToolUse` hook timeout is set to 300s in the snippet; the daemon
  itself gives up waiting on the device after 280s and falls back to
  Claude Code's normal interactive prompt (`permissionDecision: "ask"`).
- Re-pairing to Claude Desktop later requires `./buddy unpair` (or the
  on-device pairing reset) plus removing the bond from `bluetoothctl`
  (`remove AA:BB:CC:DD:EE:FF`), since BlueZ and the device only keep one
  bonded peer at a time.
