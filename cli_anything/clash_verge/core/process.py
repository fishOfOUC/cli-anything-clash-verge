"""Detect (and locate) the Clash Verge GUI and the mihomo sidecar.

Why this matters: Clash Verge keeps its state in memory and rewrites
``profiles.yaml`` / ``verge.yaml`` / ``config.yaml`` whenever the user changes
something in the GUI. Editing those files while the app is running therefore
races with the app. Every mutating command checks this and refuses by default.

Implemented with the standard library only — no psutil dependency.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .paths import PRODUCT_NAME, SIDECAR_NAME

#: Windows install locations, most likely first.
_WINDOWS_HINTS = (
    Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "Programs" / PRODUCT_NAME / f"{PRODUCT_NAME}.exe",
    Path(os.path.expandvars(r"%PROGRAMFILES%")) / PRODUCT_NAME / f"{PRODUCT_NAME}.exe",
    Path(os.path.expandvars(r"%LOCALAPPDATA%")) / PRODUCT_NAME / f"{PRODUCT_NAME}.exe",
)
_MACOS_HINTS = (
    Path("/Applications") / f"{PRODUCT_NAME}.app" / "Contents" / "MacOS" / PRODUCT_NAME,
    Path.home() / "Applications" / f"{PRODUCT_NAME}.app" / "Contents" / "MacOS" / PRODUCT_NAME,
)
_LINUX_HINTS = (
    Path("/usr/bin/clash-verge"),
    Path("/usr/bin/clash-verge-rev"),
    Path("/opt/clash-verge/clash-verge"),
    Path("/opt/Clash Verge/clash-verge"),
    Path.home() / ".local" / "bin" / "clash-verge",
)

_GUI_PATTERNS = ("clash-verge", "clash verge")
_CORE_PATTERNS = (SIDECAR_NAME,)


class ProcessError(RuntimeError):
    """Raised when process inspection fails in a way the caller must know."""


def _running_processes() -> list[tuple[int, str]]:
    """Return ``(pid, command_line)`` for every process on the system."""
    if sys.platform == "win32":
        return _windows_processes()
    return _unix_processes()


def _windows_processes() -> list[tuple[int, str]]:
    try:
        output = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProcessError(f"cannot list processes with tasklist: {exc}") from exc

    rows: list[tuple[int, str]] = []
    for line in output.splitlines():
        parts = next(
            iter(__import__("csv").reader([line])),
            [],
        )
        if len(parts) < 2:
            continue
        name, pid = parts[0], parts[1]
        try:
            rows.append((int(pid), name))
        except ValueError:
            continue
    return rows


def _unix_processes() -> list[tuple[int, str]]:
    try:
        output = subprocess.run(
            ["ps", "-A", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProcessError(f"cannot list processes with ps: {exc}") from exc

    rows: list[tuple[int, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_text, _, command = line.partition(" ")
        try:
            rows.append((int(pid_text), command))
        except ValueError:
            continue
    return rows


def _match(command: str, patterns: tuple[str, ...]) -> bool:
    lowered = command.lower()
    return any(pattern in lowered for pattern in patterns)


def find_processes() -> dict[str, list[dict[str, Any]]]:
    """Detect the Clash Verge GUI and the mihomo sidecar."""
    result: dict[str, list[dict[str, Any]]] = {"gui": [], "core": []}
    try:
        processes = _running_processes()
    except ProcessError:
        return result
    for pid, command in processes:
        if _match(command, _CORE_PATTERNS):
            result["core"].append({"pid": pid, "command": command.strip()})
        elif _match(command, _GUI_PATTERNS):
            result["gui"].append({"pid": pid, "command": command.strip()})
    return result


def gui_running() -> bool:
    """Whether the Clash Verge GUI is running."""
    return bool(find_processes()["gui"])


def core_running() -> bool:
    """Whether the mihomo sidecar is running."""
    return bool(find_processes()["core"])


def status() -> dict[str, Any]:
    """JSON-serializable process status."""
    found = find_processes()
    return {
        "gui_running": bool(found["gui"]),
        "gui_pids": [entry["pid"] for entry in found["gui"]],
        "core_running": bool(found["core"]),
        "core_pids": [entry["pid"] for entry in found["core"]],
        "gui_executable": str(locate_executable() or ""),
    }


def locate_executable() -> Path | None:
    """Best-effort path of the Clash Verge executable.

    Returns ``None`` when it cannot be found; callers must handle that rather
    than pretending they can launch the app.
    """
    if sys.platform == "win32":
        hints = _WINDOWS_HINTS
    elif sys.platform == "darwin":
        hints = _MACOS_HINTS
    else:
        hints = _LINUX_HINTS
    for hint in hints:
        if hint.is_file():
            return hint
    return None


def launch() -> dict[str, Any]:
    """Start the Clash Verge GUI detached from this CLI."""
    executable = locate_executable()
    if executable is None:
        raise ProcessError(
            "cannot locate the Clash Verge executable.\n"
            "Start it manually, or set CLASH_VERGE_HOME so the CLI can find "
            "its data directory."
        )
    try:
        if sys.platform == "win32":
            os.startfile(str(executable))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(executable)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen([str(executable)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        raise ProcessError(f"failed to launch {executable}: {exc}") from exc
    return {"launched": True, "executable": str(executable)}


def tail_log(path: Path | str, lines: int = 40) -> list[str]:
    """Read the last ``lines`` lines of a Clash Verge log file efficiently."""
    path = Path(path)
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - 512 * 1024))
            chunk = handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise ProcessError(f"cannot read log {path}: {exc}") from exc
    return chunk.splitlines()[-lines:]


def log_files(path: Path | str) -> list[Path]:
    """All Clash Verge log files, newest first."""
    directory = Path(path)
    if not directory.is_dir():
        return []
    files = [entry for entry in directory.iterdir() if entry.is_file() and entry.suffix == ".log"]
    return sorted(files, key=lambda entry: entry.stat().st_mtime, reverse=True)
