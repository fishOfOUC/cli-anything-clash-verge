"""Fetch and validate subscription / profile payloads.

Mirrors ``PrfItem::from_url`` and ``PrfItem::from_local``
(``src-tauri/src/config/prfitem.rs``):

* download the remote document (or read a local one),
* require that it parses as YAML *and* carries ``proxies`` or
  ``proxy-providers``,
* read traffic metadata from the ``subscription-userinfo`` header,
* read ``profile-update-interval`` (hours, converted to the minutes-based
  ``option.update_interval``) and ``profile-web-page-url``.

The same validation gate is applied to local files so ``profile import`` can
never store a document Clash Verge would reject.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import requests
import yaml

DEFAULT_USER_AGENT = "clash-verge/v2.4.3"
DEFAULT_TIMEOUT = 60
DEFAULT_ACCEPT = "*/*"

#: Keys that make a document a usable Clash profile.
REQUIRED_TOP_LEVEL_KEYS = ("proxies", "proxy-providers")

_SUBSCRIPTION_USERINFO_RE = re.compile(r"(\w+)\s*=\s*([^;]+)")
_CONTENT_DISPOSITION_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.I)


class SubscriptionError(RuntimeError):
    """Raised when a profile payload cannot be fetched or is not valid."""


def parse_subscription_userinfo(header: str | None) -> dict[str, int]:
    """Parse ``upload=1; download=2; total=3; expire=4`` into ints."""
    if not header:
        return {}
    values: dict[str, int] = {}
    for key, raw in _SUBSCRIPTION_USERINFO_RE.findall(header):
        try:
            values[key.strip().lower()] = int(float(raw.strip()))
        except ValueError:
            continue
    return values


def parse_update_interval(header: str | None) -> int | None:
    """``profile-update-interval`` is in hours; ``option.update_interval`` is minutes."""
    if not header:
        return None
    try:
        hours = float(header.strip())
    except ValueError:
        return None
    if hours <= 0:
        return None
    return int(hours * 60)


def parse_content_disposition(header: str | None) -> str | None:
    """Best-effort filename extraction from ``Content-Disposition``."""
    if not header:
        return None
    match = _CONTENT_DISPOSITION_RE.search(header)
    if not match:
        return None
    return match.group(1).strip().strip('"') or None


def validate_payload(text: str) -> dict[str, Any]:
    """Parse and validate a profile document.

    Returns the parsed mapping. Raises ``SubscriptionError`` when the document
    is not valid YAML or carries neither ``proxies`` nor ``proxy-providers`` —
    the same acceptance test Clash Verge applies.
    """
    if not text or not text.strip():
        raise SubscriptionError("profile payload is empty")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SubscriptionError(f"profile payload is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SubscriptionError(
            f"profile payload must be a YAML mapping, got {type(data).__name__}"
        )
    if not any(key in data for key in REQUIRED_TOP_LEVEL_KEYS):
        raise SubscriptionError(
            "profile payload has neither 'proxies' nor 'proxy-providers'. "
            "A usable Clash profile must define at least one of them."
        )
    return data


def summarize_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Counts worth surfacing right after an import."""
    return {
        "proxy_count": len(data.get("proxies") or []),
        "provider_count": len(data.get("proxy-providers") or []),
        "rule_count": len(data.get("rules") or []),
        "other_keys": sorted(
            key for key in data if key not in ("proxies", "proxy-providers", "rules")
        ),
    }


def fetch(
    url: str,
    *,
    user_agent: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    with_proxy: bool = False,
    insecure: bool = False,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Download and validate a remote profile.

    Returns a dict with the raw ``text``, the parsed ``data``, and any metadata
    gleaned from the response headers.
    """
    request_headers = {
        "User-Agent": user_agent or DEFAULT_USER_AGENT,
        "Accept": DEFAULT_ACCEPT,
    }
    if headers:
        request_headers.update(headers)

    proxies = {"http": None, "https": None} if not with_proxy else None
    try:
        response = requests.get(
            url,
            headers=request_headers,
            timeout=timeout,
            verify=not insecure,
            proxies=proxies,
        )
    except requests.RequestException as exc:
        raise SubscriptionError(f"failed to download '{url}': {exc}") from exc

    if not response.ok:
        raise SubscriptionError(
            f"download failed for '{url}': HTTP {response.status_code} {response.reason}"
        )

    text = response.text
    data = validate_payload(text)

    info = parse_subscription_userinfo(response.headers.get("subscription-userinfo"))
    return {
        "text": text,
        "data": data,
        "summary": summarize_payload(data),
        "extra": {
            "upload": info.get("upload", 0),
            "download": info.get("download", 0),
            "total": info.get("total", 0),
            "expire": info.get("expire", 0),
        },
        "update_interval": parse_update_interval(
            response.headers.get("profile-update-interval")
        ),
        "home": response.headers.get("profile-web-page-url"),
        "suggested_name": parse_content_disposition(
            response.headers.get("content-disposition")
        ),
        "status": response.status_code,
    }


def read_local(path: Path | str) -> dict[str, Any]:
    """Read and validate a profile document from disk."""
    path = Path(path)
    if not path.is_file():
        raise SubscriptionError(f"file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SubscriptionError(f"cannot read {path}: {exc}") from exc
    data = validate_payload(text)
    return {
        "text": text,
        "data": data,
        "summary": summarize_payload(data),
        "extra": None,
        "update_interval": None,
        "home": None,
        "suggested_name": path.stem,
    }
