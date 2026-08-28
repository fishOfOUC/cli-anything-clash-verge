"""High-level facade the CLI commands talk to.

Three backends, one object:

==============  ==========================================================
``paths``       where Clash Verge keeps its state
``verge``/      native YAML state — the "project file" of this harness
``clash``/
``profiles``/
``controller``  live Mihomo External Controller (needs the core running)
``process``     whether the GUI/core are running
==============  ===========================================================

The write race
--------------
Clash Verge holds its configuration in memory and rewrites ``verge.yaml``,
``config.yaml`` and ``profiles.yaml`` whenever the user changes something. There
is no file watcher, so an external edit made while the app runs is invisible at
best and silently reverted at worst.

Every mutating call therefore goes through :meth:`_mutate`, which refuses while
the GUI is running unless ``force=True`` (CLI: ``--force``). Live changes that
need to take effect immediately should go through the controller instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..core import process as process_mod
from ..core import subscription as subscription_mod
from ..core.clash import ClashConfig, RuntimeConfig
from ..core.controller import MihomoController
from ..core.paths import (
    ClashVergePaths,
    candidate_home_dirs,
    resolve_home_dir,
)
from ..core.profiles import (
    COMPANION_TYPES,
    ITEM_TYPES,
    Profiles,
    ProfileError,
    file_name_for,
    new_uid,
)
from ..core.session import ChangeSet, SessionManager
from ..core.verge import SCHEMA as VERGE_SCHEMA
from ..core.verge import VergeConfig
from ..core.yamlio import YamlError
from .templates import COMPANION_TEMPLATES


class BackendError(RuntimeError):
    """Raised for any user-facing backend failure."""


class AppRunning(BackendError):
    """Raised when a mutation is attempted while Clash Verge is running."""


class ControllerNotReady(BackendError):
    """Raised when a live operation is requested but the controller is off."""


class ClashVergeBackend:
    """Everything the CLI needs, with the running-app race handled centrally."""

    def __init__(
        self,
        home_dir: str | Path | None = None,
        session: SessionManager | None = None,
    ):
        self.session = session or SessionManager()
        target = home_dir or self.session.session.home_dir
        self.paths = ClashVergePaths(resolve_home_dir(target))
        self.verge = VergeConfig(self.paths)
        self.clash = ClashConfig(self.paths)
        self.profiles = Profiles(self.paths)
        self.runtime = RuntimeConfig(self.paths)
        self._controller: MihomoController | None = None

    # -- controller ------------------------------------------------------
    def controller(self, use_session_override: bool = True) -> MihomoController:
        """Build (and memoize) the Mihomo controller client."""
        if self._controller is not None:
            return self._controller
        session_state = self.session.session
        url = None
        secret = None
        if use_session_override:
            url = session_state.controller_url
            secret = session_state.secret
        if not url:
            url = self.clash.controller_url()
        if not secret:
            secret = self.clash.secret()
        self._controller = MihomoController(url, secret)
        return self._controller

    def invalidate_controller(self) -> None:
        """Drop the memoized client (after the URL or secret changes)."""
        self._controller = None

    def require_controller(self) -> MihomoController:
        """Return a controller, or explain precisely why one is unavailable."""
        client = self.controller()
        state = self.controller_state()
        if not state["enabled"]:
            raise ControllerNotReady(
                "the HTTP external controller is disabled in verge.yaml.\n"
                "Enable it with: clash-verge controller enable\n"
                "Then restart the Clash Verge core (or the app) for it to take effect."
            )
        probe = client.probe()
        if not probe["reachable"]:
            raise ControllerNotReady(
                f"the mihomo controller at {state['url']} is not responding "
                f"({probe.get('error')}).\n"
                "Is Clash Verge running? Check with: clash-verge core status"
            )
        return client

    def controller_state(self) -> dict[str, Any]:
        """Everything known about the controller's availability."""
        enabled_raw = self.verge.get("enable_external_controller")[1]
        enabled = bool(enabled_raw)
        address = self.clash.get("external-controller")[1]
        url = self.clash.controller_url()
        session_state = self.session.session
        if session_state.controller_url:
            url = session_state.controller_url
        secret = session_state.secret or self.clash.secret()
        return {
            "enabled": enabled,
            "url": url,
            "secret_set": bool(secret),
            "secret_is_default": secret == "set-your-secret",
            "address": address,
            "override_url": session_state.controller_url,
        }

    # -- process ---------------------------------------------------------
    def process_status(self) -> dict[str, Any]:
        return process_mod.status()

    def gui_running(self) -> bool:
        return process_mod.gui_running()

    def assert_app_closed(self, force: bool = False) -> None:
        """Refuse config mutations while the GUI is running, unless ``force``."""
        if force:
            return
        if self.gui_running():
            raise AppRunning(
                "Clash Verge is running and owns its configuration files.\n"
                "Editing them now risks your change being silently reverted by the app.\n"
                "Either: close Clash Verge and retry,\n"
                "     or: use the live commands (proxy/mode/conn) which go through the controller,\n"
                "     or: pass --force to write anyway."
            )

    # -- mutation plumbing -----------------------------------------------
    def _mutate(
        self,
        label: str,
        operation: Callable[[ChangeSet], Any],
        force: bool = False,
    ) -> tuple[Any, dict]:
        """Run a state mutation, record it for undo, then persist."""
        self.assert_app_closed(force=force)
        changes = ChangeSet(label)
        try:
            result = operation(changes)
        except (ProfileError, YamlError, BackendError):
            raise
        except Exception as exc:  # noqa: BLE001 - normalize anything unexpected
            raise BackendError(f"{label} failed: {exc}") from exc
        entry = self.session.commit(changes)
        return result, entry

    # ====================================================================
    # env
    # ====================================================================
    def env_paths(self) -> dict[str, Any]:
        """Every candidate home directory and which one is in use."""
        candidates = []
        for label, path in candidate_home_dirs():
            candidates.append(
                {"label": label, "path": str(path), "exists": path.is_dir()}
            )
        return {
            "in_use": str(self.paths.home),
            "layout": self.paths.layout(),
            "candidates": candidates,
            "files": self.paths.describe(),
            "present": self.paths.exists_map(),
        }

    def env_info(self) -> dict[str, Any]:
        """The useful summary: state, ports, controller, process."""
        current = self.profiles.current()
        runtime = self.runtime.summary()
        controller = self.controller_state()
        process_state = self.process_status()
        controller_state: dict[str, Any] = {"enabled": controller["enabled"]}
        if controller["enabled"]:
            controller_state.update(self.controller().probe())
        else:
            controller_state.update({"reachable": False, "url": controller["url"]})
        return {
            "home": str(self.paths.home),
            "layout": self.paths.layout(),
            "app": {
                "version": None,
                "gui_running": process_state["gui_running"],
                "core_running": process_state["core_running"],
                "executable": process_state["gui_executable"],
            },
            "profile": {
                "current_uid": (current or {}).get("uid"),
                "current_name": (current or {}).get("name"),
                "current_type": (current or {}).get("type"),
                "total_items": len(self.profiles.items()),
                "main_profiles": len(self.profiles.main_items()),
            },
            "runtime": runtime,
            "controller": controller_state,
        }

    def env_doctor(self) -> list[dict[str, Any]]:
        """Health checks. Returns ``[{level, check, detail}]``."""
        checks: list[dict[str, Any]] = []

        def add(level: str, check: str, detail: str) -> None:
            checks.append({"level": level, "check": check, "detail": detail})

        home = self.paths.home
        if home.is_dir():
            add("ok", "home directory", f"found at {home} ({self.paths.layout()})")
        else:
            add("fail", "home directory", f"missing: {home}")

        for label, path in (
            ("verge.yaml", self.paths.verge_yaml),
            ("config.yaml", self.paths.clash_yaml),
            ("profiles.yaml", self.paths.profiles_yaml),
        ):
            if path.is_file():
                add("ok", label, f"present ({path.stat().st_size} bytes)")
            else:
                add("fail", label, f"missing: {path}")

        try:
            problems = self.profiles.validate()
            if problems:
                for problem in problems:
                    add("warn", "profiles.yaml", problem)
            else:
                add(
                    "ok",
                    "profiles.yaml",
                    f"{len(self.profiles.items())} items, "
                    f"{len(self.profiles.main_items())} selectable",
                )
        except YamlError as exc:
            add("fail", "profiles.yaml", str(exc))

        verge_problems = self.verge.validate()
        clash_problems = self.clash.validate()
        if verge_problems or clash_problems:
            for problem in verge_problems:
                add("warn", "verge.yaml", problem)
            for problem in clash_problems:
                add("warn", "config.yaml", problem)
        else:
            add("ok", "config typing", "verge.yaml and config.yaml types look valid")

        unknown = self.verge.unknown_keys() + self.clash.unknown_keys()
        if unknown:
            add(
                "info",
                "unknown keys",
                "preserved but not understood by this CLI: " + ", ".join(sorted(set(unknown))[:8]),
            )

        runtime = self.runtime.summary()
        if runtime["available"]:
            add(
                "ok",
                "runtime config",
                f"clash-verge.yaml loaded: mode={runtime['mode']}, "
                f"mixed-port={runtime['mixed_port']}, {runtime['proxy_count']} proxies",
            )
        else:
            add("warn", "runtime config", "no clash-verge.yaml — the core has not generated one yet")

        process_state = self.process_status()
        if process_state["gui_running"]:
            add("warn", "app running", "Clash Verge is running — config writes need --force")
        else:
            add("ok", "app running", "Clash Verge is not running — config writes are safe")
        if process_state["core_running"]:
            add("ok", "core running", f"verge-mihomo pids={process_state['core_pids']}")
        else:
            add("warn", "core running", "mihomo sidecar not detected")

        controller = self.controller_state()
        if not controller["enabled"]:
            add(
                "info",
                "controller",
                "HTTP external controller disabled — live commands unavailable. "
                "Run: clash-verge controller enable",
            )
        else:
            probe = self.controller().probe()
            if probe["reachable"]:
                add("ok", "controller", f"reachable at {controller['url']} ({probe.get('version')})")
            else:
                add("warn", "controller", f"enabled but unreachable at {controller['url']}")
        return checks

    # ====================================================================
    # verge.yaml
    # ====================================================================
    def verge_list(self, only_set: bool = False) -> list[dict[str, Any]]:
        data = self.verge.load()
        rows = []
        for key, (kind, default, doc) in VERGE_SCHEMA.items():
            is_set = key in data
            if only_set and not is_set:
                continue
            rows.append(
                {
                    "key": key,
                    "value": data[key] if is_set else default,
                    "set": is_set,
                    "type": kind,
                    "doc": doc,
                }
            )
        return rows

    def verge_get(self, key: str) -> dict[str, Any]:
        if key not in VERGE_SCHEMA:
            raise BackendError(
                f"unknown verge key '{key}'. Run `clash-verge verge list` for valid keys."
            )
        is_set, value = self.verge.get(key)
        return {
            "key": key,
            "value": value,
            "set": is_set,
            "type": VERGE_SCHEMA[key][0],
            "doc": VERGE_SCHEMA[key][2],
        }

    def verge_set(self, key: str, raw: str, force: bool = False) -> tuple[dict, dict]:
        if key not in VERGE_SCHEMA:
            raise BackendError(
                f"unknown verge key '{key}'. Run `clash-verge verge list` for valid keys."
            )

        def op(changes: ChangeSet) -> dict:
            changes.track(self.paths.verge_yaml)
            old, new = self.verge.set(key, raw)
            if key == "enable_external_controller" or key == "external-controller":
                self.invalidate_controller()
            return {"key": key, "old": old, "new": new}

        return self._mutate(f"verge set {key} {raw}", op, force=force)

    def verge_unset(self, key: str, force: bool = False) -> tuple[dict, dict]:
        def op(changes: ChangeSet) -> dict:
            changes.track(self.paths.verge_yaml)
            removed, old = self.verge.unset(key)
            return {"key": key, "removed": removed, "old": old}

        return self._mutate(f"verge unset {key}", op, force=force)

    # ====================================================================
    # config.yaml
    # ====================================================================
    def clash_list(self) -> list[dict[str, Any]]:
        data = self.clash.load()
        rows = []
        for key in (
            "mixed-port", "socks-port", "port", "redir-port", "tproxy-port",
            "mode", "allow-lan", "ipv6", "log-level", "unified-delay",
            "external-controller", "secret",
        ):
            from ..core.clash import DEFAULT_TEMPLATE

            is_set = key in data
            rows.append(
                {
                    "key": key,
                    "value": data[key] if is_set else DEFAULT_TEMPLATE.get(key),
                    "set": is_set,
                }
            )
        return rows

    def clash_get(self, key: str) -> dict[str, Any]:
        """Any key is valid for a get: ``config.yaml`` is a free-form mapping."""
        is_set, value = self.clash.get(key)
        return {"key": key, "value": value, "set": is_set}

    def clash_set(self, key: str, raw: str, force: bool = False) -> tuple[dict, dict]:
        def op(changes: ChangeSet) -> dict:
            changes.track(self.paths.clash_yaml)
            old, new = self.clash.set(key, raw)
            if key in ("external-controller", "secret"):
                self.invalidate_controller()
            return {"key": key, "old": old, "new": new}

        return self._mutate(f"clash set {key} {raw}", op, force=force)

    def clash_unset(self, key: str, force: bool = False) -> tuple[dict, dict]:
        def op(changes: ChangeSet) -> dict:
            changes.track(self.paths.clash_yaml)
            removed, old = self.clash.unset(key)
            return {"key": key, "removed": removed, "old": old}

        return self._mutate(f"clash unset {key}", op, force=force)

    # ====================================================================
    # profiles
    # ====================================================================
    def profile_list(self, all_types: bool = False) -> list[dict[str, Any]]:
        data = self.profiles.load()
        current_uid = data.get("current")
        items = self.profiles.items() if all_types else self.profiles.main_items()
        rows = []
        for item in items:
            path = self.profiles.file_path(item)
            rows.append(
                {
                    "uid": item.get("uid"),
                    "name": item.get("name"),
                    "type": item.get("type"),
                    "current": item.get("uid") == current_uid,
                    "url": item.get("url"),
                    "updated": item.get("updated"),
                    "file": item.get("file"),
                    "file_exists": bool(path) and path.is_file(),
                    "size": path.stat().st_size if path and path.is_file() else 0,
                    "desc": item.get("desc"),
                    "home": item.get("home"),
                }
            )
        return rows

    def profile_show(self, selector: str) -> dict[str, Any]:
        item = self.profiles.resolve(selector)
        path = self.profiles.file_path(item)
        payload: dict[str, Any] = {"item": item, "file": str(path) if path else None}
        if path and path.is_file():
            payload["size"] = path.stat().st_size
            payload["mtime"] = int(path.stat().st_mtime)
            if item.get("type") in ("remote", "local"):
                try:
                    summary = subscription_mod.summarize_payload(
                        subscription_mod.validate_payload(path.read_text(encoding="utf-8"))
                    )
                    payload["contents"] = summary
                except (subscription_mod.SubscriptionError, OSError) as exc:
                    payload["contents_error"] = str(exc).splitlines()[0]
        payload["companions"] = [
            {"uid": c.get("uid"), "type": c.get("type"), "name": c.get("name")}
            for c in self.profiles.companions(item.get("uid", ""))
        ]
        return payload

    def profile_import(
        self,
        url: str | None = None,
        path: str | Path | None = None,
        name: str | None = None,
        *,
        itype: str = "remote",
        user_agent: str | None = None,
        timeout: int = subscription_mod.DEFAULT_TIMEOUT,
        with_proxy: bool = False,
        insecure: bool = False,
        force: bool = False,
    ) -> tuple[dict, dict]:
        """Import a remote subscription or local YAML file as a new profile."""
        if itype not in ("remote", "local"):
            raise BackendError(
                f"cannot import type '{itype}'. Use 'remote' (URL) or 'local' (file)."
            )
        if itype == "remote" and not url:
            raise BackendError("importing a remote profile requires --url.")
        if itype == "local" and not path:
            raise BackendError("importing a local profile requires --path.")

        def op(changes: ChangeSet) -> dict:
            if itype == "remote":
                fetched = subscription_mod.fetch(
                    url,
                    user_agent=user_agent,
                    timeout=timeout,
                    with_proxy=with_proxy,
                    insecure=insecure,
                )
            else:
                fetched = subscription_mod.read_local(path)

            uid = new_uid(itype)
            file_name = file_name_for(uid, itype)
            target = self.paths.profile_file(file_name)
            self.paths.profiles_dir.mkdir(parents=True, exist_ok=True)

            changes.track(target)
            changes.track(self.paths.profiles_yaml)
            target.write_text(fetched["text"], encoding="utf-8")

            item: dict[str, Any] = {
                "uid": uid,
                "type": itype,
                "name": name or fetched.get("suggested_name") or (url or Path(path).stem),
                "file": file_name,
                "updated": Profiles.stamp_updated(),
            }
            if itype == "remote":
                item["url"] = url
                item["extra"] = fetched["extra"]
                if fetched.get("home"):
                    item["home"] = fetched["home"]
                if fetched.get("update_interval"):
                    item.setdefault("option", {})["update_interval"] = fetched["update_interval"]
                if user_agent or with_proxy or insecure or timeout != subscription_mod.DEFAULT_TIMEOUT:
                    option = item.setdefault("option", {})
                    if user_agent:
                        option["user_agent"] = user_agent
                    if with_proxy:
                        option["with_proxy"] = True
                    if insecure:
                        option["danger_accept_invalid_certs"] = True
                    if timeout != subscription_mod.DEFAULT_TIMEOUT:
                        option["timeout_seconds"] = timeout

            self.profiles.add(item)

            companions = []
            for companion_type in COMPANION_TYPES:
                companion_uid = new_uid(companion_type)
                companion_file = file_name_for(companion_uid, companion_type)
                companion_path = self.paths.profile_file(companion_file)
                changes.track(companion_path)
                companion_path.write_text(COMPANION_TEMPLATES[companion_type], encoding="utf-8")
                companion_item = {
                    "uid": companion_uid,
                    "type": companion_type,
                    "name": f"{uid} {companion_type}",
                    "file": companion_file,
                }
                self.profiles.add(companion_item)
                changes.track(self.paths.profiles_yaml)
                companions.append(companion_uid)

            data = self.profiles.load()
            if not data.get("current"):
                data["current"] = uid
                self.profiles.save(data)

            return {
                "uid": uid,
                "name": item["name"],
                "type": itype,
                "file": str(target),
                "summary": fetched["summary"],
                "extra": fetched.get("extra"),
                "companions": companions,
                "selected": data.get("current") == uid,
            }

        label = f"profile import {url or path}"
        return self._mutate(label, op, force=force)

    def profile_create(
        self,
        name: str,
        force: bool = False,
    ) -> tuple[dict, dict]:
        """Create an empty local profile from Clash Verge's own template."""
        if not name:
            raise BackendError("profile name is required.")

        def op(changes: ChangeSet) -> dict:
            from .templates import ITEM_LOCAL

            uid = new_uid("local")
            file_name = file_name_for(uid, "local")
            target = self.paths.profile_file(file_name)
            self.paths.profiles_dir.mkdir(parents=True, exist_ok=True)
            changes.track(target)
            changes.track(self.paths.profiles_yaml)
            target.write_text(ITEM_LOCAL, encoding="utf-8")

            item = {
                "uid": uid,
                "type": "local",
                "name": name,
                "file": file_name,
                "updated": Profiles.stamp_updated(),
            }
            self.profiles.add(item)

            companions = []
            for companion_type in COMPANION_TYPES:
                companion_uid = new_uid(companion_type)
                companion_file = file_name_for(companion_uid, companion_type)
                companion_path = self.paths.profile_file(companion_file)
                changes.track(companion_path)
                companion_path.write_text(COMPANION_TEMPLATES[companion_type], encoding="utf-8")
                self.profiles.add(
                    {
                        "uid": companion_uid,
                        "type": companion_type,
                        "name": f"{uid} {companion_type}",
                        "file": companion_file,
                    }
                )
                companions.append(companion_uid)

            data = self.profiles.load()
            if not data.get("current"):
                data["current"] = uid
                self.profiles.save(data)

            return {
                "uid": uid,
                "name": name,
                "type": "local",
                "file": str(target),
                "companions": companions,
            }

        return self._mutate(f"profile create {name}", op, force=force)

    def profile_update(self, selector: str, force: bool = False) -> tuple[dict, dict]:
        """Re-download a remote profile and replace its data file."""
        item = self.profiles.resolve(selector)
        if item.get("type") != "remote":
            raise BackendError(
                f"'{selector}' is a '{item.get('type')}' profile, not a remote subscription. "
                "Only remote profiles can be updated."
            )
        option = item.get("option") or {}
        url = item.get("url")
        if not url:
            raise BackendError(f"profile '{selector}' has no url.")

        def op(changes: ChangeSet) -> dict:
            fetched = subscription_mod.fetch(
                url,
                user_agent=option.get("user_agent"),
                timeout=option.get("timeout_seconds") or subscription_mod.DEFAULT_TIMEOUT,
                with_proxy=bool(option.get("with_proxy")),
                insecure=bool(option.get("danger_accept_invalid_certs")),
            )
            target = self.paths.profile_file(item["file"])
            changes.track(target)
            changes.track(self.paths.profiles_yaml)
            target.write_text(fetched["text"], encoding="utf-8")

            patch = {"updated": Profiles.stamp_updated()}
            if fetched.get("extra"):
                patch["extra"] = fetched["extra"]
            if fetched.get("home"):
                patch["home"] = fetched["home"]
            if fetched.get("update_interval"):
                patch.setdefault("option", dict(option))
                patch["option"]["update_interval"] = fetched["update_interval"]
            self.profiles.update_item(item["uid"], patch)
            return {
                "uid": item["uid"],
                "name": item.get("name"),
                "url": url,
                "summary": fetched["summary"],
                "extra": fetched.get("extra"),
            }

        return self._mutate(f"profile update {selector}", op, force=force)

    def profile_delete(self, selector: str, force: bool = False) -> tuple[dict, dict]:
        item = self.profiles.resolve(selector)
        uid = item["uid"]
        companions = self.profiles.companions(uid)

        def op(changes: ChangeSet) -> dict:
            for companion in companions:
                companion_path = self.profiles.file_path(companion)
                if companion_path:
                    changes.track(companion_path)
            changes.track(self.paths.profiles_yaml)
            target_path = self.profiles.file_path(item)
            if target_path:
                changes.track(target_path)

            removed = self.profiles.remove(uid)
            for companion in companions:
                self.profiles.remove(companion["uid"])
                companion_path = self.profiles.file_path(companion)
                if companion_path and companion_path.is_file():
                    companion_path.unlink()

            return {
                "uid": uid,
                "name": item.get("name"),
                "removed_files": removed["removed_files"],
                "companions_removed": [c["uid"] for c in companions],
            }

        return self._mutate(f"profile delete {selector}", op, force=force)

    def profile_select(self, selector: str, force: bool = False) -> tuple[dict, dict]:
        item = self.profiles.resolve(selector)

        def op(changes: ChangeSet) -> dict:
            changes.track(self.paths.profiles_yaml)
            return self.profiles.set_current(item["uid"])

        return self._mutate(f"profile select {selector}", op, force=force)

    def profile_rename(
        self, selector: str, name: str, force: bool = False
    ) -> tuple[dict, dict]:
        item = self.profiles.resolve(selector)
        if not name.strip():
            raise BackendError("new name must not be empty.")

        def op(changes: ChangeSet) -> dict:
            changes.track(self.paths.profiles_yaml)
            self.profiles.update_item(item["uid"], {"name": name})
            return {"uid": item["uid"], "old": item.get("name"), "new": name}

        return self._mutate(f"profile rename {selector} {name}", op, force=force)

    def profile_export(self, selector: str, dest: str | Path) -> Path:
        """Copy a profile's data file out of the profiles directory."""
        item = self.profiles.resolve(selector)
        source = self.profiles.file_path(item)
        if not source or not source.is_file():
            raise BackendError(f"profile '{selector}' has no data file on disk.")
        target = Path(dest)
        if target.is_dir():
            target = target / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        return target

    def profile_file_read(self, selector: str) -> str:
        """Raw contents of a profile's data file."""
        item = self.profiles.resolve(selector)
        path = self.profiles.file_path(item)
        if not path or not path.is_file():
            raise BackendError(f"profile '{selector}' has no data file on disk.")
        return path.read_text(encoding="utf-8")

    def profile_file_write(
        self, selector: str, source: str | Path, force: bool = False
    ) -> tuple[dict, dict]:
        """Replace a profile's data file, validating it first."""
        item = self.profiles.resolve(selector)
        target = self.profiles.file_path(item)
        if not target:
            raise BackendError(f"profile '{selector}' has no 'file' field.")
        payload = subscription_mod.read_local(source)

        def op(changes: ChangeSet) -> dict:
            changes.track(target)
            changes.track(self.paths.profiles_yaml)
            target.write_text(payload["text"], encoding="utf-8")
            self.profiles.update_item(item["uid"], {"updated": Profiles.stamp_updated()})
            return {
                "uid": item["uid"],
                "file": str(target),
                "summary": payload["summary"],
            }

        return self._mutate(f"profile file write {selector}", op, force=force)

    # ====================================================================
    # controller
    # ====================================================================
    def controller_enable(self, force: bool = False) -> tuple[dict, dict]:
        def op(changes: ChangeSet) -> dict:
            changes.track(self.paths.verge_yaml)
            old, new = self.verge.set("enable_external_controller", "true")
            self.invalidate_controller()
            return {"key": "enable_external_controller", "old": old, "new": new}

        return self._mutate("controller enable", op, force=force)

    def controller_disable(self, force: bool = False) -> tuple[dict, dict]:
        def op(changes: ChangeSet) -> dict:
            changes.track(self.paths.verge_yaml)
            old, new = self.verge.set("enable_external_controller", "false")
            self.invalidate_controller()
            return {"key": "enable_external_controller", "old": old, "new": new}

        return self._mutate("controller disable", op, force=force)

    def controller_status(self) -> dict[str, Any]:
        state = self.controller_state()
        probe = self.controller().probe() if state["enabled"] else {"reachable": False}
        payload = dict(state)
        payload["probe"] = probe
        payload["process"] = self.process_status()
        return payload

    # ====================================================================
    # live (controller-backed) operations
    # ====================================================================
    def proxy_groups(self) -> dict[str, Any]:
        client = self.require_controller()
        groups = client.groups()
        return {
            "count": len(groups),
            "groups": [
                {
                    "name": name,
                    "type": info.get("type"),
                    "now": info.get("now"),
                    "members": len(info.get("all") or []),
                }
                for name, info in groups.items()
            ],
        }

    def proxy_nodes(self, group: str | None) -> dict[str, Any]:
        client = self.require_controller()
        nodes = client.nodes(group)
        return {
            "group": group,
            "count": len(nodes),
            "nodes": [
                {
                    "name": node.get("name"),
                    "type": node.get("type"),
                    "alive": node.get("alive"),
                    "delay": node.get("delay"),
                    "history": (node.get("history") or [{}])[-1:],
                }
                for node in nodes
            ],
        }

    def proxy_select(self, group: str, node: str) -> dict[str, Any]:
        client = self.require_controller()
        client.select(group, node)
        return {"group": group, "node": node, "now": client.current_of(group)}

    def proxy_current(self, group: str | None) -> dict[str, Any]:
        client = self.require_controller()
        if group:
            return {"group": group, "now": client.current_of(group)}
        groups = client.groups()
        return {
            "count": len(groups),
            "selected": {
                name: info.get("now") for name, info in groups.items()
            },
        }

    def proxy_delay(
        self, name: str | None, url: str, timeout: int, group: bool
    ) -> dict[str, Any]:
        client = self.require_controller()
        if group:
            if not name:
                raise BackendError("--group requires a group name.")
            result = client.group_delay(name, url, timeout)
            return {"group": name, "url": url, "result": result}
        if name:
            result = client.delay(name, url, timeout)
            return {"target": name, "url": url, "result": result}
        return {"url": url, "result": client.group_delay("GLOBAL", url, timeout)}

    def proxy_providers(self) -> dict[str, Any]:
        client = self.require_controller()
        providers = client.providers()
        return {
            "count": len(providers),
            "providers": [
                {
                    "name": name,
                    "type": info.get("vehicleType"),
                    "updated_at": info.get("updatedAt"),
                    "proxies": len(info.get("proxies") or []),
                }
                for name, info in providers.items()
            ],
        }

    def proxy_update_provider(self, name: str) -> dict[str, Any]:
        client = self.require_controller()
        client.update_provider(name)
        return {"provider": name, "updated": True}

    def mode_get(self) -> dict[str, Any]:
        client = self.require_controller()
        return {"mode": client.mode()}

    def mode_set(self, mode: str) -> dict[str, Any]:
        client = self.require_controller()
        client.set_mode(mode)
        return {"mode": client.mode()}

    def conn_list(self, limit: int = 0) -> dict[str, Any]:
        client = self.require_controller()
        data = client.connections()
        connections = data.get("connections") or []
        if limit and limit > 0:
            connections = connections[:limit]
        return {
            "count": len(connections),
            "download_total": data.get("downloadTotal"),
            "upload_total": data.get("uploadTotal"),
            "connections": [
                {
                    "id": conn.get("id"),
                    "host": conn.get("metadata", {}).get("host") or conn.get("metadata", {}).get("destinationIP"),
                    "network": conn.get("metadata", {}).get("network"),
                    "type": conn.get("metadata", {}).get("type"),
                    "chains": list(reversed(conn.get("chains") or [])),
                    "rule": conn.get("rule"),
                    "rule_payload": conn.get("rulePayload"),
                    "download": conn.get("download"),
                    "upload": conn.get("upload"),
                    "start": conn.get("start"),
                }
                for conn in connections
            ],
        }

    def conn_close(self, connection_id: str) -> dict[str, Any]:
        client = self.require_controller()
        client.close_connection(connection_id)
        return {"closed": connection_id}

    def conn_close_all(self) -> dict[str, Any]:
        client = self.require_controller()
        client.close_connections()
        return {"closed_all": True}

    def rule_list(self) -> dict[str, Any]:
        client = self.require_controller()
        rules = client.rules()
        return {
            "count": len(rules),
            "rules": [
                {
                    "type": rule.get("type"),
                    "payload": rule.get("payload"),
                    "proxy": rule.get("proxy"),
                    "size": rule.get("size"),
                }
                for rule in rules
            ],
        }

    def core_status(self) -> dict[str, Any]:
        process_state = self.process_status()
        payload: dict[str, Any] = {
            "gui_running": process_state["gui_running"],
            "core_running": process_state["core_running"],
            "gui_pids": process_state["gui_pids"],
            "core_pids": process_state["core_pids"],
        }
        state = self.controller_state()
        if state["enabled"]:
            payload["controller"] = self.controller().probe()
        else:
            payload["controller"] = {"reachable": False, "reason": "disabled in verge.yaml"}
        payload["runtime"] = self.runtime.summary()
        return payload

    def core_version(self) -> dict[str, Any]:
        client = self.require_controller()
        return client.version()

    def core_configs(self) -> dict[str, Any]:
        client = self.require_controller()
        return client.configs()

    def logs(self, lines: int = 40, file: str | None = None) -> dict[str, Any]:
        """Tail Clash Verge's own log files (not the controller's WS stream)."""
        target = Path(file) if file else self.paths.latest_log
        files = process_mod.log_files(self.paths.logs_dir)
        return {
            "file": str(target),
            "exists": target.is_file(),
            "lines": process_mod.tail_log(target, lines),
            "available_files": [str(entry) for entry in files[:10]],
        }


__all__ = [
    "ClashVergeBackend",
    "BackendError",
    "AppRunning",
    "ControllerNotReady",
    "ITEM_TYPES",
]
