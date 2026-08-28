"""Read/write ``profiles.yaml`` and the ``profiles/`` directory.

Native schema (``src-tauri/src/config/profiles.rs`` and ``prfitem.rs``)::

    # profiles.yaml
    current: <uid>
    items:
      - uid: RAbCdEfGhIjK
        type: remote          # remote | local | script | merge | rules | proxies | groups
        name: my subscription
        file: RAbCdEfGhIjK.yaml
        url: https://...
        updated: 1712345678
        option: {user_agent, with_proxy, self_proxy, update_interval, ...}
        extra: {upload, download, total, expire}
        selected: [{name, now}]

``uid`` prefixes encode the type::

    R remote   L local   s script (.js)
    m merge    r rules   p proxies   g groups

Clash Verge creates five companion items (merge/script/rules/proxies/groups)
for every real profile, so a freshly imported profile is really six items.
"""

from __future__ import annotations

import random
import string
import time
from pathlib import Path
from typing import Any

from .paths import ClashVergePaths
from .yamlio import VERGE_HEADER, load_mapping, save_yaml

#: ``type`` values and their uid prefix / file extension.
ITEM_TYPES: dict[str, dict[str, str]] = {
    "remote": {"prefix": "R", "ext": "yaml"},
    "local": {"prefix": "L", "ext": "yaml"},
    "script": {"prefix": "s", "ext": "js"},
    "merge": {"prefix": "m", "ext": "yaml"},
    "rules": {"prefix": "r", "ext": "yaml"},
    "proxies": {"prefix": "p", "ext": "yaml"},
    "groups": {"prefix": "g", "ext": "yaml"},
}

#: Item types that represent a real, selectable profile.
MAIN_TYPES = ("remote", "local")
#: Item types that are companions of a main profile.
COMPANION_TYPES = ("script", "merge", "rules", "proxies", "groups")

_UID_ALPHABET = string.ascii_letters + string.digits
_UID_LENGTH = 11


class ProfileError(RuntimeError):
    """Raised for invalid profile operations."""


def new_uid(itype: str) -> str:
    """Generate a uid matching Clash Verge's ``help::get_uid``.

    Rust uses ``nanoid`` with an alphanumeric alphabet; the profile-file regex
    in Clash Verge only accepts ``[a-zA-Z0-9]`` after the type prefix, so the
    alphabet here is alphanumeric only.
    """
    if itype not in ITEM_TYPES:
        raise ProfileError(
            f"unknown profile type '{itype}'. Valid: {', '.join(ITEM_TYPES)}"
        )
    prefix = ITEM_TYPES[itype]["prefix"]
    body = "".join(random.choices(_UID_ALPHABET, k=_UID_LENGTH))
    return f"{prefix}{body}"


def file_name_for(uid: str, itype: str) -> str:
    """``<uid>.yaml`` (or ``<uid>.js`` for scripts)."""
    return f"{uid}.{ITEM_TYPES[itype]['ext']}"


class Profiles:
    """Typed access to ``profiles.yaml`` plus its ``profiles/`` files."""

    path_key = "profiles_yaml"

    def __init__(self, paths: ClashVergePaths):
        self.paths = paths

    @property
    def path(self) -> Path:
        return self.paths.profiles_yaml

    # -- io --------------------------------------------------------------
    def load(self) -> dict[str, Any]:
        data = load_mapping(self.path)
        data.setdefault("current", None)
        if not isinstance(data.get("items"), list):
            data["items"] = []
        return data

    def save(self, data: dict[str, Any]) -> None:
        save_yaml(self.path, data, header=VERGE_HEADER)

    @staticmethod
    def empty() -> dict[str, Any]:
        return {"current": None, "items": []}

    # -- reads -----------------------------------------------------------
    def items(self) -> list[dict[str, Any]]:
        return list(self.load().get("items") or [])

    def main_items(self) -> list[dict[str, Any]]:
        """Only ``remote`` / ``local`` profiles."""
        return [
            item for item in self.items()
            if item.get("type") in MAIN_TYPES
        ]

    def by_uid(self, uid: str) -> dict[str, Any] | None:
        for item in self.items():
            if item.get("uid") == uid:
                return item
        return None

    def by_name(self, name: str) -> list[dict[str, Any]]:
        return [item for item in self.items() if item.get("name") == name]

    def resolve(self, selector: str) -> dict[str, Any]:
        """Resolve a uid or name to exactly one item.

        Names are matched case-insensitively; an ambiguous name is an error
        rather than a silent first-match, because agents cannot guess which
        profile they got.
        """
        item = self.by_uid(selector)
        if item is not None:
            return item
        lowered = selector.lower()
        matches = [i for i in self.items() if (i.get("name") or "").lower() == lowered]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ProfileError(
                f"no profile with uid or name '{selector}'.\n"
                "Run `clash-verge profile list` to see available profiles."
            )
        raise ProfileError(
            f"'{selector}' matches {len(matches)} profiles: "
            f"{', '.join(i.get('uid', '?') for i in matches)}. Use the uid."
        )

    def current(self) -> dict[str, Any] | None:
        data = self.load()
        uid = data.get("current")
        if not uid:
            return None
        return self.by_uid(uid)

    def companions(self, uid: str) -> list[dict[str, Any]]:
        """Companion items belonging to the profile ``uid``.

        Clash Verge names them ``<uid> merge`` / ``<uid> script`` / etc.
        """
        return [
            item for item in self.items()
            if item.get("type") in COMPANION_TYPES
            and (item.get("name") or "").startswith(uid)
        ]

    def file_path(self, item: dict[str, Any]) -> Path | None:
        """Absolute path of an item's data file."""
        file_name = item.get("file")
        if not file_name:
            return None
        return self.paths.profile_file(file_name)

    def file_exists(self, item: dict[str, Any]) -> bool:
        path = self.file_path(item)
        return bool(path) and path.is_file()

    # -- writes ----------------------------------------------------------
    def add(self, item: dict[str, Any]) -> dict[str, Any]:
        """Append ``item`` to ``profiles.yaml``."""
        data = self.load()
        if any(existing.get("uid") == item.get("uid") for existing in data["items"]):
            raise ProfileError(f"profile uid already exists: {item.get('uid')}")
        data["items"].append(item)
        self.save(data)
        return item

    def update_item(self, uid: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge ``patch`` into the item identified by ``uid``."""
        data = self.load()
        for item in data["items"]:
            if item.get("uid") == uid:
                item.update(patch)
                self.save(data)
                return item
        raise ProfileError(f"no profile with uid '{uid}'")

    def remove(self, uid: str) -> dict[str, Any]:
        """Remove ``uid`` and delete its data file.

        Returns a summary of what was removed so the caller can report it.
        """
        data = self.load()
        remaining = [item for item in data["items"] if item.get("uid") != uid]
        if len(remaining) == len(data["items"]):
            raise ProfileError(f"no profile with uid '{uid}'")

        target = self.by_uid(uid) or {}
        removed_files: list[str] = []
        path = self.file_path(target)
        if path and path.is_file():
            path.unlink()
            removed_files.append(path.name)

        data["items"] = remaining
        if data.get("current") == uid:
            next_main = [
                item for item in remaining if item.get("type") in MAIN_TYPES
            ]
            data["current"] = next_main[0].get("uid") if next_main else None
        self.save(data)
        return {"item": target, "removed_files": removed_files}

    def set_current(self, uid: str) -> dict[str, Any]:
        """Point ``current`` at ``uid``."""
        item = self.by_uid(uid)
        if item is None:
            raise ProfileError(f"no profile with uid '{uid}'")
        if item.get("type") not in MAIN_TYPES:
            raise ProfileError(
                f"'{uid}' is a '{item.get('type')}' item, not a selectable profile. "
                f"Selectable types: {', '.join(MAIN_TYPES)}."
            )
        data = self.load()
        previous = data.get("current")
        data["current"] = uid
        self.save(data)
        return {"previous": previous, "current": uid}

    @staticmethod
    def stamp_updated() -> int:
        """Unix timestamp in seconds, matching ``PrfItem.updated``."""
        return int(time.time())

    # -- inspection ------------------------------------------------------
    def validate(self) -> list[str]:
        """Return a list of inconsistencies in the profile store."""
        problems: list[str] = []
        data = self.load()
        seen: set[str] = set()
        for item in data["items"]:
            uid = item.get("uid")
            if not uid:
                problems.append("an item has no uid")
                continue
            if uid in seen:
                problems.append(f"duplicate uid: {uid}")
            seen.add(uid)
            itype = item.get("type")
            if itype and itype not in ITEM_TYPES:
                problems.append(f"{uid}: unknown type '{itype}'")
            file_name = item.get("file")
            if file_name and not self.paths.profile_file(file_name).is_file():
                problems.append(f"{uid}: missing data file {file_name}")
            elif not file_name:
                problems.append(f"{uid}: no 'file' field")

        current = data.get("current")
        if current and current not in seen:
            problems.append(f"current points at unknown uid: {current}")
        return problems
