"""Read/write ``verge.yaml`` — Clash Verge's own application settings.

The schema is a direct transcription of ``IVerge``
(``src-tauri/src/config/verge.rs``). Field names are snake_case in Rust and
identical in YAML (there are no ``#[serde(rename)]`` attributes on this struct).

Design notes
------------
* ``webdav_url`` / ``webdav_username`` / ``webdav_password`` are persisted with
  ``serialize_encrypted`` / ``deserialize_encrypted``. Writing plaintext back
  would corrupt them, so the CLI refuses to set these keys.
* Unknown keys are preserved on write, so settings added by a newer Clash Verge
  release survive a CLI edit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .paths import ClashVergePaths
from .yamlio import VERGE_HEADER, load_mapping, save_yaml

#: Keys the CLI refuses to write because Clash Verge encrypts them on disk.
ENCRYPTED_KEYS = frozenset({"webdav_url", "webdav_username", "webdav_password"})

_B = "bool"
_I = "int"
_S = "str"
_L = "list"
_J = "json"

#: ``IVerge`` field -> (type, template default, short description).
SCHEMA: dict[str, tuple[str, Any, str]] = {
    # -- logging ---------------------------------------------------------
    "app_log_level": (_S, None, "App log level: trace|debug|info|warn|error|silent"),
    "app_log_max_size": (_I, 128, "Max app log file size in KB"),
    "app_log_max_count": (_I, 8, "Max number of rotated app log files"),
    # -- appearance ------------------------------------------------------
    "language": (_S, None, "UI language, e.g. zh|en|ru|fa"),
    "theme_mode": (_S, "system", "Theme: system|light|dark"),
    "tray_event": (_S, None, "Tray click event"),
    "env_type": (_S, None, "Shell used for env vars: bash|powershell"),
    "start_page": (_S, "/", "Startup route"),
    "startup_script": (_S, None, "Script executed on startup"),
    "traffic_graph": (_B, True, "Show the traffic graph"),
    "enable_memory_usage": (_B, True, "Show memory usage"),
    "enable_group_icon": (_B, True, "Show proxy group icons"),
    "pause_render_traffic_stats_on_blur": (_B, True, "Pause traffic render when unfocused"),
    "common_tray_icon": (_B, False, "Use the common tray icon"),
    "tray_icon": (_S, None, "Tray icon style: colorful|monochrome"),
    "menu_icon": (_S, "monochrome", "Menu icon style: colorful|monochrome"),
    "menu_order": (_L, None, "Tray menu ordering"),
    "notice_position": (_S, "top-right", "Toast position"),
    "collapse_navbar": (_B, False, "Collapse the left navigation bar"),
    "sysproxy_tray_icon": (_B, False, "Colour the tray icon when system proxy is on"),
    "tun_tray_icon": (_B, False, "Colour the tray icon when TUN is on"),
    "theme_setting": (_J, None, "Custom theme object (IVergeTheme)"),
    "home_cards": (_J, None, "Home page card layout"),
    "proxy_layout_column": (_I, None, "Number of proxy columns"),
    # -- proxy / network -------------------------------------------------
    "enable_tun_mode": (_B, False, "Enable TUN (system-level) mode"),
    "enable_system_proxy": (_B, False, "Take over the OS system proxy"),
    "enable_proxy_guard": (_B, False, "Guard/restore the system proxy setting"),
    "enable_bypass_check": (_B, True, "Validate the bypass list"),
    "use_default_bypass": (_B, True, "Use the built-in bypass list"),
    "system_proxy_bypass": (_S, None, "Newline-separated system proxy bypass list"),
    "proxy_guard_duration": (_I, 30, "Proxy guard polling interval in seconds"),
    "proxy_auto_config": (_B, False, "Use a PAC file instead of a fixed proxy"),
    "pac_file_content": (_S, None, "PAC script content"),
    "proxy_host": (_S, "127.0.0.1", "Address the system proxy binds for clients"),
    "verge_mixed_port": (_I, 7897, "Mixed (HTTP+SOCKS) inbound port"),
    "verge_socks_port": (_I, 7898, "SOCKS inbound port"),
    "verge_socks_enabled": (_B, False, "Enable the SOCKS inbound"),
    "verge_port": (_I, 7899, "HTTP inbound port"),
    "verge_http_enabled": (_B, False, "Enable the HTTP inbound"),
    "verge_redir_port": (_I, 7895, "Redir inbound port (non-Windows)"),
    "verge_redir_enabled": (_B, False, "Enable the redir inbound (non-Windows)"),
    "verge_tproxy_port": (_I, 7896, "TProxy inbound port (Linux)"),
    "verge_tproxy_enabled": (_B, False, "Enable the tproxy inbound (Linux)"),
    "enable_dns_settings": (_B, None, "Apply built-in DNS settings"),
    "enable_external_controller": (_B, False, "Expose the mihomo REST controller over HTTP"),
    # -- behaviour -------------------------------------------------------
    "enable_auto_launch": (_B, False, "Launch at login"),
    "enable_silent_start": (_B, False, "Start hidden in the tray"),
    "auto_close_connection": (_B, True, "Close existing connections on profile switch"),
    "auto_check_update": (_B, True, "Check for updates in the background"),
    "default_latency_test": (_S, None, "Default latency test URL"),
    "default_latency_timeout": (_I, None, "Latency test timeout in seconds"),
    "enable_auto_delay_detection": (_B, False, "Run delay detection on a schedule"),
    "auto_delay_detection_interval_minutes": (_I, None, "Delay-detection interval in minutes"),
    "enable_builtin_enhanced": (_B, True, "Apply the built-in config enhancement profile"),
    "enable_global_hotkey": (_B, True, "Enable global hotkeys"),
    "hotkeys": (_L, None, "Global hotkey bindings"),
    "clash_core": (_S, "verge-mihomo", "Core binary: verge-mihomo|verge-mihomo-alpha"),
    "test_list": (_L, None, "Saved latency test targets"),
    "web_ui_list": (_L, None, "External dashboard URLs"),
    # -- housekeeping ----------------------------------------------------
    "auto_log_clean": (_I, 2, "Log retention: 0 off|1 1d|2 7d|3 30d|4 90d"),
    "enable_auto_backup_schedule": (_B, False, "Back up the config on a schedule"),
    "auto_backup_interval_hours": (_I, 24, "Scheduled backup interval in hours"),
    "auto_backup_on_change": (_B, True, "Back up the config when it changes"),
    "enable_auto_light_weight_mode": (_B, False, "Enter lightweight mode when idle"),
    "auto_light_weight_minutes": (_I, 10, "Idle minutes before lightweight mode"),
    "enable_hover_jump_navigator": (_B, True, "Hover-jump navigation on the proxy page"),
    "hover_jump_navigator_delay": (_I, 280, "Hover-jump delay in milliseconds"),
    "enable_tray_speed": (_B, False, "Show speed in the tray (macOS)"),
    "tray_proxy_groups_display_mode": (_S, "default", "Tray proxy group display mode"),
    "tray_inline_outbound_modes": (_B, False, "Inline outbound modes in the tray"),
    # -- encrypted, read-only --------------------------------------------
    "webdav_url": (_S, None, "WebDAV URL (ENCRYPTED on disk — read-only)"),
    "webdav_username": (_S, None, "WebDAV user (ENCRYPTED on disk — read-only)"),
    "webdav_password": (_S, None, "WebDAV password (ENCRYPTED on disk — read-only)"),
}

TRUE_WORDS = {"true", "yes", "on", "1", "enable", "enabled"}
FALSE_WORDS = {"false", "no", "off", "0", "disable", "disabled"}


class VergeError(RuntimeError):
    """Raised for invalid ``verge.yaml`` operations."""


class VergeConfig:
    """Typed access to ``verge.yaml``."""

    path_key = "verge_yaml"

    def __init__(self, paths: ClashVergePaths):
        self.paths = paths

    @property
    def path(self) -> Path:
        return self.paths.verge_yaml

    # -- io --------------------------------------------------------------
    def load(self) -> dict[str, Any]:
        return load_mapping(self.path)

    def save(self, data: dict[str, Any]) -> None:
        save_yaml(self.path, data, header=VERGE_HEADER)

    # -- reads -----------------------------------------------------------
    def get(self, key: str) -> tuple[bool, Any]:
        """Return ``(is_set, value)`` for ``key``.

        ``is_set`` distinguishes "absent" from "present with value None", which
        matters because Clash Verge omits unset optional fields entirely.
        """
        data = self.load()
        if key in data:
            return True, data[key]
        if key in SCHEMA:
            return False, SCHEMA[key][1]
        return False, None

    def known(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """All schema keys with their effective values and set/unset flag."""
        data = self.load() if data is None else data
        rows: dict[str, Any] = {}
        for key, (_type, default, _doc) in SCHEMA.items():
            rows[key] = data[key] if key in data else default
        return rows

    def unknown_keys(self, data: dict[str, Any] | None = None) -> list[str]:
        """Keys present on disk that this schema does not know about."""
        data = self.load() if data is None else data
        return [key for key in data if key not in SCHEMA]

    # -- writes ----------------------------------------------------------
    @staticmethod
    def coerce(key: str, raw: str) -> Any:
        """Parse a CLI string into the YAML type declared for ``key``."""
        if key in ENCRYPTED_KEYS:
            raise VergeError(
                f"'{key}' is stored encrypted by Clash Verge and cannot be written "
                "by this CLI. Change it in the Clash Verge GUI."
            )
        if key not in SCHEMA:
            raise VergeError(
                f"unknown verge key '{key}'.\n"
                "Run `clash-verge verge list` for the full key list."
            )
        import json

        kind = SCHEMA[key][0]
        text = raw.strip()
        if kind == _B:
            lowered = text.lower()
            if lowered in TRUE_WORDS:
                return True
            if lowered in FALSE_WORDS:
                return False
            raise VergeError(
                f"'{key}' expects a boolean; got '{raw}'. Use true/false."
            )
        if kind == _I:
            try:
                return int(text, 10)
            except ValueError as exc:
                raise VergeError(f"'{key}' expects an integer; got '{raw}'.") from exc
        if kind == _L:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = [item.strip() for item in text.split(",") if item.strip()]
            if not isinstance(parsed, list):
                raise VergeError(
                    f"'{key}' expects a JSON array or comma-separated list; got '{raw}'."
                )
            return parsed
        if kind == _J:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise VergeError(
                    f"'{key}' expects a JSON object; could not parse: {exc}"
                ) from exc
            if not isinstance(parsed, dict):
                raise VergeError(
                    f"'{key}' expects a JSON object; got {type(parsed).__name__}."
                )
            return parsed
        return raw

    def set(self, key: str, raw: str) -> tuple[Any, Any]:
        """Set ``key`` to the coerced value of ``raw``. Returns ``(old, new)``."""
        value = self.coerce(key, raw)
        data = self.load()
        old = data.get(key, SCHEMA.get(key, (_S, None, ""))[1])
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

    def validate(self) -> list[str]:
        """Return a list of problems found in the on-disk file."""
        problems: list[str] = []
        data = self.load()
        for key, (kind, _default, _doc) in SCHEMA.items():
            if key not in data:
                continue
            value = data[key]
            if value is None:
                continue
            if kind == _B and not isinstance(value, bool):
                problems.append(f"{key}: expected boolean, found {value!r}")
            elif kind == _I and not isinstance(value, int):
                problems.append(f"{key}: expected integer, found {value!r}")
            elif kind == _S and not isinstance(value, str):
                problems.append(f"{key}: expected string, found {value!r}")
        return problems
