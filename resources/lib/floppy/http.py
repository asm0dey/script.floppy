"""Minimal JSON-over-HTTP client for the Floppy API (stdlib only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class FloppyError(Exception):
    """HTTP or network failure. ``status`` is None for network errors."""

    def __init__(self, status: int | None, code: str | None, body: dict, message: str = ""):
        self.status = status
        self.code = code
        self.body = body
        super().__init__(f"{message or 'request failed'} (status={status}, code={code})")


def _error(status: int, raw: bytes) -> FloppyError:
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        body = {}
    code = body.get("code") or body.get("error")
    return FloppyError(status, code if isinstance(code, str) else None, body, f"HTTP {status}")


def request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    api_key: str | None = None,
    json_body: dict | None = None,
    form: dict | None = None,
    timeout: float = 10,
    headers: dict | None = None,
) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "script.floppy", **(headers or {})}
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise _error(e.code, e.read()) from e
    except (OSError, ValueError) as e:  # URLError, timeouts, bad URL
        raise FloppyError(None, None, {}, f"network error: {e}") from e
    if not raw:
        return {}
    try:
        out = json.loads(raw)
    except ValueError as e:
        raise FloppyError(None, None, {}, "invalid JSON in response") from e
    return out if isinstance(out, dict) else {}
