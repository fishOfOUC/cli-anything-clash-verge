"""Mihomo (Clash Meta) External Controller REST client.

This is the same controller the Tauri shell drives through
``tauri-plugin-mihomo`` — the difference is transport: Clash Verge talks to the
core over a private IPC socket, while this client uses the HTTP controller that
mihomo exposes when ``external-controller`` is enabled.

Enabling it
-----------
``external-controller`` is only honoured when ``enable_external_controller`` is
``true`` in ``verge.yaml``; otherwise Clash Verge blanks the address and keeps
the core on IPC only. ``clash-verge controller enable`` flips that flag; the
core then has to be restarted for it to take effect.

Auth
----
Every request carries ``Authorization: Bearer <secret>``, where ``secret`` is
``config.yaml``'s ``secret`` (default ``set-your-secret``).
"""

from __future__ import annotations

import json
import shutil
import time
from typing import Any, Iterator
from urllib.parse import urlencode

import requests

DEFAULT_TIMEOUT = 10.0


class ControllerError(RuntimeError):
    """Raised when the controller cannot be reached or rejects a request."""


class ControllerDisabled(ControllerError):
    """Raised when the HTTP controller is known to be turned off."""


class MihomoController:
    """Thin wrapper over the mihomo External Controller."""

    def __init__(
        self,
        base_url: str,
        secret: str = "",
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.secret = secret or ""
        self.timeout = timeout
        self._session = requests.Session()
        if self.secret:
            self._session.headers.update({"Authorization": f"Bearer {self.secret}"})

    # -- plumbing --------------------------------------------------------
    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", self.timeout)
        try:
            response = self._session.request(method, self._url(path), **kwargs)
        except requests.RequestException as exc:
            raise ControllerError(
                f"cannot reach the mihomo controller at {self.base_url}: {exc}\n"
                "Is Clash Verge running with the external controller enabled? "
                "See `clash-verge controller status`."
            ) from exc

        if response.status_code == 401:
            raise ControllerError(
                "controller rejected the request: 401 Unauthorized.\n"
                "The 'secret' in config.yaml does not match the running core. "
                "Check `clash-verge controller secret`."
            )
        if response.status_code == 404:
            raise ControllerError(f"controller has no endpoint {path} (404).")
        if not response.ok:
            raise ControllerError(
                f"controller returned HTTP {response.status_code} for {path}: "
                f"{response.text[:400]}"
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except json.JSONDecodeError:
            return {"text": response.text}

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def patch(self, path: str, body: dict[str, Any]) -> Any:
        return self._request("PATCH", path, json=body)

    def put(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self._request("PUT", path, json=body if body is not None else {})

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=body if body is not None else {})

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # -- health ----------------------------------------------------------
    def probe(self) -> dict[str, Any]:
        """Check reachability. Never raises; returns a status dict."""
        started = time.monotonic()
        try:
            version = self.version()
        except ControllerError as exc:
            return {
                "reachable": False,
                "url": self.base_url,
                "error": str(exc).splitlines()[0],
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
            }
        return {
            "reachable": True,
            "url": self.base_url,
            "version": version.get("version"),
            "premium": version.get("premium"),
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
        }

    def version(self) -> dict[str, Any]:
        return self.get("/version")

    # -- configs ---------------------------------------------------------
    def configs(self) -> dict[str, Any]:
        return self.get("/configs")

    def patch_configs(self, body: dict[str, Any]) -> Any:
        """Live-apply config changes (mode, ports, allow-lan, log-level, ...)."""
        return self.patch("/configs", body)

    def mode(self) -> str:
        return self.configs().get("mode", "unknown")

    def set_mode(self, mode: str) -> Any:
        if mode not in ("rule", "global", "direct"):
            raise ControllerError(
                f"invalid mode '{mode}'. Use rule, global or direct."
            )
        return self.patch_configs({"mode": mode})

    # -- proxies ---------------------------------------------------------
    def proxies(self) -> dict[str, dict[str, Any]]:
        """``GET /proxies`` -> mapping of name -> proxy/proxy-group."""
        data = self.get("/proxies")
        return data.get("proxies") or {}

    def groups(self) -> dict[str, dict[str, Any]]:
        """Only the selectable proxy groups."""
        return {
            name: info
            for name, info in self.proxies().items()
            if "Selector" in (info.get("type") or "")
            or "Fallback" in (info.get("type") or "")
            or "LoadBalance" in (info.get("type") or "")
            or "URLTest" in (info.get("type") or "")
            or "Relay" in (info.get("type") or "")
        }

    def nodes(self, group: str | None = None) -> list[dict[str, Any]]:
        """Flat list of proxies, optionally restricted to one group's members."""
        all_proxies = self.proxies()
        if group is None:
            return [
                {"name": name, **info}
                for name, info in all_proxies.items()
                if "all" not in info
            ]
        info = all_proxies.get(group)
        if info is None:
            raise ControllerError(
                f"no proxy group named '{group}'.\n"
                "Run `clash-verge proxy groups` to list them."
            )
        members = info.get("all") or []
        return [
            {"name": name, **all_proxies.get(name, {})}
            for name in members
        ]

    def select(self, group: str, node: str) -> Any:
        """Switch a selectable group to ``node``."""
        available = self.proxies().get(group)
        if available is None:
            raise ControllerError(
                f"no proxy group named '{group}'.\n"
                "Run `clash-verge proxy groups` to list them."
            )
        if "all" not in available:
            raise ControllerError(
                f"'{group}' is a {available.get('type', 'proxy')} and is not selectable."
            )
        members = available.get("all") or []
        if node not in members:
            raise ControllerError(
                f"'{node}' is not a member of '{group}'.\n"
                f"Members: {', '.join(members[:20])}"
                + (" ..." if len(members) > 20 else "")
            )
        return self.put(f"/proxies/{_quote(group)}", {"name": node})

    def current_of(self, group: str) -> str | None:
        info = self.proxies().get(group)
        return (info or {}).get("now")

    def delay(self, name: str, url: str = "http://www.gstatic.com/generate_204", timeout: int = 5000) -> dict[str, Any]:
        """Measure one proxy's delay in milliseconds."""
        params = {"url": url, "timeout": int(timeout)}
        try:
            return self.get(f"/proxies/{_quote(name)}/delay", params=params)
        except ControllerError as exc:
            return {"name": name, "delay": 0, "error": str(exc).splitlines()[0]}

    def group_delay(self, name: str, url: str = "http://www.gstatic.com/generate_204", timeout: int = 5000) -> dict[str, Any]:
        """Measure every member of a group."""
        params = {"url": url, "timeout": int(timeout)}
        return self.get(f"/group/{_quote(name)}/delay", params=params)

    # -- rules -----------------------------------------------------------
    def rules(self) -> list[dict[str, Any]]:
        data = self.get("/rules")
        return data.get("rules") or []

    # -- connections -----------------------------------------------------
    def connections(self) -> dict[str, Any]:
        return self.get("/connections")

    def close_connection(self, connection_id: str) -> Any:
        return self.delete(f"/connections/{_quote(connection_id)}")

    def close_connections(self) -> Any:
        return self.delete("/connections")

    # -- providers -------------------------------------------------------
    def providers(self) -> dict[str, dict[str, Any]]:
        data = self.get("/providers/proxies")
        return data.get("providers") or {}

    def update_provider(self, name: str) -> Any:
        return self.put(f"/providers/proxies/{_quote(name)}")

    def healthcheck_provider(self, name: str) -> Any:
        return self.get(f"/providers/proxies/{_quote(name)}/healthcheck")

    # -- dns -------------------------------------------------------------
    def dns_query(self, name: str, qtype: str = "A") -> Any:
        return self.get("/dns/query", params={"name": name, "type": qtype})

    def flush_fakeip(self) -> Any:
        return self.post("/cache/fakeip/flush")

    # -- websocket streams -----------------------------------------------
    def _ws_url(self, path: str, params: dict[str, Any]) -> str:
        base = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        query = urlencode(params)
        url = f"{base}{path}"
        return f"{url}?{query}" if query else url

    def stream(self, path: str, params: dict[str, Any] | None = None, duration: float = 3.0) -> Iterator[dict[str, Any]]:
        """Yield JSON frames from a controller WebSocket for ``duration`` seconds.

        Used for ``/traffic``, ``/logs`` and ``/memory``, which the controller
        exposes as WebSocket streams only.
        """
        try:
            import websocket  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ControllerError(
                f"{path} is a WebSocket stream and needs the optional dependency "
                "'websocket-client'.\nInstall it with: pip install websocket-client"
            ) from exc

        params = dict(params or {})
        if self.secret:
            params.setdefault("token", self.secret)
        url = self._ws_url(path, params)
        deadline = time.monotonic() + duration
        try:
            ws = websocket.create_connection(url, timeout=max(1.0, duration))
        except Exception as exc:  # noqa: BLE001 - surfaced as ControllerError
            raise ControllerError(f"cannot open {url}: {exc}") from exc
        try:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                ws.settimeout(remaining)
                try:
                    frame = ws.recv()
                except Exception:  # noqa: BLE001 - timeout ends the stream
                    break
                if not frame:
                    break
                try:
                    yield json.loads(frame)
                except json.JSONDecodeError:
                    yield {"raw": frame}
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def traffic(self, duration: float = 3.0) -> Iterator[dict[str, Any]]:
        yield from self.stream("/traffic", duration=duration)

    def logs(self, level: str = "info", duration: float = 3.0) -> Iterator[dict[str, Any]]:
        yield from self.stream("/logs", {"level": level}, duration=duration)

    def memory(self, duration: float = 3.0) -> Iterator[dict[str, Any]]:
        yield from self.stream("/memory", duration=duration)


def _quote(value: str) -> str:
    """Percent-escape path segments without touching ``/``."""
    return requests.utils.quote(str(value), safe="")


def websocket_available() -> bool:
    """Whether the optional WebSocket dependency is installed."""
    return shutil.which("python") is not None and _has_websocket()


def _has_websocket() -> bool:
    try:
        import websocket  # noqa: F401

        return True
    except ImportError:
        return False
