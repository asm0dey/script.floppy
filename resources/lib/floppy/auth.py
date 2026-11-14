"""OAuth device-flow login, token refresh, and authenticated calls."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .http import FloppyError, request
from .store import Store

_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_TOKEN_KEYS = ("access_token", "refresh_token", "access_expires_at")
_DEAD_GRANT = ("invalid_grant", "invalid_client")  # the only refresh errors that end a login
# Refresh rotates the refresh token: two threads refreshing at once would revoke the family.
_refresh_lock = threading.Lock()


class AuthRequired(Exception):  # noqa: N818 (name fixed by the task interface)
    """No usable credentials; the user must log in again."""


class ApiKeyRejected(AuthRequired):
    """Floppy answered 401 to the API token."""


def _store_tokens(store: Store, resp: dict) -> None:
    store.set("access_token", resp["access_token"])
    store.set("refresh_token", resp["refresh_token"])  # refresh rotates: always keep the new one
    store.set("access_expires_at", str(int(time.time()) + int(resp.get("expires_in", 0))))


def start_login(store: Store, server: str, client_name: str) -> dict:
    server = server.rstrip("/")
    client_id = store.get("client_id")
    if not client_id:
        reg = request("POST", f"{server}/oauth/register", form={"client_name": client_name})
        client_id = reg["client_id"]
        store.set("client_id", client_id)
    return request("POST", f"{server}/oauth/device/authorization", form={"client_id": client_id})


def poll_login(
    store: Store,
    server: str,
    device: dict,
    sleep: Callable[[float], None],
    cancelled: Callable[[], bool],
) -> bool:
    server = server.rstrip("/")
    interval = device.get("interval", 5)
    deadline = time.monotonic() + device.get("expires_in", 900)
    while not cancelled() and time.monotonic() < deadline:
        sleep(interval)
        if cancelled():
            return False
        try:
            resp = request(
                "POST",
                f"{server}/oauth/token",
                form={
                    "client_id": store.get("client_id") or "",
                    "grant_type": _DEVICE_GRANT,
                    "device_code": device["device_code"],
                },
            )
        except FloppyError as e:
            if e.code == "authorization_pending":
                continue
            if e.code == "slow_down":
                interval += 5
                continue
            if e.status is None:  # offline: keep waiting until the code expires
                continue
            return False  # expired_token, access_denied, anything else terminal
        _store_tokens(store, resp)
        return True
    return False


def _fresh(store: Store) -> bool:
    return int(store.get("access_expires_at") or 0) - time.time() >= 60


def _refresh(store: Store, server: str, stale: str | None) -> str:
    """New access token in place of ``stale``; reuses one another thread already fetched."""
    with _refresh_lock:
        current = store.get("access_token")
        if current and current != stale and _fresh(store):
            return current
        return _refresh_locked(store, server)


def _refresh_locked(store: Store, server: str) -> str:
    try:
        resp = request(
            "POST",
            f"{server.rstrip('/')}/oauth/token",
            form={
                "client_id": store.get("client_id") or "",
                "grant_type": "refresh_token",
                "refresh_token": store.get("refresh_token") or "",
            },
        )
    except FloppyError as e:
        if e.code in _DEAD_GRANT:
            for k in _TOKEN_KEYS:
                store.set(k, None)
            raise AuthRequired("refresh token rejected") from e
        raise
    _store_tokens(store, resp)
    return resp["access_token"]


def auth_headers(store: Store, server: str) -> dict:
    access = store.get("access_token")
    if store.get("refresh_token") and access:
        return {"token": access if _fresh(store) else _refresh(store, server, access)}
    if api_key := store.get("api_key"):
        return {"api_key": api_key}
    raise AuthRequired("not logged in")


def call(store: Store, server: str, method: str, path: str, **kw) -> dict:
    url = server.rstrip("/") + path
    creds = auth_headers(store, server)
    try:
        return request(method, url, **creds, **kw)
    except FloppyError as e:
        if e.status != 401:
            raise
        if "api_key" in creds:
            raise ApiKeyRejected("Floppy rejected the API token") from e
        if not store.get("refresh_token"):
            raise
    return request(method, url, token=_refresh(store, server, creds.get("token")), **kw)
