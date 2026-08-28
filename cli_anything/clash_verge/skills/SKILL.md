---
name: "cli-anything-clash-verge"
description: >-
  Command-line interface for Clash Verge Rev - manage subscription profiles, application settings and core ports through Clash Verge's own YAML state, and drive the running Mihomo (Clash Meta) core through its External Controller REST API. Designed for AI agents and power users who need to switch proxy nodes, inspect connections and rotate subscriptions without opening the GUI.
---

# cli-anything-clash-verge

Clash Verge Rev, from the terminal.

Clash Verge Rev is a Tauri GUI around the **Mihomo (Clash Meta)** core. This CLI
works against the same two surfaces the GUI itself uses:

1. **Clash Verge's state files** — `verge.yaml`, `config.yaml`, `profiles.yaml`
   and `profiles/*.yaml`. Always available, no running process required.
2. **The Mihomo External Controller** — the same REST API the Tauri shell drives
   internally, just over HTTP instead of the app's private IPC socket.

No proxying logic is reimplemented here. All traffic handling is done by the
real mihomo core; this CLI only edits state and calls the controller.

## Installation

```bash
pip install git+https://github.com/HKUDS/CLI-Anything.git#subdirectory=clash-verge/agent-harness
```

**Prerequisites:**
- Python 3.10+
- Clash Verge Rev installed and launched at least once (so its data directory exists)

## Usage

```bash
# Start here — verifies everything and tells you what is wrong
cli-anything-clash-verge env doctor

# Interactive REPL
cli-anything-clash-verge repl

# JSON output for agent consumption (--json works before or after the subcommand)
cli-anything-clash-verge --json profile list
cli-anything-clash-verge profile list --json
```

## The one rule you must know

**Clash Verge owns its state files while it is running.** It keeps the
configuration in memory and rewrites the YAML whenever the user changes
something, and it has no file watcher — so an external edit is invisible at
best and silently reverted at worst.

Therefore:

- **Config-file commands** (`profile`, `verge`, `clash`, `controller`) refuse to
  write while the app is running. Close Clash Verge first, or pass `--force`.
- **Live commands** (`proxy`, `mode`, `conn`, `rule`) go through the controller
  and are safe to run at any time.

## Enabling live control

By default Clash Verge talks to the core over a private IPC socket and leaves
the HTTP controller off. To use the live commands:

```bash
cli-anything-clash-verge controller enable   # writes enable_external_controller: true
# restart Clash Verge (or its core)
cli-anything-clash-verge controller status   # confirm it is reachable
```

## Command Groups

### env

Environment discovery and diagnostics.

| Command | Description |
|---------|-------------|
| `info` | Resolved home directory, current profile, runtime ports, controller state |
| `paths` | Every known path plus all candidate home directories |
| `doctor` | Health checks with `ok` / `warn` / `fail` levels (exits 1 on failure) |

### profile

Subscription and configuration profile management. Operates on
`profiles.yaml` and `profiles/`.

| Command | Description |
|---------|-------------|
| `list [--all]` | List profiles (`--all` includes merge/script/rules companions) |
| `current` | Show the active profile |
| `show <sel>` | Detail for one profile (uid or name) |
| `import --url <url>` | Import a remote subscription |
| `import --path <file>` | Import a local YAML profile |
| `create <name>` | Create an empty local profile |
| `update <sel>` | Re-download a remote subscription |
| `select <sel>` | Make a profile active |
| `rename <sel> <name>` | Rename a profile |
| `delete <sel>` | Delete a profile and its companions |
| `export <sel> <dest>` | Copy a profile's data file out |
| `file get <sel>` | Print the raw profile YAML |
| `file set <sel> <src>` | Replace a profile's data file (validated first) |

`<sel>` accepts either a uid (`R9NPhlFZAf12`) or a name. Names are matched
case-insensitively; an ambiguous name is an error rather than a silent guess.

Importing applies Clash Verge's own acceptance test: the document must parse as
YAML **and** contain `proxies` or `proxy-providers`. It also creates the five
companion items (merge / script / rules / proxies / groups) that Clash Verge
expects next to every profile.

### verge

`verge.yaml` — Clash Verge application settings.

| Command | Description |
|---------|-------------|
| `list [--set-only]` | Every known setting with its effective value |
| `get <key>` | Show one setting |
| `set <key> <value>` | Set a setting (booleans `true`/`false`, lists as JSON) |
| `unset <key>` | Remove the key so Clash Verge uses its own default |

Note: `webdav_url`, `webdav_username` and `webdav_password` are stored
**encrypted** by Clash Verge. The CLI refuses to write them.

### clash

`config.yaml` — Clash core overrides.

| Command | Description |
|---------|-------------|
| `list` | Ports, mode, allow-lan, controller address, secret |
| `get <key>` | Show one setting |
| `set <key> <value>` | Set a setting (ports validated 1–65535, mode/log-level enum-checked) |
| `unset <key>` | Remove the key |

### controller

The Mihomo HTTP External Controller.

| Command | Description |
|---------|-------------|
| `status` | Enabled? Reachable? Which version? |
| `enable` / `disable` | Toggle `enable_external_controller` (core restart required) |
| `url [--set URL]` | Show or override the controller URL for this session |
| `secret [--set SECRET]` | Show or override the Bearer secret for this session |

### proxy

Live proxy control (needs the core running).

| Command | Description |
|---------|-------------|
| `groups` | Selectable proxy groups with their current node |
| `nodes [--group G]` | Proxy nodes, optionally one group's members |
| `select <group> <node>` | Switch a group to a node |
| `current [--group G]` | Show the selected node of each group |
| `delay [name] [--url U] [--timeout MS] [--group]` | Measure latency |
| `providers` | List proxy providers |
| `update-provider <name>` | Force-refresh a provider |

### mode

| Command | Description |
|---------|-------------|
| `get` | Current routing mode |
| `set <rule\|global\|direct>` | Change mode live |

### conn

| Command | Description |
|---------|-------------|
| `list [--limit N]` | Active connections with their rule and chain |
| `close <id>` | Close one connection |
| `close-all` | Close every connection |

### rule

| Command | Description |
|---------|-------------|
| `list [--limit N]` | The rule set currently loaded by the core |

### core

| Command | Description |
|---------|-------------|
| `status` | GUI/core process state plus controller reachability |
| `version` | Running core version |
| `configs` | Dump the live core configuration |
| `launch` | Start the Clash Verge GUI |

### log

| Command | Description |
|---------|-------------|
| `files` | List log files, newest first |
| `tail [--lines N] [--file F]` | Tail a log file |

### session

Every state mutation is recorded with the exact bytes it changed, so any write
is reversible — even across separate CLI invocations.

| Command | Description |
|---------|-------------|
| `show` | Session file location, target directory, undo depth |
| `home [path]` | Show or set the Clash Verge home directory |
| `history` | Recorded changes, newest first |
| `undo` | Revert the most recent change |
| `redo` | Re-apply the most recently undone change |

## Common Recipes

```bash
# Diagnose the installation
cli-anything-clash-verge env doctor

# Add a subscription and make it active
cli-anything-clash-verge profile import --url "https://example.com/sub" --name "main"
cli-anything-clash-verge profile select main
# restart the core in Clash Verge for it to take effect

# Refresh every remote subscription
cli-anything-clash-verge --json profile list | \
  python -c "import json,sys; print('\n'.join(p['uid'] for p in json.load(sys.stdin) if p['type']=='remote'))"

# Turn on the HTTP controller, then drive the core live
cli-anything-clash-verge controller enable
# (restart Clash Verge)
cli-anything-clash-verge proxy groups
cli-anything-clash-verge --json proxy nodes --group PROXY
cli-anything-clash-verge proxy select PROXY "Hong Kong 01"
cli-anything-clash-verge mode set global

# Measure latency across a whole group and pick the fastest
cli-anything-clash-verge --json proxy delay PROXY --group

# Oops — revert the last write
cli-anything-clash-verge session undo
```

## Output

Every command supports `--json`, which prints a single JSON document and nothing
else — no banners, no progress lines. Accepted before or after the subcommand.

Human output uses the shared cli-anything REPL skin (tables, status blocks,
colour), and the REPL (`cli-anything-clash-verge repl`) offers tab completion
and history.

## State locations

The home directory is auto-detected, in this order:

1. `--home <dir>` / `$CLASH_VERGE_HOME`
2. Portable layout `<exe_dir>/.config/io.github.clash-verge-rev.clash-verge-rev`
   (set `$CLASH_VERGE_PORTABLE_EXE_DIR`)
3. Standard release directory
4. Dev build directory (`...clash-verge-rev.dev`)

===============  ==========================================================
Platform         Standard location
===============  ==========================================================
Windows          `%APPDATA%\io.github.clash-verge-rev.clash-verge-rev`
macOS            `~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev`
Linux            `$XDG_DATA_HOME` or `~/.local/share` + `/io.github.clash-verge-rev.clash-verge-rev`
===============  ==========================================================

Files: `verge.yaml`, `config.yaml`, `profiles.yaml`, `profiles/`, `logs/`,
`clash-verge.yaml` (the config mihomo actually loaded).

Use `clash-verge env paths` to see all of them with existence flags.

## Limitations

- **Remote subscriptions are fetched, not converted.** `profile import --url`
  downloads the document and validates it the same way Clash Verge does; it does
  not rewrite it.
- **Config edits need the core restarted** to take effect. The CLI cannot
  restart it — there is no external trigger for that. Live changes go through
  `proxy` / `mode` instead.
- **`/traffic` and `/logs` streaming** are WebSocket-only endpoints and require
  the optional dependency `pip install websocket-client`. `log tail` reads
  Clash Verge's own log files and needs nothing extra.
- **WebDAV credentials** cannot be read or written (stored encrypted).

## Testing

```bash
cd clash-verge/agent-harness
python -m pytest cli_anything/clash_verge/tests/ -v
```

193 tests. No Clash Verge installation and no network access required — the
suite runs against temporary directories and a loopback HTTP server that stands
in for the mihomo controller.
