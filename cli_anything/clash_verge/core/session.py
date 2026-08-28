"""Persistent session state for the Clash Verge harness.

Two things live here:

``Session``
    The directory being operated on plus controller overrides. The harness has
    no "project file" — Clash Verge's own home directory *is* the project.

``undo`` / ``redo``
    Every mutation records the exact bytes of each file it touched, before and
    after. Undo restores the "before" bytes. This is what makes agent-driven
    editing safe: a bad ``profile import`` or ``verge set`` is always
    reversible, even across separate CLI invocations.

Session JSON is written under an exclusive lock with stale-lock cleanup, so a
parallel ``clash-verge`` process can never interleave two writes.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SESSION_DIR_NAME = ".cli-anything-clash-verge"
SESSION_FILE_NAME = "session.json"
LOCK_STALE_SECONDS = 60.0
LOCK_TIMEOUT_SECONDS = 5.0
MAX_HISTORY = 100


class SessionError(RuntimeError):
    """Raised for session persistence problems."""


def session_dir() -> Path:
    """Directory holding the session file (``~/.cli-anything-clash-verge``)."""
    return Path.home() / SESSION_DIR_NAME


def session_path() -> Path:
    return session_dir() / SESSION_FILE_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_text_or_none(path: Path) -> str | None:
    """Current file contents, or ``None`` when the file does not exist."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:  # pragma: no cover - platform specific
        raise SessionError(f"cannot read {path}: {exc}") from exc


def _locked_save_json(path: Path, payload: dict, timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
    """Write JSON to ``path`` under an exclusive ``.lock`` file.

    Stale locks older than ``LOCK_STALE_SECONDS`` are reclaimed so an
    interrupted process cannot wedge the session forever.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")

    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0.0
            if age > LOCK_STALE_SECONDS:
                try:
                    lock_path.unlink()
                    continue
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                raise SessionError(
                    f"session file is locked by another process: {lock_path}"
                )
            time.sleep(0.05)

    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            os.replace(tmp_name, path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SessionError(
            f"session file is corrupted: {path}\n"
            f"  {exc}\n"
            "Delete it to start a fresh session."
        ) from exc
    if not isinstance(data, dict):
        raise SessionError(f"session file must contain a JSON object: {path}")
    return data


class ChangeSet:
    """Tracks file contents before and after one logical mutation.

    Usage::

        changes = ChangeSet("profile import demo")
        changes.track(paths.profiles_yaml)
        ...write the new file...
        session.commit(changes)
    """

    def __init__(self, label: str):
        self.label = label
        self._paths: list[Path] = []
        self._before: dict[str, str | None] = {}
        self._after: dict[str, str | None] = {}

    def track(self, path: Path | str) -> None:
        """Start tracking ``path``, capturing its pre-mutation contents."""
        resolved = Path(path)
        key = str(resolved)
        if key in self._before:
            return
        self._before[key] = _read_text_or_none(resolved)
        self._paths.append(resolved)

    def capture_after(self, path: Path | str) -> None:
        """Capture post-mutation contents for an already tracked path."""
        resolved = Path(path)
        key = str(resolved)
        if key not in self._before:
            self.track(resolved)
        self._after[key] = _read_text_or_none(resolved)

    def finish(self) -> dict:
        """Finalize capture for all tracked paths and return a history entry."""
        for resolved in self._paths:
            key = str(resolved)
            self._after.setdefault(key, _read_text_or_none(resolved))
        return {
            "label": self.label,
            "ts": _now_iso(),
            "files": {
                key: {"before": self._before[key], "after": self._after[key]}
                for key in self._before
            },
        }


class Session:
    """The persistent session payload."""

    def __init__(
        self,
        home_dir: str | None = None,
        controller_url: str | None = None,
        secret: str | None = None,
    ):
        self.home_dir = home_dir
        self.controller_url = controller_url
        self.secret = secret
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.updated_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "home_dir": self.home_dir,
            "controller_url": self.controller_url,
            "secret": self.secret,
            "undo_stack": self.undo_stack[-MAX_HISTORY:],
            "redo_stack": self.redo_stack[-MAX_HISTORY:],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        session = cls(
            home_dir=data.get("home_dir"),
            controller_url=data.get("controller_url"),
            secret=data.get("secret"),
        )
        session.undo_stack = list(data.get("undo_stack") or [])
        session.redo_stack = list(data.get("redo_stack") or [])
        session.updated_at = data.get("updated_at")
        return session


class SessionManager:
    """Loads, mutates and persists the session with undo/redo support."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else session_path()
        self._data = _load_json(self.path)
        self.session = Session.from_dict(self._data)

    # -- persistence -----------------------------------------------------
    def save(self) -> None:
        """Persist the session to disk under an exclusive lock."""
        self.session.updated_at = _now_iso()
        _locked_save_json(self.path, self.session.to_dict())

    # -- mutations -------------------------------------------------------
    def commit(self, changes: ChangeSet) -> dict:
        """Record a completed mutation and persist it."""
        entry = changes.finish()
        if not entry["files"]:
            raise SessionError(f"nothing to record for change: {changes.label}")
        entry["id"] = (self.session.undo_stack[-1]["id"] + 1) if self.session.undo_stack else 1
        self.session.undo_stack.append(entry)
        self.session.redo_stack.clear()
        if len(self.session.undo_stack) > MAX_HISTORY:
            del self.session.undo_stack[0]
        self.save()
        return entry

    def undo(self) -> dict | None:
        """Restore the most recent change; returns the entry that was undone."""
        if not self.session.undo_stack:
            return None
        entry = self.session.undo_stack.pop()
        self._apply(entry["files"], "before")
        self.session.redo_stack.append(entry)
        self.save()
        return entry

    def redo(self) -> dict | None:
        """Re-apply the most recently undone change."""
        if not self.session.redo_stack:
            return None
        entry = self.session.redo_stack.pop()
        self._apply(entry["files"], "after")
        self.session.undo_stack.append(entry)
        self.save()
        return entry

    @staticmethod
    def _apply(files: dict[str, dict], side: str) -> None:
        for path_str, states in files.items():
            path = Path(path_str)
            content = states.get(side)
            if content is None:
                if path.exists():
                    path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    # -- inspection ------------------------------------------------------
    def history(self, limit: int = 20) -> list[dict]:
        """Human-readable undo history, newest first."""
        rows = []
        for entry in reversed(self.session.undo_stack[-limit:]):
            rows.append(
                {
                    "id": entry.get("id"),
                    "label": entry.get("label"),
                    "ts": entry.get("ts"),
                    "files": list(entry.get("files", {}).keys()),
                }
            )
        return rows
