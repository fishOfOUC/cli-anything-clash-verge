# cli-anything-clash-verge

Agent-native CLI for **Clash Verge Rev**. It drives the exact same backends the
GUI uses — Clash Verge's state files (`profiles.yaml`, `verge.yaml`, `config.yaml`,
`profiles/`) and the Mihomo **External Controller** REST API — so an agent (or you)
can manage subscriptions, profiles, settings, proxies, rules and the live core
without clicking through the UI.

> This is a [CLI-Anything](https://github.com/HKUDS/CLI-Anything) harness packaged
> as a standalone project.

## Install

```bash
pip install -e .
```

Dependencies: `click`, `PyYAML`, `requests`, `prompt-toolkit` (and `websocket-client`
for `controller stream`).

## Quick start

```bash
clash-verge --help
clash-verge env                 # show where Clash Verge stores its data
clash-verge profile list --json
clash-verge controller proxies --json
clash-verge mode set rule
```

Read-only commands are safe by default. Commands that modify state files refuse to
run while Clash Verge is running (write-race guard), unless `--yes` is passed.

Every mutating command records a **byte-exact undo**; revert with:

```bash
clash-verge session list
clash-verge session undo <id>
```

## Command groups

`env` · `profile` · `verge` · `clash` · `controller` · `proxy` · `mode` · `conn` ·
`rule` · `core` · `log` · `session` · `repl`

See [`CLASH_VERGE.md`](CLASH_VERGE.md) for the full reference and
[`SKILL.md`](SKILL.md) for the agent-facing skill definition.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

195 tests (unit + end-to-end). No install or network required — the External
Controller is mocked.
