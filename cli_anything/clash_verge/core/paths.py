"""Locate the Clash Verge Rev application home directory and its state files.

This mirrors ``src-tauri/src/utils/dirs.rs`` exactly:

.. code-block:: rust

    pub static APP_ID: &str = "io.github.clash-verge-rev.clash-verge-rev";
    pub fn app_home_dir() -> Result<PathBuf> {
        // portable: <exe_dir>/.config/<APP_ID>
        // otherwise: <data_dir>/<APP_ID>
    }
    pub static CLASH_CONFIG: &str  = "config.yaml";
    pub static VERGE_CONFIG: &str  = "verge.yaml";
    pub static PROFILE_YAML: &str  = "profiles.yaml";
    pub fn app_profiles_dir() -> Result<PathBuf> { app_home_dir().join("profiles") }

``<data_dir>`` is Tauri's ``path().data_dir()``:

===============  ==========================================================
Platform         Data root
===============  ==========================================================
Windows          ``%APPDATA%``
macOS            ``~/Library/Application Support``
Linux            ``$XDG_DATA_HOME`` or ``~/.local/share``
===============  ==========================================================
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ID = "io.github.clash-verge-rev.clash-verge-rev"
DEV_APP_ID = f"{APP_ID}.dev"

#: File names as defined in ``dirs.rs`` and ``constants.rs``.
CLASH_CONFIG = "config.yaml"
VERGE_CONFIG = "verge.yaml"
PROFILE_YAML = "profiles.yaml"
PROFILES_DIR = "profiles"
LOGS_DIR = "logs"
ICONS_DIR = "icons"
BACKUP_DIR = "clash-verge-rev-backup"
DEV_BACKUP_DIR = "clash-verge-rev-backup-dev"

#: ``constants::files``
RUNTIME_CONFIG = "clash-verge.yaml"
CHECK_CONFIG = "clash-verge-check.yaml"
DNS_CONFIG = "dns_config.yaml"
WINDOW_STATE = "window_state.json"

#: Environment overrides honoured by this harness.
ENV_HOME = "CLASH_VERGE_HOME"
ENV_PORTABLE_EXE_DIR = "CLASH_VERGE_PORTABLE_EXE_DIR"

PRODUCT_NAME = "Clash Verge"
SIDECAR_NAME = "verge-mihomo"


class ClashVergeNotFound(RuntimeError):
    """Raised when no Clash Verge home directory can be resolved."""


def platform_data_root() -> Path:
    """Return Tauri's ``data_dir()`` root for the current platform."""
    if sys.platform == "win32":
        root = os.environ.get("APPDATA")
        if not root:
            raise ClashVergeNotFound(
                "APPDATA is not set — cannot resolve the Clash Verge data directory. "
                "Set CLASH_VERGE_HOME to point at the Clash Verge home directory directly."
            )
        return Path(root)
    if sys.platform == "darwin":
        home = os.environ.get("HOME")
        if not home:
            raise ClashVergeNotFound(
                "HOME is not set — cannot resolve the Clash Verge data directory."
            )
        return Path(home) / "Library" / "Application Support"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    home = os.environ.get("HOME")
    if not home:
        raise ClashVergeNotFound(
            "HOME is not set — cannot resolve the Clash Verge data directory."
        )
    return Path(home) / ".local" / "share"


def default_home_dir(dev: bool = False) -> Path:
    """Standard (non-portable) home directory for the release or dev build."""
    return platform_data_root() / (DEV_APP_ID if dev else APP_ID)


def portable_home_dir(exe_dir: Path | str, dev: bool = False) -> Path:
    """Portable layout: ``<exe_dir>/.config/<APP_ID>``."""
    return Path(exe_dir) / ".config" / (DEV_APP_ID if dev else APP_ID)


def candidate_home_dirs() -> list[tuple[str, Path]]:
    """All plausible home directories, most specific first.

    Returns ``(label, path)`` pairs. Labels are stable identifiers so agents
    can reason about which layout they are actually talking to.
    """
    candidates: list[tuple[str, Path]] = []

    explicit = os.environ.get(ENV_HOME)
    if explicit:
        candidates.append(("env", Path(explicit).expanduser()))

    portable_dir = os.environ.get(ENV_PORTABLE_EXE_DIR)
    if portable_dir:
        candidates.append(
            ("portable", portable_home_dir(Path(portable_dir).expanduser()))
        )
        candidates.append(
            ("portable-dev", portable_home_dir(Path(portable_dir).expanduser(), dev=True))
        )

    candidates.append(("release", default_home_dir(dev=False)))
    candidates.append(("dev", default_home_dir(dev=True)))
    return candidates


def resolve_home_dir(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the Clash Verge home directory.

    Order: explicit argument -> ``$CLASH_VERGE_HOME`` -> first existing
    candidate -> the standard release directory.

    Raises ``ClashVergeNotFound`` when an explicit path was requested but does
    not exist, so agents get an unambiguous error instead of silently writing
    to an empty default location.
    """
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not path.is_dir():
            raise ClashVergeNotFound(
                f"Clash Verge home directory does not exist: {path}\n"
                "Clash Verge Rev must have been launched at least once, or pass "
                "an existing directory (see: clash-verge env paths)."
            )
        return path

    env_override = os.environ.get(ENV_HOME)
    if env_override:
        return Path(env_override).expanduser()

    for _label, path in candidate_home_dirs():
        if path.is_dir():
            return path

    return default_home_dir(dev=False)


class ClashVergePaths:
    """Typed accessor for every path the harness touches."""

    def __init__(self, home_dir: Path | str):
        self.home = Path(home_dir).expanduser()

    # -- top level state -------------------------------------------------
    @property
    def verge_yaml(self) -> Path:
        return self.home / VERGE_CONFIG

    @property
    def clash_yaml(self) -> Path:
        return self.home / CLASH_CONFIG

    @property
    def profiles_yaml(self) -> Path:
        return self.home / PROFILE_YAML

    @property
    def runtime_config(self) -> Path:
        """``clash-verge.yaml`` — the config mihomo actually loaded."""
        return self.home / RUNTIME_CONFIG

    @property
    def check_config(self) -> Path:
        return self.home / CHECK_CONFIG

    @property
    def dns_config(self) -> Path:
        return self.home / DNS_CONFIG

    @property
    def window_state(self) -> Path:
        return self.home / WINDOW_STATE

    # -- directories -----------------------------------------------------
    @property
    def profiles_dir(self) -> Path:
        return self.home / PROFILES_DIR

    @property
    def logs_dir(self) -> Path:
        return self.home / LOGS_DIR

    @property
    def icons_dir(self) -> Path:
        return self.home / ICONS_DIR

    @property
    def backup_dir(self) -> Path:
        return self.home / BACKUP_DIR

    @property
    def latest_log(self) -> Path:
        return self.logs_dir / "latest.log"

    # -- helpers ---------------------------------------------------------
    def profile_file(self, file_name: str) -> Path:
        """Absolute path of a file inside ``profiles/``."""
        return self.profiles_dir / file_name

    def layout(self) -> str:
        """Which layout this home directory corresponds to."""
        name = self.home.name
        if name == DEV_APP_ID:
            return "dev"
        if name == APP_ID:
            return "portable" if self.home.parent.name == ".config" else "release"
        return "custom"

    def describe(self) -> dict[str, str]:
        """JSON-serializable summary of every known path and its existence."""
        entries = {
            "home": self.home,
            "verge_yaml": self.verge_yaml,
            "clash_yaml": self.clash_yaml,
            "profiles_yaml": self.profiles_yaml,
            "runtime_config": self.runtime_config,
            "dns_config": self.dns_config,
            "profiles_dir": self.profiles_dir,
            "logs_dir": self.logs_dir,
            "latest_log": self.latest_log,
        }
        return {key: str(value) for key, value in entries.items()}

    def exists_map(self) -> dict[str, bool]:
        """``describe()`` keys mapped to whether each path exists."""
        entries = {
            "home": self.home,
            "verge_yaml": self.verge_yaml,
            "clash_yaml": self.clash_yaml,
            "profiles_yaml": self.profiles_yaml,
            "runtime_config": self.runtime_config,
            "dns_config": self.dns_config,
            "profiles_dir": self.profiles_dir,
            "logs_dir": self.logs_dir,
            "latest_log": self.latest_log,
        }
        return {key: value.exists() for key, value in entries.items()}
