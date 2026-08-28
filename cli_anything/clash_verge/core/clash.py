"""Read/write ``config.yaml`` — the Clash core overrides Clash Verge applies.

``config.yaml`` maps to ``IClashTemp`` (``src-tauri/src/config/clash.rs``), which
is a thin wrapper around a free-form YAML ``Mapping``. Clash Verge merges it
with the selected profile and its built-in enhancement to produce
``clash-verge.yaml`` — the file mihomo actually loads.

Because the struct is a ``Mapping``, unknown keys are legal and are preserved
on write.

Defaults below come from ``IClashTemp::template()`` and
``constants::network::ports``:

===========  =====
mixed-port   7897
socks-port   7898
port         7899
redir-port   7895
tproxy-port  7896
===========  =====

``external-controller`` defaults to ``127.0.0.1:9097`` and ``secret`` to
``set-your-secret``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .paths import ClashVergePaths
from .yamlio import CLASH_HEADER, load_mapping, save_yaml

DEFAULT_EXTERNAL_CONTROLLER = "127.0.0.1:9097"
DEFAULT_SECRET = "set-your-secret"

PORT_KEYS = (
    "mixed-port",
    "socks-port",
    "port",
    "redir-port",
    "tproxy-port",
)
BOOL_KEYS = ("allow-lan", "ipv6", "unified-delay")
ENUM_KEYS = {
    "mode": ("rule", "global", "direct"),
    "log-level": ("silent", "error", "warning", "info", "debug"),
}
MAPPING_KEYS = ("tun", "dns", "external-controller-cors", "profile", "experimental")
STRING_KEYS = (
    "external-controller",
    "external-controller-unix",
    "external-controller-pipe",
    "secret",
    "geodata-mode",
    "find-process-mode",
    "geodata-loader",
    "interface-name",
)

DEFAULT_TEMPLATE: dict[str, Any] = {
    "mixed-port": 7897,
    "socks-port": 7898,
    "port": 7899,
    "redir-port": 7895,
    "tproxy-port": 7896,
    "log-level": "info",
    "allow-lan": False,
    "ipv6": True,
    "mode": "rule",
    "external-controller": DEFAULT_EXTERNAL_CONTROLLER,
    "secret": DEFAULT_SECRET,
    "unified-delay": True,
}


class ClashError(RuntimeError):
    """Raised for invalid ``config.yaml`` operations."""


class ClashConfig:
    """Typed access to ``config.yaml`` (``IClashTemp``)."""

    path_key = "clash_yaml"

    def __init__(self, paths: ClashVergePaths):
        self.paths = paths

    @property
    def path(self) -> Path:
        return self.paths.clash_yaml

    # -- io --------------------------------------------------------------
    def load(self) -> dict[str, Any]:
        return load_mapping(self.path)

    def save(self, data: dict[str, Any]) -> None:
        save_yaml(self.path, data, header=CLASH_HEADER)

    # -- reads -----------------------------------------------------------
    def get(self, key: str) -> tuple[bool, Any]:
        """Return ``(is_set, value)`` for ``key``."""
        data = self.load()
        if key in data:
            return True, data[key]
        return False, DEFAULT_TEMPLATE.get(key)

    def known(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Scalar keys with their effective values."""
        data = self.load() if data is None else data
        rows = dict(DEFAULT_TEMPLATE)
        for key in STRING_KEYS:
            rows.setdefault(key, None)
        rows.update({key: data[key] for key in data if not isinstance(data[key], (dict, list))})
        return rows

    def unknown_keys(self, data: dict[str, Any] | None = None) -> list[str]:
        data = self.load() if data is None else data
        return [key for key in data if isinstance(data[key], (dict, list))]

    # -- writes ----------------------------------------------------------
    @staticmethod
    def coerce(key: str, raw: str) -> Any:
        """Parse a CLI string into the YAML type implied by ``key``."""
        text = raw.strip()
        if key in PORT_KEYS:
            try:
                port = int(text, 10)
            except ValueError as exc:
                raise ClashError(f"'{key}' expects a port number; got '{raw}'.") from exc
            if not 1 <= port <= 65535:
                raise ClashError(f"'{key}' must be between 1 and 65535; got {port}.")
            return port
        if key in BOOL_KEYS:
            lowered = text.lower()
            if lowered in ("true", "yes", "on", "1"):
                return True
            if lowered in ("false", "no", "off", "0"):
                return False
            raise ClashError(f"'{key}' expects a boolean; got '{raw}'.")
        if key in ENUM_KEYS:
            if text not in ENUM_KEYS[key]:
                raise ClashError(
                    f"'{key}' must be one of {', '.join(ENUM_KEYS[key])}; got '{raw}'."
                )
            return text
        if key in MAPPING_KEYS:
            import json

            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ClashError(
                    f"'{key}' expects a JSON object; could not parse: {exc}"
                ) from exc
            if not isinstance(parsed, dict):
                raise ClashError(f"'{key}' expects a JSON object; got {type(parsed).__name__}.")
            return parsed
        return raw

    def set(self, key: str, raw: str) -> tuple[Any, Any]:
        """Set ``key`` to the coerced value of ``raw``. Returns ``(old, new)``."""
        value = self.coerce(key, raw)
        data = self.load()
        old = data.get(key, DEFAULT_TEMPLATE.get(key))
        data[key] = value
        self.save(data)
        return old, value

    def unset(self, key: str) -> tuple[bool, Any]:
        """Remove ``key`` so Clash Verge falls back to its own default."""
        data = self.load()
        if key not in data:
            return False, None
        old = data.pop(key)
        self.save(data)
        return True, old

    # -- derived ---------------------------------------------------------
    def controller_url(self, verge: dict[str, Any] | None = None) -> str:
        """The HTTP base URL of the mihomo External Controller.

        Resolution mirrors Clash Verge: the ``external-controller`` value from
        ``config.yaml``, falling back to the compiled-in default
        (``constants::network::DEFAULT_EXTERNAL_CONTROLLER``).
        """
        _ = verge
        value = self.load().get("external-controller")
        host = value if isinstance(value, str) and value.strip() else DEFAULT_EXTERNAL_CONTROLLER
        host = host.strip()
        if "://" in host:
            return host.rstrip("/")
        return f"http://{host}"

    def secret(self) -> str:
        """The Bearer secret for the External Controller."""
        value = self.load().get("secret")
        if isinstance(value, str) and value:
            return value
        return DEFAULT_SECRET

    def validate(self) -> list[str]:
        """Return a list of problems found in the on-disk file."""
        problems: list[str] = []
        data = self.load()
        for key in PORT_KEYS:
            if key in data and not isinstance(data[key], int):
                problems.append(f"{key}: expected integer port, found {data[key]!r}")
        for key in BOOL_KEYS:
            if key in data and not isinstance(data[key], bool):
                problems.append(f"{key}: expected boolean, found {data[key]!r}")
        for key, allowed in ENUM_KEYS.items():
            if key in data and data[key] not in allowed:
                problems.append(f"{key}: must be one of {', '.join(allowed)}, found {data[key]!r}")
        return problems


class RuntimeConfig:
    """Read-only view of ``clash-verge.yaml`` — the config mihomo loaded.

    This is the authoritative answer to "what is actually running right now",
    because it is the merge product of the profile, the enhancement profile and
    ``config.yaml``. When present it beats any static reasoning about ports.
    """

    def __init__(self, paths: ClashVergePaths):
        self.paths = paths

    @property
    def path(self) -> Path:
        return self.paths.runtime_config

    def load(self) -> dict[str, Any]:
        return load_mapping(self.path)

    def summary(self) -> dict[str, Any]:
        """Ports, mode and controller as actually loaded by mihomo."""
        data = self.load()
        ports = {key: data[key] for key in PORT_KEYS if key in data}
        return {
            "available": bool(data),
            "ports": ports,
            "mode": data.get("mode"),
            "mixed_port": data.get("mixed-port"),
            "external_controller": data.get("external-controller"),
            "secret": data.get("secret"),
            "allow_lan": data.get("allow-lan"),
            "log_level": data.get("log-level"),
            "proxy_count": len(data.get("proxies") or []),
            "provider_count": len(data.get("proxy-providers") or []),
            "rule_count": len(data.get("rules") or []),
        }
