"""cli-anything-clash-verge — command line interface for Clash Verge Rev.

Everything here is a thin shell over :mod:`cli_anything.clash_verge.utils.
clash_verge_backend`. No packet handling, no config parsing logic — the real
work happens in Clash Verge's own YAML state and in the mihomo core.
"""

from __future__ import annotations

import json as _json
import shlex
import sys
from typing import Any

import click

from . import __version__
from .core import process as process_mod
from .core.controller import ControllerError
from .core.paths import ClashVergeNotFound
from .core.profiles import ProfileError
from .core.subscription import SubscriptionError
from .core.verge import VergeError
from .core.yamlio import YamlError
from .utils.clash_verge_backend import (
    AppRunning,
    BackendError,
    ClashVergeBackend,
    ControllerNotReady,
)
from .utils.repl_skin import ReplSkin

#: Domain errors that carry a complete, actionable message already.
KNOWN_ERRORS = (
    BackendError,
    AppRunning,
    ControllerNotReady,
    ControllerError,
    ProfileError,
    SubscriptionError,
    VergeError,
    YamlError,
    ClashVergeNotFound,
)


def _fail(exc: Exception) -> None:
    """Convert a domain error into a clean CLI error message."""
    raise click.ClickException(str(exc)) from exc


def new_backend(home: str | None = None) -> ClashVergeBackend:
    try:
        return ClashVergeBackend(home_dir=home)
    except ClashVergeNotFound as exc:
        _fail(exc)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, KNOWN_ERRORS):
            _fail(exc)
        raise
    raise AssertionError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# root
# ---------------------------------------------------------------------------
@click.group(invoke_without_command=False)
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON.")
@click.option(
    "--home",
    "home",
    metavar="DIR",
    default=None,
    help="Clash Verge home directory (default: auto-detect).",
)
@click.version_option(__version__, prog_name="cli-anything-clash-verge")
@click.pass_context
def cli(ctx: click.Context, as_json: bool, home: str | None) -> None:
    """Clash Verge Rev, from the terminal.

    Manages Clash Verge's own state files (profiles.yaml, verge.yaml,
    config.yaml) and drives the live mihomo core through its External
    Controller.

    \b
    State files (safe to edit with the app closed):
      profile ...   subscription / config profile management
      verge .....   verge.yaml  — application settings
      clash .....   config.yaml — Clash core overrides

    \b
    Live core (needs Clash Verge running):
      proxy .....   proxy groups, node selection, latency
      mode ......   rule | global | direct
      conn ......   active connections
      rule ......   loaded rule set

    \b
    Start here:
      clash-verge env doctor
    """
    ctx.ensure_object(dict)
    # `or` keeps a --json that was hoisted from a later position by _invoke().
    ctx.obj["json"] = bool(as_json) or bool(ctx.obj.get("json"))
    ctx.obj["home"] = home


def _backend(ctx: click.Context) -> ClashVergeBackend:
    return new_backend(ctx.obj.get("home"))


def _json_flag(ctx: click.Context, _param: click.Parameter, value: bool) -> bool:
    """Set the JSON flag on the shared context.

    Exists so ``--json`` works in both positions::

        clash-verge --json profile list
        clash-verge profile list --json

    Agents routinely get this wrong, so both spellings are accepted.
    """
    ctx.ensure_object(dict)
    if value:
        ctx.obj["json"] = True
    return value


def json_option(func: Any) -> Any:
    """Attach a context-setting ``--json`` flag to a group or command."""
    return click.option(
        "--json",
        "as_json",
        is_flag=True,
        expose_value=False,
        is_eager=True,
        callback=_json_flag,
        help="Output results as JSON.",
    )(func)


def out(
    ctx: click.Context,
    payload: Any,
    *,
    title: str | None = None,
    headers: list[str] | None = None,
    rows: list[list[str]] | None = None,
    statuses: dict[str, str] | None = None,
) -> None:
    """Emit ``payload`` as JSON, or a titled table / status block for humans."""
    if ctx.obj.get("json"):
        click.echo(_json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return

    skin = ReplSkin("clash-verge", version=__version__)
    if title:
        skin.section(title)
    if headers and rows is not None:
        skin.table(headers, rows)
    if statuses:
        skin.status_block(statuses)


def _fmt(value: Any) -> str:
    """Render a value for a table cell."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) if value else "-"
    if isinstance(value, dict):
        return _json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _report_change(ctx: click.Context, entry: dict | None, skin: ReplSkin | None = None) -> None:
    """Tell the user a mutation happened and how to reverse it."""
    if entry is None:
        return
    if ctx.obj.get("json"):
        return
    label = entry.get("label", "change")
    skin = skin or ReplSkin("clash-verge", version=__version__)
    skin.hint(f"recorded for undo ({label}) — reverse with: clash-verge session undo")


# ===========================================================================
# env
# ===========================================================================
@cli.group()
@json_option
def env() -> None:
    """Environment: paths, state summary, health checks."""


@env.command("info")
@click.pass_context
def env_info(ctx: click.Context) -> None:
    """Show the resolved state of Clash Verge."""
    backend = _backend(ctx)
    info = backend.env_info()
    if ctx.obj["json"]:
        out(ctx, info)
        return
    skin = ReplSkin("clash-verge", version=__version__)
    skin.section("Clash Verge")
    skin.status_block(
        {
            "Home": info["home"],
            "Layout": info["layout"],
            "GUI running": _fmt(info["app"]["gui_running"]),
            "Core running": _fmt(info["app"]["core_running"]),
        }
    )
    profile = info["profile"]
    skin.status_block(
        {
            "Current profile": profile["current_name"] or "(none)",
            "Current uid": profile["current_uid"] or "-",
            "Items": f"{profile['total_items']} total, {profile['main_profiles']} selectable",
        },
        title="Profiles",
    )
    runtime = info["runtime"]
    skin.status_block(
        {
            "Generated config": "clash-verge.yaml" if runtime["available"] else "(not generated)",
            "Mode": _fmt(runtime.get("mode")),
            "Mixed port": _fmt(runtime.get("mixed_port")),
            "Proxies": _fmt(runtime.get("proxy_count")),
            "Providers": _fmt(runtime.get("provider_count")),
            "Rules": _fmt(runtime.get("rule_count")),
        },
        title="Runtime",
    )
    controller = info["controller"]
    skin.status_block(
        {
            "Enabled": _fmt(controller.get("enabled")),
            "URL": _fmt(controller.get("url")),
            "Reachable": _fmt(controller.get("reachable")),
            "Version": _fmt(controller.get("version")),
        },
        title="Controller",
    )


@env.command("paths")
@click.pass_context
def env_paths(ctx: click.Context) -> None:
    """List every Clash Verge path and which candidates exist."""
    backend = _backend(ctx)
    payload = backend.env_paths()
    if ctx.obj["json"]:
        out(ctx, payload)
        return
    skin = ReplSkin("clash-verge", version=__version__)
    skin.section("Paths")
    skin.status_block(
        {
            "In use": payload["in_use"],
            "Layout": payload["layout"],
        }
    )
    skin.table(
        ["Key", "Path", "Exists"],
        [
            [key, payload["files"][key], _fmt(payload["present"][key])]
            for key in payload["files"]
        ],
    )
    skin.table(
        ["Candidate", "Path", "Exists"],
        [[row["label"], row["path"], _fmt(row["exists"])] for row in payload["candidates"]],
        title="Candidate home directories",
    )


@env.command("doctor")
@click.pass_context
def env_doctor(ctx: click.Context) -> None:
    """Run health checks and report what is broken."""
    backend = _backend(ctx)
    checks = backend.env_doctor()
    if ctx.obj["json"]:
        out(ctx, checks)
        return
    skin = ReplSkin("clash-verge", version=__version__)
    skin.section("Diagnostics")
    skin.table(
        ["Level", "Check", "Detail"],
        [[row["level"], row["check"], row["detail"]] for row in checks],
    )
    failures = [row for row in checks if row["level"] == "fail"]
    warnings = [row for row in checks if row["level"] == "warn"]
    if failures:
        skin.error(f"{len(failures)} problem(s) need attention")
        raise SystemExit(1)
    if warnings:
        skin.warning(f"{len(warnings)} warning(s)")
    else:
        skin.success("all checks passed")


# ===========================================================================
# profile
# ===========================================================================
@cli.group()
@json_option
def profile() -> None:
    """Subscription and configuration profiles."""


@profile.command("list")
@click.option("--all", "all_types", is_flag=True, help="Include merge/script/rules companions.")
@click.pass_context
def profile_list(ctx: click.Context, all_types: bool) -> None:
    """List profiles."""
    backend = _backend(ctx)
    rows = backend.profile_list(all_types=all_types)
    if ctx.obj["json"]:
        out(ctx, rows)
        return
    skin = ReplSkin("clash-verge", version=__version__)
    skin.section("Profiles")
    if not rows:
        skin.info("no profiles found")
        return
    skin.table(
        ["", "UID", "Name", "Type", "Updated", "File"],
        [
            [
                "*" if row["current"] else "",
                row["uid"],
                row["name"] or "-",
                row["type"],
                _fmt(row["updated"]),
                _fmt(row["file_exists"]),
            ]
            for row in rows
        ],
    )


@profile.command("current")
@click.pass_context
def profile_current(ctx: click.Context) -> None:
    """Show the active profile."""
    backend = _backend(ctx)
    item = backend.profiles.current()
    if item is None:
        if ctx.obj["json"]:
            out(ctx, {"current": None})
            return
        ReplSkin("clash-verge", version=__version__).warning("no profile selected")
        raise SystemExit(1)
    payload = {
        "uid": item.get("uid"),
        "name": item.get("name"),
        "type": item.get("type"),
        "url": item.get("url"),
        "updated": item.get("updated"),
        "file": item.get("file"),
        "extra": item.get("extra"),
        "option": item.get("option"),
    }
    out(
        ctx,
        payload,
        title="Current profile",
        statuses={key: _fmt(value) for key, value in payload.items()},
    )


@profile.command("show")
@click.argument("selector")
@click.pass_context
def profile_show(ctx: click.Context, selector: str) -> None:
    """Show one profile in detail (uid or name)."""
    backend = _backend(ctx)
    try:
        payload = backend.profile_show(selector)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    if ctx.obj["json"]:
        out(ctx, payload)
        return
    skin = ReplSkin("clash-verge", version=__version__)
    item = payload["item"]
    skin.section(f"Profile: {item.get('name')}")
    skin.status_block(
        {
            "UID": _fmt(item.get("uid")),
            "Type": _fmt(item.get("type")),
            "URL": _fmt(item.get("url")),
            "Home": _fmt(item.get("home")),
            "Updated": _fmt(item.get("updated")),
            "File": _fmt(payload.get("file")),
            "Size": _fmt(payload.get("size")),
        }
    )
    if "contents" in payload:
        skin.status_block(
            {key: _fmt(value) for key, value in payload["contents"].items()},
            title="Contents",
        )
    if payload.get("contents_error"):
        skin.warning(f"cannot parse contents: {payload['contents_error']}")
    if payload.get("companions"):
        skin.table(
            ["UID", "Type", "Name"],
            [[c["uid"], c["type"], c["name"]] for c in payload["companions"]],
            title="Companion items",
        )


@profile.command("import")
@click.option("--url", default=None, help="Subscription URL (remote profile).")
@click.option("--path", "path", default=None, type=click.Path(exists=True), help="Local YAML file.")
@click.option("--name", default=None, help="Profile name (default: filename/URL).")
@click.option("--user-agent", default=None, help="Custom User-Agent header.")
@click.option("--timeout", default=60, show_default=True, help="HTTP timeout in seconds.")
@click.option("--with-proxy", is_flag=True, help="Fetch through the system proxy.")
@click.option("--insecure", is_flag=True, help="Skip TLS certificate verification.")
@click.option("--force", is_flag=True, help="Write even if Clash Verge is running.")
@click.pass_context
def profile_import(
    ctx: click.Context,
    url: str | None,
    path: str | None,
    name: str | None,
    user_agent: str | None,
    timeout: int,
    with_proxy: bool,
    insecure: bool,
    force: bool,
) -> None:
    """Import a subscription URL or a local YAML file."""
    if bool(url) == bool(path):
        raise click.UsageError("provide exactly one of --url or --path.")
    backend = _backend(ctx)
    itype = "remote" if url else "local"
    try:
        payload, entry = backend.profile_import(
            url=url,
            path=path,
            name=name,
            itype=itype,
            user_agent=user_agent,
            timeout=timeout,
            with_proxy=with_proxy,
            insecure=insecure,
            force=force,
        )
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title="Imported profile",
        statuses={
            "UID": payload["uid"],
            "Name": payload["name"],
            "Type": payload["type"],
            "File": payload["file"],
            **{key: _fmt(value) for key, value in payload["summary"].items()},
        },
    )
    _report_change(ctx, entry)


@profile.command("create")
@click.argument("name")
@click.option("--force", is_flag=True, help="Write even if Clash Verge is running.")
@click.pass_context
def profile_create(ctx: click.Context, name: str, force: bool) -> None:
    """Create an empty local profile."""
    backend = _backend(ctx)
    try:
        payload, entry = backend.profile_create(name, force=force)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title="Created profile",
        statuses={
            "UID": payload["uid"],
            "Name": payload["name"],
            "File": payload["file"],
            "Companions": _fmt(payload["companions"]),
        },
    )
    _report_change(ctx, entry)


@profile.command("delete")
@click.argument("selector")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@click.option("--force", is_flag=True, help="Write even if Clash Verge is running.")
@click.pass_context
def profile_delete(ctx: click.Context, selector: str, yes: bool, force: bool) -> None:
    """Delete a profile and its companions."""
    backend = _backend(ctx)
    try:
        item = backend.profiles.resolve(selector)
        if not yes and not ctx.obj["json"]:
            click.confirm(
                f"Delete profile '{item.get('name')}' ({item.get('uid')}) and its "
                "companion items? This cannot be undone by Clash Verge, only by "
                "`clash-verge session undo`.",
                abort=True,
            )
        payload, entry = backend.profile_delete(selector, force=force)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title="Deleted profile",
        statuses={
            "UID": payload["uid"],
            "Name": payload["name"],
            "Files removed": _fmt(payload["removed_files"]),
            "Companions removed": _fmt(payload["companions_removed"]),
        },
    )
    _report_change(ctx, entry)


@profile.command("select")
@click.argument("selector")
@click.option("--force", is_flag=True, help="Write even if Clash Verge is running.")
@click.pass_context
def profile_select(ctx: click.Context, selector: str, force: bool) -> None:
    """Make a profile active."""
    backend = _backend(ctx)
    try:
        payload, entry = backend.profile_select(selector, force=force)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title="Selected profile",
        statuses={"Previous": _fmt(payload["previous"]), "Current": payload["current"]},
    )
    _report_change(ctx, entry)
    if not ctx.obj["json"]:
        ReplSkin("clash-verge", version=__version__).hint(
            "restart the Clash Verge core for the new profile to take effect"
        )


@profile.command("update")
@click.argument("selector")
@click.option("--force", is_flag=True, help="Write even if Clash Verge is running.")
@click.pass_context
def profile_update(ctx: click.Context, selector: str, force: bool) -> None:
    """Re-download a remote subscription."""
    backend = _backend(ctx)
    try:
        payload, entry = backend.profile_update(selector, force=force)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title="Updated profile",
        statuses={
            "UID": payload["uid"],
            "Name": payload["name"],
            "URL": payload["url"],
            **{key: _fmt(value) for key, value in payload["summary"].items()},
        },
    )
    _report_change(ctx, entry)


@profile.command("rename")
@click.argument("selector")
@click.argument("name")
@click.option("--force", is_flag=True, help="Write even if Clash Verge is running.")
@click.pass_context
def profile_rename(ctx: click.Context, selector: str, name: str, force: bool) -> None:
    """Rename a profile."""
    backend = _backend(ctx)
    try:
        payload, entry = backend.profile_rename(selector, name, force=force)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title="Renamed profile",
        statuses={"UID": payload["uid"], "Old": payload["old"], "New": payload["new"]},
    )
    _report_change(ctx, entry)


@profile.command("export")
@click.argument("selector")
@click.argument("dest", type=click.Path())
@click.pass_context
def profile_export(ctx: click.Context, selector: str, dest: str) -> None:
    """Copy a profile's data file to DEST."""
    backend = _backend(ctx)
    try:
        target = backend.profile_export(selector, dest)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        {"selector": selector, "dest": str(target)},
        title="Exported profile",
        statuses={"Destination": str(target)},
    )


@profile.group("file")
@json_option
def profile_file() -> None:
    """Read or replace a profile's raw data file."""


@profile_file.command("get")
@click.argument("selector")
@click.pass_context
def profile_file_get(ctx: click.Context, selector: str) -> None:
    """Print the raw profile YAML to stdout."""
    backend = _backend(ctx)
    try:
        text = backend.profile_file_read(selector)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    if ctx.obj["json"]:
        out(ctx, {"selector": selector, "content": text})
    else:
        click.echo(text)


@profile_file.command("set")
@click.argument("selector")
@click.argument("source", type=click.Path(exists=True))
@click.option("--force", is_flag=True, help="Write even if Clash Verge is running.")
@click.pass_context
def profile_file_set(ctx: click.Context, selector: str, source: str, force: bool) -> None:
    """Replace a profile's data file with SOURCE (validated first)."""
    backend = _backend(ctx)
    try:
        payload, entry = backend.profile_file_write(selector, source, force=force)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title="Profile file replaced",
        statuses={
            "UID": payload["uid"],
            "File": payload["file"],
            **{key: _fmt(value) for key, value in payload["summary"].items()},
        },
    )
    _report_change(ctx, entry)


# ===========================================================================
# verge.yaml
# ===========================================================================
@cli.group()
@json_option
def verge() -> None:
    """verge.yaml — Clash Verge application settings."""


@verge.command("list")
@click.option("--set-only", is_flag=True, help="Only show keys explicitly present in the file.")
@click.pass_context
def verge_list(ctx: click.Context, set_only: bool) -> None:
    """List every known setting with its effective value."""
    backend = _backend(ctx)
    rows = backend.verge_list(only_set=set_only)
    if ctx.obj["json"]:
        out(ctx, rows)
        return
    ReplSkin("clash-verge", version=__version__).table(
        ["Key", "Value", "Set", "Type", "Description"],
        [
            [row["key"], _fmt(row["value"]), _fmt(row["set"]), row["type"], row["doc"]]
            for row in rows
        ],
        title="verge.yaml",
    )


@verge.command("get")
@click.argument("key")
@click.pass_context
def verge_get(ctx: click.Context, key: str) -> None:
    """Show one setting."""
    backend = _backend(ctx)
    try:
        payload = backend.verge_get(key)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title=f"verge.yaml: {key}",
        statuses={
            "Value": _fmt(payload["value"]),
            "Set in file": _fmt(payload["set"]),
            "Type": payload["type"],
            "Description": payload["doc"],
        },
    )


@verge.command("set")
@click.argument("key")
@click.argument("value")
@click.option("--force", is_flag=True, help="Write even if Clash Verge is running.")
@click.pass_context
def verge_set(ctx: click.Context, key: str, value: str, force: bool) -> None:
    """Set a setting (booleans: true/false; lists: JSON array)."""
    backend = _backend(ctx)
    try:
        payload, entry = backend.verge_set(key, value, force=force)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title=f"verge.yaml: {key}",
        statuses={"Old": _fmt(payload["old"]), "New": _fmt(payload["new"])},
    )
    _report_change(ctx, entry)


@verge.command("unset")
@click.argument("key")
@click.option("--force", is_flag=True, help="Write even if Clash Verge is running.")
@click.pass_context
def verge_unset(ctx: click.Context, key: str, force: bool) -> None:
    """Remove a key so Clash Verge falls back to its default."""
    backend = _backend(ctx)
    try:
        payload, entry = backend.verge_unset(key, force=force)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title=f"verge.yaml: {key}",
        statuses={"Removed": _fmt(payload["removed"]), "Old": _fmt(payload["old"])},
    )
    _report_change(ctx, entry)


# ===========================================================================
# config.yaml
# ===========================================================================
@cli.group()
@json_option
def clash() -> None:
    """config.yaml — Clash core overrides (ports, mode, controller)."""


@clash.command("list")
@click.pass_context
def clash_list(ctx: click.Context) -> None:
    """List core settings with their effective values."""
    backend = _backend(ctx)
    rows = backend.clash_list()
    if ctx.obj["json"]:
        out(ctx, rows)
        return
    ReplSkin("clash-verge", version=__version__).table(
        ["Key", "Value", "Set in file"],
        [[row["key"], _fmt(row["value"]), _fmt(row["set"])] for row in rows],
        title="config.yaml",
    )


@clash.command("get")
@click.argument("key")
@click.pass_context
def clash_get(ctx: click.Context, key: str) -> None:
    """Show one core setting."""
    backend = _backend(ctx)
    payload = backend.clash_get(key)
    out(
        ctx,
        payload,
        title=f"config.yaml: {key}",
        statuses={"Value": _fmt(payload["value"]), "Set in file": _fmt(payload["set"])},
    )


@clash.command("set")
@click.argument("key")
@click.argument("value")
@click.option("--force", is_flag=True, help="Write even if Clash Verge is running.")
@click.pass_context
def clash_set(ctx: click.Context, key: str, value: str, force: bool) -> None:
    """Set a core setting."""
    backend = _backend(ctx)
    try:
        payload, entry = backend.clash_set(key, value, force=force)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title=f"config.yaml: {key}",
        statuses={"Old": _fmt(payload["old"]), "New": _fmt(payload["new"])},
    )
    _report_change(ctx, entry)


@clash.command("unset")
@click.argument("key")
@click.option("--force", is_flag=True, help="Write even if Clash Verge is running.")
@click.pass_context
def clash_unset(ctx: click.Context, key: str, force: bool) -> None:
    """Remove a core setting."""
    backend = _backend(ctx)
    try:
        payload, entry = backend.clash_unset(key, force=force)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title=f"config.yaml: {key}",
        statuses={"Removed": _fmt(payload["removed"]), "Old": _fmt(payload["old"])},
    )
    _report_change(ctx, entry)


# ===========================================================================
# controller
# ===========================================================================
@cli.group()
@json_option
def controller() -> None:
    """The mihomo HTTP External Controller."""


@controller.command("status")
@click.pass_context
def controller_status(ctx: click.Context) -> None:
    """Is the controller enabled, and is it reachable?"""
    backend = _backend(ctx)
    payload = backend.controller_status()
    if ctx.obj["json"]:
        out(ctx, payload)
        return
    skin = ReplSkin("clash-verge", version=__version__)
    skin.section("External controller")
    skin.status_block(
        {
            "Enabled": _fmt(payload["enabled"]),
            "URL": _fmt(payload["url"]),
            "Address": _fmt(payload["address"]),
            "Secret set": _fmt(payload["secret_set"]),
            "Secret is default": _fmt(payload["secret_is_default"]),
        }
    )
    probe = payload.get("probe") or {}
    skin.status_block(
        {
            "Reachable": _fmt(probe.get("reachable")),
            "Version": _fmt(probe.get("version")),
            "Latency": f"{probe.get('latency_ms')} ms" if probe.get("reachable") else "-",
            "Error": _fmt(probe.get("error")),
        },
        title="Probe",
    )
    if not payload["enabled"]:
        skin.hint("enable with: clash-verge controller enable")


@controller.command("enable")
@click.option("--force", is_flag=True, help="Write even if Clash Verge is running.")
@click.pass_context
def controller_enable(ctx: click.Context, force: bool) -> None:
    """Enable the HTTP controller (core restart required)."""
    backend = _backend(ctx)
    try:
        payload, entry = backend.controller_enable(force=force)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title="Controller",
        statuses={"enable_external_controller": f"{payload['old']} -> {payload['new']}"},
    )
    _report_change(ctx, entry)
    if not ctx.obj["json"]:
        ReplSkin("clash-verge", version=__version__).hint(
            "restart the Clash Verge core (or the app) — the flag is read at core start"
        )


@controller.command("disable")
@click.option("--force", is_flag=True, help="Write even if Clash Verge is running.")
@click.pass_context
def controller_disable(ctx: click.Context, force: bool) -> None:
    """Disable the HTTP controller (core restart required)."""
    backend = _backend(ctx)
    try:
        payload, entry = backend.controller_disable(force=force)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title="Controller",
        statuses={"enable_external_controller": f"{payload['old']} -> {payload['new']}"},
    )
    _report_change(ctx, entry)


@controller.command("url")
@click.option("--set", "set_url", default=None, metavar="URL", help="Override the URL in this session.")
@click.pass_context
def controller_url(ctx: click.Context, set_url: str | None) -> None:
    """Show or override the controller URL for this session."""
    backend = _backend(ctx)
    if set_url is not None:
        backend.session.session.controller_url = set_url
        backend.invalidate_controller()
        backend.session.save()
    payload = {"url": backend.controller().base_url, "override": set_url}
    out(ctx, payload, title="Controller URL", statuses={"URL": payload["url"]})


@controller.command("secret")
@click.option("--set", "set_secret", default=None, metavar="SECRET", help="Override the secret in this session.")
@click.pass_context
def controller_secret(ctx: click.Context, set_secret: str | None) -> None:
    """Show or override the controller secret for this session."""
    backend = _backend(ctx)
    if set_secret is not None:
        backend.session.session.secret = set_secret
        backend.invalidate_controller()
        backend.session.save()
    secret = backend.controller().secret
    shown = secret if set_secret is not None else f"{secret[:4]}...({len(secret)} chars)"
    payload = {"secret": secret, "is_default": secret == "set-your-secret"}
    out(ctx, payload, title="Controller secret", statuses={"Secret": shown})


# ===========================================================================
# live: proxy / mode / conn / rule / core
# ===========================================================================
@cli.group()
@json_option
def proxy() -> None:
    """Proxy groups and nodes (needs the core running)."""


@proxy.command("groups")
@click.pass_context
def proxy_groups(ctx: click.Context) -> None:
    """List selectable proxy groups."""
    backend = _backend(ctx)
    try:
        payload = backend.proxy_groups()
    except KNOWN_ERRORS as exc:
        _fail(exc)
    if ctx.obj["json"]:
        out(ctx, payload)
        return
    ReplSkin("clash-verge", version=__version__).table(
        ["Group", "Type", "Selected", "Members"],
        [
            [row["name"], row["type"], row["now"] or "-", str(row["members"])]
            for row in payload["groups"]
        ],
        title=f"Proxy groups ({payload['count']})",
    )


@proxy.command("nodes")
@click.option("--group", default=None, help="Only members of this group.")
@click.pass_context
def proxy_nodes(ctx: click.Context, group: str | None) -> None:
    """List proxy nodes."""
    backend = _backend(ctx)
    try:
        payload = backend.proxy_nodes(group)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    if ctx.obj["json"]:
        out(ctx, payload)
        return
    ReplSkin("clash-verge", version=__version__).table(
        ["Node", "Type", "Delay"],
        [
            [row["name"], row["type"] or "-", _fmt(row["delay"])]
            for row in payload["nodes"]
        ],
        title=f"Nodes ({payload['count']})"
        + (f" in {group}" if group else ""),
    )


@proxy.command("select")
@click.argument("group")
@click.argument("node")
@click.pass_context
def proxy_select(ctx: click.Context, group: str, node: str) -> None:
    """Switch GROUP to NODE."""
    backend = _backend(ctx)
    try:
        payload = backend.proxy_select(group, node)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title="Selected node",
        statuses={"Group": payload["group"], "Node": payload["node"], "Now": _fmt(payload["now"])},
    )


@proxy.command("current")
@click.option("--group", default=None, help="Only this group.")
@click.pass_context
def proxy_current(ctx: click.Context, group: str | None) -> None:
    """Show the selected node of each group."""
    backend = _backend(ctx)
    try:
        payload = backend.proxy_current(group)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    if ctx.obj["json"] or group:
        out(
            ctx,
            payload,
            title="Current selection",
            statuses=(
                {"Group": group, "Node": _fmt(payload.get("now"))}
                if group
                else {key: _fmt(value) for key, value in payload.get("selected", {}).items()}
            ),
        )
        return
    ReplSkin("clash-verge", version=__version__).table(
        ["Group", "Selected"],
        [[key, _fmt(value)] for key, value in payload["selected"].items()],
        title="Current selection",
    )


@proxy.command("delay")
@click.argument("name", required=False)
@click.option("--url", default="http://www.gstatic.com/generate_204", show_default=True)
@click.option("--timeout", default=5000, show_default=True, help="Timeout in ms.")
@click.option("--group", is_flag=True, help="Treat NAME as a group and test all members.")
@click.pass_context
def proxy_delay(
    ctx: click.Context, name: str | None, url: str, timeout: int, group: bool
) -> None:
    """Measure latency of a node, or of every member of a group."""
    backend = _backend(ctx)
    try:
        payload = backend.proxy_delay(name, url, timeout, group)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    if ctx.obj["json"]:
        out(ctx, payload)
        return
    result = payload["result"]
    skin = ReplSkin("clash-verge", version=__version__)
    if isinstance(result, dict) and "delay" in result:
        out(ctx, payload, title="Latency", statuses={"Target": payload.get("target"), "Delay": f"{result['delay']} ms"})
        return
    rows = []
    if isinstance(result, dict):
        for key, value in result.items():
            if isinstance(value, dict):
                rows.append([key, _fmt(value.get("delay"))])
            else:
                rows.append([key, _fmt(value)])
    skin.table(["Node", "Delay (ms)"], rows, title=f"Latency ({payload.get('group') or payload.get('target')})")


@proxy.command("providers")
@click.pass_context
def proxy_providers(ctx: click.Context) -> None:
    """List proxy providers."""
    backend = _backend(ctx)
    try:
        payload = backend.proxy_providers()
    except KNOWN_ERRORS as exc:
        _fail(exc)
    if ctx.obj["json"]:
        out(ctx, payload)
        return
    ReplSkin("clash-verge", version=__version__).table(
        ["Provider", "Type", "Proxies", "Updated"],
        [
            [row["name"], row["type"] or "-", str(row["proxies"]), _fmt(row["updated_at"])]
            for row in payload["providers"]
        ],
        title=f"Proxy providers ({payload['count']})",
    )


@proxy.command("update-provider")
@click.argument("name")
@click.pass_context
def proxy_update_provider(ctx: click.Context, name: str) -> None:
    """Force-refresh a proxy provider."""
    backend = _backend(ctx)
    try:
        payload = backend.proxy_update_provider(name)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(ctx, payload, title="Provider", statuses={"Provider": payload["provider"], "Updated": "yes"})


@cli.group()
@json_option
def mode() -> None:
    """Routing mode (needs the core running)."""


@mode.command("get")
@click.pass_context
def mode_get(ctx: click.Context) -> None:
    """Show the current mode."""
    backend = _backend(ctx)
    try:
        payload = backend.mode_get()
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(ctx, payload, title="Mode", statuses={"Mode": payload["mode"]})


@mode.command("set")
@click.argument("value", type=click.Choice(["rule", "global", "direct"]))
@click.pass_context
def mode_set(ctx: click.Context, value: str) -> None:
    """Set the routing mode, live."""
    backend = _backend(ctx)
    try:
        payload = backend.mode_set(value)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(ctx, payload, title="Mode", statuses={"Mode": payload["mode"]})


@cli.group()
@json_option
def conn() -> None:
    """Active connections (needs the core running)."""


@conn.command("list")
@click.option("--limit", default=50, show_default=True, help="Max rows (0 = all).")
@click.pass_context
def conn_list(ctx: click.Context, limit: int) -> None:
    """List active connections."""
    backend = _backend(ctx)
    try:
        payload = backend.conn_list(limit)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    if ctx.obj["json"]:
        out(ctx, payload)
        return
    ReplSkin("clash-verge", version=__version__).table(
        ["ID", "Host", "Network", "Chain", "Rule", "Down", "Up"],
        [
            [
                row["id"] or "-",
                row["host"] or "-",
                row["network"] or "-",
                " → ".join(row["chains"]) or "-",
                f"{row['rule'] or ''}{':' + row['rule_payload'] if row['rule_payload'] else ''}" or "-",
                _fmt(row["download"]),
                _fmt(row["upload"]),
            ]
            for row in payload["connections"]
        ],
        title=f"Connections ({payload['count']})",
    )


@conn.command("close")
@click.argument("connection_id")
@click.pass_context
def conn_close(ctx: click.Context, connection_id: str) -> None:
    """Close one connection by id."""
    backend = _backend(ctx)
    try:
        payload = backend.conn_close(connection_id)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(ctx, payload, title="Connection", statuses={"Closed": payload["closed"]})


@conn.command("close-all")
@click.pass_context
def conn_close_all(ctx: click.Context) -> None:
    """Close every connection."""
    backend = _backend(ctx)
    try:
        payload = backend.conn_close_all()
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(ctx, payload, title="Connections", statuses={"Closed all": "yes"})


@cli.group()
@json_option
def rule() -> None:
    """The loaded rule set (needs the core running)."""


@rule.command("list")
@click.option("--limit", default=0, show_default=True, help="Max rows (0 = all).")
@click.pass_context
def rule_list(ctx: click.Context, limit: int) -> None:
    """List rules."""
    backend = _backend(ctx)
    try:
        payload = backend.rule_list()
    except KNOWN_ERRORS as exc:
        _fail(exc)
    rules = payload["rules"]
    if limit and limit > 0:
        rules = rules[:limit]
    if ctx.obj["json"]:
        out(ctx, {**payload, "rules": rules})
        return
    ReplSkin("clash-verge", version=__version__).table(
        ["Type", "Payload", "Proxy", "Count"],
        [
            [row["type"] or "-", row["payload"] or "-", row["proxy"] or "-", _fmt(row["size"])]
            for row in rules
        ],
        title=f"Rules ({len(rules)})",
    )


@cli.group()
@json_option
def core() -> None:
    """The mihomo core process."""


@core.command("status")
@click.pass_context
def core_status(ctx: click.Context) -> None:
    """GUI/core process state plus controller reachability."""
    backend = _backend(ctx)
    payload = backend.core_status()
    out(
        ctx,
        payload,
        title="Core",
        statuses={
            "GUI running": _fmt(payload["gui_running"]),
            "Core running": _fmt(payload["core_running"]),
            "GUI pids": _fmt(payload["gui_pids"]),
            "Core pids": _fmt(payload["core_pids"]),
            "Controller reachable": _fmt(payload["controller"].get("reachable")),
            "Runtime config": "present" if payload["runtime"]["available"] else "missing",
        },
    )


@core.command("version")
@click.pass_context
def core_version(ctx: click.Context) -> None:
    """Report the running core's version."""
    backend = _backend(ctx)
    try:
        payload = backend.core_version()
    except KNOWN_ERRORS as exc:
        _fail(exc)
    out(
        ctx,
        payload,
        title="Core version",
        statuses={
            "Version": _fmt(payload.get("version")),
            "Premium": _fmt(payload.get("premium")),
        },
    )


@core.command("configs")
@click.pass_context
def core_configs(ctx: click.Context) -> None:
    """Dump the live core configuration."""
    backend = _backend(ctx)
    try:
        payload = backend.core_configs()
    except KNOWN_ERRORS as exc:
        _fail(exc)
    if ctx.obj["json"]:
        out(ctx, payload)
        return
    ReplSkin("clash-verge", version=__version__).status_block(
        {key: _fmt(value) for key, value in payload.items() if not isinstance(value, (dict, list))},
        title="Live core config",
    )


@core.command("launch")
@click.pass_context
def core_launch(ctx: click.Context) -> None:
    """Start the Clash Verge GUI."""
    try:
        payload = process_mod.launch()
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    out(ctx, payload, title="Launch", statuses={"Executable": payload["executable"]})


# ===========================================================================
# logs
# ===========================================================================
@cli.group("log")
@json_option
def log_group() -> None:
    """Clash Verge log files."""


@log_group.command("files")
@click.pass_context
def log_files(ctx: click.Context) -> None:
    """List log files, newest first."""
    backend = _backend(ctx)
    files = process_mod.log_files(backend.paths.logs_dir)
    out(
        ctx,
        {"files": [str(entry) for entry in files]},
        title="Log files",
        statuses={"Count": str(len(files))},
    )
    if not ctx.obj["json"] and files:
        ReplSkin("clash-verge", version=__version__).table(
            ["File", "Bytes"],
            [[entry.name, str(entry.stat().st_size)] for entry in files[:20]],
        )


@log_group.command("tail")
@click.option("--lines", default=40, show_default=True, help="Number of lines.")
@click.option("--file", "file", default=None, help="Specific log file.")
@click.pass_context
def log_tail(ctx: click.Context, lines: int, file: str | None) -> None:
    """Print the tail of a log file."""
    backend = _backend(ctx)
    try:
        payload = backend.logs(lines=lines, file=file)
    except KNOWN_ERRORS as exc:
        _fail(exc)
    if ctx.obj["json"]:
        out(ctx, payload)
        return
    if not payload["exists"]:
        ReplSkin("clash-verge", version=__version__).warning(
            f"log file not found: {payload['file']}"
        )
        return
    click.echo("\n".join(payload["lines"]))


# ===========================================================================
# session
# ===========================================================================
@cli.group()
@json_option
def session() -> None:
    """Session state, undo history and the target directory."""


@session.command("show")
@click.pass_context
def session_show(ctx: click.Context) -> None:
    """Show the current session."""
    backend = _backend(ctx)
    state = backend.session.session
    payload = {
        "session_file": str(backend.session.path),
        "home_dir": state.home_dir,
        "resolved_home": str(backend.paths.home),
        "controller_url": state.controller_url,
        "secret_set": bool(state.secret),
        "undo_depth": len(state.undo_stack),
        "redo_depth": len(state.redo_stack),
        "updated_at": state.updated_at,
    }
    out(ctx, payload, title="Session", statuses={key: _fmt(value) for key, value in payload.items()})


@session.command("home")
@click.argument("path", required=False, type=click.Path(exists=True))
@click.pass_context
def session_home(ctx: click.Context, path: str | None) -> None:
    """Show or set the Clash Verge home directory for this session."""
    backend = _backend(ctx)
    if path:
        from .core.paths import ClashVergePaths

        resolved = ClashVergePaths(path)
        backend.session.session.home_dir = str(path)
        backend.session.save()
        payload = {"home_dir": str(path), "layout": resolved.layout()}
    else:
        payload = {
            "home_dir": backend.session.session.home_dir,
            "resolved": str(backend.paths.home),
        }
    out(
        ctx,
        payload,
        title="Session home",
        statuses={key: _fmt(value) for key, value in payload.items()},
    )


@session.command("history")
@click.option("--limit", default=20, show_default=True)
@click.pass_context
def session_history(ctx: click.Context, limit: int) -> None:
    """Show recorded changes, newest first."""
    backend = _backend(ctx)
    rows = backend.session.history(limit=limit)
    if ctx.obj["json"]:
        out(ctx, rows)
        return
    if not rows:
        ReplSkin("clash-verge", version=__version__).info("no recorded changes")
        return
    ReplSkin("clash-verge", version=__version__).table(
        ["ID", "When", "Change", "Files"],
        [[str(row["id"]), row["ts"] or "-", row["label"], _fmt(row["files"])] for row in rows],
        title="Change history",
    )


@session.command("undo")
@click.pass_context
def session_undo(ctx: click.Context) -> None:
    """Revert the most recent recorded change."""
    backend = _backend(ctx)
    entry = backend.session.undo()
    if entry is None:
        out(ctx, {"undone": None}, title="Undo", statuses={"Result": "nothing to undo"})
        return
    out(
        ctx,
        {"undone": entry},
        title="Undo",
        statuses={
            "Reverted": entry.get("label"),
            "Files": _fmt(list((entry.get("files") or {}).keys())),
        },
    )


@session.command("redo")
@click.pass_context
def session_redo(ctx: click.Context) -> None:
    """Re-apply the most recently undone change."""
    backend = _backend(ctx)
    entry = backend.session.redo()
    if entry is None:
        out(ctx, {"redone": None}, title="Redo", statuses={"Result": "nothing to redo"})
        return
    out(
        ctx,
        {"redone": entry},
        title="Redo",
        statuses={
            "Re-applied": entry.get("label"),
            "Files": _fmt(list((entry.get("files") or {}).keys())),
        },
    )


# ===========================================================================
# repl
# ===========================================================================
@cli.command()
@click.pass_context
def repl(ctx: click.Context) -> None:
    """Start an interactive session."""
    from .core.paths import resolve_home_dir

    skin = ReplSkin("clash-verge", version=__version__)
    skin.print_banner()

    try:
        home = resolve_home_dir(ctx.obj.get("home"))
        skin.info(f"Clash Verge home: {home}")
    except ClashVergeNotFound as exc:
        skin.warning(str(exc).splitlines()[0])

    pt_session = None
    try:
        pt_session = skin.create_prompt_session()
    except Exception:  # noqa: BLE001 - no console buffer under some CI shells
        pt_session = None

    skin.help(
        {
            "env info|paths|doctor": "Environment and diagnostics",
            "profile list|show|import|select|update|delete": "Profile management",
            "verge list|get|set|unset": "verge.yaml settings",
            "clash list|get|set|unset": "config.yaml core settings",
            "controller status|enable|disable": "Mihomo HTTP controller",
            "proxy groups|nodes|select|delay|providers": "Live proxy control",
            "mode get|set": "rule | global | direct",
            "conn list|close|close-all": "Active connections",
            "rule list": "Loaded rule set",
            "core status|version|configs|launch": "Core process",
            "session history|undo|redo|home": "Session and undo stack",
            "help": "Show this help",
            "quit / exit": "Leave the REPL",
        }
    )

    while True:
        try:
            if pt_session is None:
                line = input("clash-verge> ").strip()
            else:
                line = skin.get_input(pt_session, project_name="clash-verge").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo()
            skin.print_goodbye()
            return

        if not line:
            continue
        if line.lower() in ("quit", "exit", "q"):
            skin.print_goodbye()
            return
        if line.lower() in ("help", "?"):
            skin.help(
                {
                    "env doctor": "Run health checks",
                    "profile list": "List profiles",
                    "controller enable": "Turn on the HTTP controller",
                    "proxy groups": "List proxy groups",
                    "session undo": "Revert the last change",
                    "quit": "Leave the REPL",
                }
            )
            continue

        try:
            argv = shlex.split(line)
        except ValueError as exc:
            skin.error(f"parse error: {exc}")
            continue

        try:
            _invoke(
                argv,
                obj={"json": ctx.obj.get("json", False), "home": ctx.obj.get("home")},
                parent=ctx,
            )
        except click.exceptions.Exit:
            continue
        except click.ClickException as exc:
            skin.error(exc.format_message())
        except SystemExit:
            continue
        except Exception as exc:  # noqa: BLE001
            skin.error(f"{type(exc).__name__}: {exc}")


PROG_NAME = "cli-anything-clash-verge"


def _is_misplaced_json(exc: click.UsageError) -> bool:
    """True when a parse failed *only* because ``--json`` sat in a wrong slot."""
    message = (exc.format_message() or "").lower()
    return "no such option" in message and "--json" in message


def _invoke(
    argv: list[str],
    obj: dict[str, Any] | None = None,
    parent: click.Context | None = None,
) -> None:
    """Run the CLI, accepting ``--json`` in any position.

    Click only recognises a group option directly after the group name, so
    ``profile list --json`` is a usage error. Instead of making callers
    remember that, parse first and — only when the failure is caused by a
    misplaced ``--json`` — hoist it to the front and retry. A literal
    ``--json`` passed as an option *value* therefore still works, because the
    first parse succeeds and no retry happens.
    """
    state = dict(obj or {})
    try:
        cli.main(
            args=list(argv),
            obj=state,
            parent=parent,
            prog_name=PROG_NAME,
            standalone_mode=False,
        )
        return
    except click.UsageError as exc:
        if "--json" not in argv or not _is_misplaced_json(exc):
            raise

    state["json"] = True
    cleaned = [arg for arg in argv if arg != "--json"]
    cli.main(
        args=cleaned,
        obj=state,
        parent=parent,
        prog_name=PROG_NAME,
        standalone_mode=False,
    )


def ensure_utf8_output() -> None:
    """Never let a legacy console encoding turn a glyph into a traceback.

    Windows consoles default to a locale code page (cp936 on Chinese systems)
    that cannot encode the box-drawing and warning glyphs the REPL skin uses.
    Without this, ``env doctor`` — the command every new user is told to run
    first — dies with ``UnicodeEncodeError: 'gbk' codec`` on exactly those
    machines. Reconfiguring to UTF-8 keeps output correct on modern terminals
    and degrades to replacement characters instead of crashing on legacy ones.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - exotic stream types
            continue


def main() -> None:
    """Console-script entry point."""
    ensure_utf8_output()
    try:
        _invoke(sys.argv[1:])
    except click.exceptions.Exit as exc:
        raise SystemExit(exc.exit_code) from exc
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from exc
    except click.Abort:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
