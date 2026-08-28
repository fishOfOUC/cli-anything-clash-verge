# cli-anything-clash-verge

Agent-native CLI for **Clash Verge Rev**.

Manages Clash Verge's own state files (`profiles.yaml`, `verge.yaml`,
`config.yaml`, `profiles/`) and drives the running Mihomo (Clash Meta) core
through its External Controller REST API. No proxying logic is reimplemented —
all traffic handling stays in the real mihomo core.

## Install

```bash
cd clash-verge/agent-harness
pip install -e .
```

## Quick start

```bash
# Diagnose the installation
cli-anything-clash-verge env doctor

# Profiles (state files — close Clash Verge first)
cli-anything-clash-verge profile list
cli-anything-clash-verge profile import --url "https://example.com/sub" --name main
cli-anything-clash-verge profile select main

# Settings
cli-anything-clash-verge verge set enable_system_proxy true
cli-anything-clash-verge clash set mixed-port 8080

# Live control (needs the core running; enable once)
cli-anything-clash-verge controller enable
cli-anything-clash-verge proxy groups
cli-anything-clash-verge proxy select PROXY "Hong Kong 01"
cli-anything-clash-verge mode set global

# Undo any write
cli-anything-clash-verge session undo
```

## Layout

```
cli_anything/clash_verge/
├── clash_verge_cli.py            Click CLI + REPL
├── core/
│   ├── paths.py                  home directory resolution (mirrors dirs.rs)
│   ├── yamlio.py                 tolerant, atomic YAML persistence
│   ├── session.py                session state + byte-exact undo/redo
│   ├── verge.py                  verge.yaml (IVerge schema)
│   ├── clash.py                  config.yaml (IClashTemp) + clash-verge.yaml
│   ├── profiles.py               profiles.yaml + profiles/ (PrfItem)
│   ├── subscription.py           fetch/validate, mirrors PrfItem::from_url
│   ├── controller.py             Mihomo External Controller REST client
│   └── process.py                GUI / sidecar detection
├── utils/
│   ├── clash_verge_backend.py    facade; owns the write-race guard
│   ├── templates.py              profile templates from tmpl.rs
│   └── repl_skin.py              shared cli-anything REPL skin
├── skills/SKILL.md               agent-facing documentation
└── tests/                        TEST.md + 193 tests
```

## The write race

Clash Verge keeps its configuration in memory and rewrites the YAML when the
user changes something; it has no file watcher. External edits made while the
app runs are silently reverted. State-file commands therefore refuse to write
while the app is running — close it, use a live command, or pass `--force`.

## Tests

```bash
python -m pytest cli_anything/clash_verge/tests/ -v
```

No Clash Verge installation or network access required.

## Documentation

- `skills/SKILL.md` — full command reference and recipes
- `../CLASH_VERGE.md` — software analysis this harness is built on
- `tests/TEST.md` — test plan
