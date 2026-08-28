"""Shared fixtures.

Two guarantees for every test in this directory:

1. **Nothing touches a real installation.** The Clash Verge home directory and
   the session file are both redirected into pytest's ``tmp_path``.
2. **Process detection is pinned.** The suite never depends on whether the
   developer happens to have Clash Verge open.

The mihomo controller is replaced by a real HTTP server on a loopback port, so
the live command surface is tested over actual sockets.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pytest

from cli_anything.clash_verge.core import process as process_module
from cli_anything.clash_verge.core import session as session_module
from cli_anything.clash_verge.core.paths import ClashVergePaths

MOCK_SECRET = "test-secret"

#: What the mock mihomo reports. Mutated by ``PUT /proxies/:name`` and
#: ``PATCH /configs`` so live commands observe real state changes.
MOCK_PROXIES: dict[str, dict[str, Any]] = {
    "GLOBAL": {
        "type": "Selector",
        "now": "node-a",
        "all": ["node-a", "node-b"],
    },
    "PROXY": {
        "type": "URLTest",
        "now": "node-b",
        "all": ["node-a", "node-b", "DIRECT"],
    },
    "node-a": {"type": "Shadowsocks", "delay": 120},
    "node-b": {"type": "Vmess", "delay": 80},
    "DIRECT": {"type": "Direct", "delay": 0},
}
MOCK_MODE = "rule"
MOCK_CONNECTIONS = [
    {
        "id": "conn-1",
        "metadata": {"network": "tcp", "type": "HTTP", "host": "example.com"},
        "chains": ["node-a", "PROXY"],
        "rule": "MATCH",
        "rulePayload": "",
        "download": 1024,
        "upload": 512,
        "start": "2026-01-01T00:00:00Z",
    }
]
MOCK_RULES = [
    {"type": "DOMAIN-SUFFIX", "payload": "example.com", "proxy": "PROXY", "size": 1},
    {"type": "MATCH", "payload": "", "proxy": "PROXY", "size": 0},
]
MOCK_PROVIDERS = {
    "provider-one": {"vehicleType": "HTTP", "proxies": [{}, {}], "updatedAt": "2026-01-01T00:00:00Z"},
}


class _MihomoHandler(BaseHTTPRequestHandler):
    """A small subset of the mihomo External Controller."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # silence stderr noise
        return

    # -- plumbing --------------------------------------------------------
    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {MOCK_SECRET}"

    def _send(self, status: int, payload: Any = None) -> None:
        if payload is None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self) -> tuple[str, str]:
        parsed = urlparse(self.path)
        return parsed.path, unquote(parsed.path)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    # -- verbs -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        global MOCK_MODE
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        path, decoded = self._route()
        if path == "/version":
            self._send(200, {"version": "mihomo v1.18.0", "premium": False})
        elif path == "/configs":
            self._send(200, {"mode": MOCK_MODE, "mixed-port": 7897, "allow-lan": False})
        elif path == "/proxies":
            self._send(200, {"proxies": MOCK_PROXIES})
        elif path == "/rules":
            self._send(200, {"rules": MOCK_RULES})
        elif path == "/connections":
            self._send(
                200,
                {
                    "connections": MOCK_CONNECTIONS,
                    "downloadTotal": 4096,
                    "uploadTotal": 2048,
                },
            )
        elif path == "/providers/proxies":
            self._send(200, {"providers": MOCK_PROVIDERS})
        elif decoded.endswith("/delay"):
            if decoded.startswith("/group/"):
                name = decoded[len("/group/"):-len("/delay")]
                members = MOCK_PROXIES.get(name, {}).get("all", [])
                self._send(200, {member: {"delay": 42} for member in members})
            else:
                self._send(200, {"delay": 42})
        else:
            self._send(404, {"error": "not found"})

    def do_PATCH(self) -> None:  # noqa: N802
        global MOCK_MODE
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        path, _decoded = self._route()
        body = self._body()
        if path == "/configs":
            if "mode" in body:
                MOCK_MODE = body["mode"]
            self._send(204)
        else:
            self._send(404, {"error": "not found"})

    def do_PUT(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        path, decoded = self._route()
        body = self._body()
        if decoded.startswith("/proxies/"):
            name = decoded[len("/proxies/"):]
            group = MOCK_PROXIES.get(name)
            if group is None or "all" not in group:
                self._send(404, {"error": "not a selectable group"})
                return
            if body.get("name") not in group["all"]:
                self._send(400, {"error": "not a member"})
                return
            group["now"] = body["name"]
            self._send(204)
        elif decoded.startswith("/providers/proxies/"):
            self._send(204)
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        _path, decoded = self._route()
        if decoded == "/connections" or decoded.startswith("/connections/"):
            self._send(204)
        else:
            self._send(404, {"error": "not found"})


@pytest.fixture
def mock_controller_server():
    """Run the mock mihomo controller; yields its base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MihomoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect all state into ``tmp_path`` and pin process detection."""
    session_file = tmp_path / "session" / "session.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(session_module, "session_path", lambda: session_file)

    stopped = {
        "gui_running": False,
        "gui_pids": [],
        "core_running": False,
        "core_pids": [],
        "gui_executable": "",
    }
    monkeypatch.setattr(process_module, "gui_running", lambda: False)
    monkeypatch.setattr(process_module, "core_running", lambda: False)
    monkeypatch.setattr(process_module, "find_processes", lambda: {"gui": [], "core": []})
    monkeypatch.setattr(process_module, "status", lambda: dict(stopped))

    yield {"session_file": session_file, "tmp": tmp_path}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """An empty Clash Verge home directory."""
    target = tmp_path / "cv-home"
    target.mkdir(parents=True, exist_ok=True)
    return target


@pytest.fixture
def home_arg(home: Path) -> list[str]:
    """``--home <dir>`` ready to splice into a CLI invocation."""
    return ["--home", str(home)]


@pytest.fixture
def populated_home(home: Path) -> Path:
    """A home directory that already looks like a used installation."""
    from cli_anything.clash_verge.core.yamlio import CLASH_HEADER, VERGE_HEADER, save_yaml

    save_yaml(
        home / "verge.yaml",
        {
            "theme_mode": "dark",
            "enable_system_proxy": True,
            "enable_tun_mode": False,
            "verge_mixed_port": 7897,
            "some_future_key": {"nested": True},
        },
        header=VERGE_HEADER,
    )
    save_yaml(
        home / "config.yaml",
        {
            "mixed-port": 7897,
            "mode": "rule",
            "external-controller": "127.0.0.1:9097",
            "secret": "set-your-secret",
            "tun": {"enable": False, "stack": "gvisor"},
        },
        header=CLASH_HEADER,
    )
    save_yaml(
        home / "profiles.yaml",
        {
            "current": "RAAA1111111",
            "items": [
                {
                    "uid": "RAAA1111111",
                    "type": "remote",
                    "name": "existing sub",
                    "file": "RAAA1111111.yaml",
                    "url": "https://example.com/sub",
                    "updated": 1700000000,
                }
            ],
        },
        header=VERGE_HEADER,
    )
    profiles_dir = home / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "RAAA1111111.yaml").write_text(
        "proxies:\n- name: a\n  type: socks5\n", encoding="utf-8"
    )
    save_yaml(
        home / "clash-verge.yaml",
        {"mixed-port": 7897, "mode": "rule", "proxies": [{"name": "a"}]},
        header=CLASH_HEADER,
    )
    return home


@pytest.fixture
def enabled_controller_home(populated_home: Path) -> Path:
    """A home directory with the HTTP external controller turned on."""
    from cli_anything.clash_verge.core.yamlio import VERGE_HEADER, load_mapping, save_yaml

    path = populated_home / "verge.yaml"
    data = load_mapping(path)
    data["enable_external_controller"] = True
    save_yaml(path, data, header=VERGE_HEADER)
    return populated_home


def paths_for(home: Path) -> ClashVergePaths:
    """Convenience for tests that need a ``ClashVergePaths``."""
    return ClashVergePaths(home)


@pytest.fixture
def reset_mock_state():
    """Undo mutations a previous test made to the mock controller."""

    def _reset() -> None:
        global MOCK_MODE
        MOCK_MODE = "rule"
        MOCK_PROXIES["GLOBAL"]["now"] = "node-a"
        MOCK_PROXIES["PROXY"]["now"] = "node-b"

    _reset()
    return _reset
