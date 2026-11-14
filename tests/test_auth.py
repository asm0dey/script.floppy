from __future__ import annotations

import time

import pytest
from fake_floppy import FakeFloppy
from floppy import auth
from floppy.auth import AuthRequired
from floppy.http import FloppyError
from floppy.store import Store

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "s.db"))


def _tokens(access="a1", refresh="r1", expires_in=3600):
    return {"access_token": access, "refresh_token": refresh, "expires_in": expires_in}


def _register(fake):
    fake.route("POST", "/oauth/register", lambda r: (200, {"client_id": "cid"}))


def test_register_once(store):
    device = {"device_code": "d", "user_code": "U", "interval": 5}
    with FakeFloppy() as fake:
        _register(fake)
        fake.route("POST", "/oauth/device/authorization", lambda r: (200, device))
        assert auth.start_login(store, fake.url, "Kodi on box")["user_code"] == "U"
        auth.start_login(store, fake.url, "Kodi on box")
        regs = [r for r in fake.requests if r.path == "/oauth/register"]
        assert len(regs) == 1 and regs[0].form == {"client_name": "Kodi on box"}
    assert store.get("client_id") == "cid"


def test_poll_handles_pending_then_slow_down_then_success(store):
    store.set("client_id", "cid")
    replies = iter(
        [(400, {"error": "authorization_pending"}), (400, {"error": "slow_down"}), (200, _tokens())]
    )
    sleeps: list[float] = []
    with FakeFloppy() as fake:
        fake.route("POST", "/oauth/token", lambda r: next(replies))
        ok = auth.poll_login(
            store, fake.url, {"device_code": "d", "interval": 5}, sleeps.append, lambda: False
        )
        assert fake.requests[0].form == {
            "client_id": "cid",
            "grant_type": DEVICE_GRANT,
            "device_code": "d",
        }
    assert ok and sleeps == [5, 5, 10]
    assert store.get("access_token") == "a1" and store.get("refresh_token") == "r1"
    assert int(store.get("access_expires_at")) > time.time() + 3000


def test_poll_stops_on_access_denied(store):
    store.set("client_id", "cid")
    with FakeFloppy() as fake:
        fake.route("POST", "/oauth/token", lambda r: (400, {"error": "access_denied"}))
        assert not auth.poll_login(
            store, fake.url, {"device_code": "d", "interval": 5}, lambda s: None, lambda: False
        )
    assert store.get("access_token") is None


def test_poll_cancelled(store):
    store.set("client_id", "cid")
    with FakeFloppy() as fake:
        assert not auth.poll_login(
            store, fake.url, {"device_code": "d", "interval": 5}, lambda s: None, lambda: True
        )


def _logged_in(store, expires_in=3600):
    store.set("client_id", "cid")
    store.set("access_token", "old")
    store.set("refresh_token", "oldr")
    store.set("access_expires_at", str(int(time.time()) + expires_in))


def test_call_refreshes_on_401_and_retries(store):
    _logged_in(store)
    seen = []

    def api(r):
        seen.append(r.headers["Authorization"])
        return (401, {}) if len(seen) == 1 else (200, {"ok": 1})

    with FakeFloppy() as fake:
        fake.route("GET", "/api/v1/x/", api)
        fake.route("POST", "/oauth/token", lambda r: (200, _tokens("new", "newr")))
        assert auth.call(store, fake.url + "/", "GET", "/api/v1/x/") == {"ok": 1}
        assert fake.requests[1].form == {
            "client_id": "cid",
            "grant_type": "refresh_token",
            "refresh_token": "oldr",
        }
    assert seen == ["Bearer old", "Bearer new"]
    assert store.get("refresh_token") == "newr"


def test_expiring_token_refreshed_before_call(store):
    _logged_in(store, expires_in=30)
    with FakeFloppy() as fake:
        fake.route("POST", "/oauth/token", lambda r: (200, _tokens("new", "newr")))
        assert auth.auth_headers(store, fake.url) == {"token": "new"}


def test_refresh_rejected_clears_tokens_and_raises_auth_required(store):
    _logged_in(store)
    with FakeFloppy() as fake:
        fake.route("GET", "/api/v1/x/", lambda r: (401, {}))
        fake.route("POST", "/oauth/token", lambda r: (400, {"error": "invalid_grant"}))
        with pytest.raises(AuthRequired):
            auth.call(store, fake.url, "GET", "/api/v1/x/")
    assert store.get("refresh_token") is None and store.get("access_token") is None
    assert store.get("access_expires_at") is None


def test_network_error_during_refresh_keeps_tokens(store):
    _logged_in(store, expires_in=-10)
    with pytest.raises(FloppyError):
        auth.auth_headers(store, "http://127.0.0.1:1")
    assert store.get("refresh_token") == "oldr"


def test_api_key_fallback_used_when_no_oauth(store):
    store.set("api_key", "k")
    with FakeFloppy() as fake:
        fake.route("GET", "/api/v1/x/", lambda r: (200, {"key": r.headers["X-API-Key"]}))
        assert auth.call(store, fake.url, "GET", "/api/v1/x/") == {"key": "k"}


def test_nothing_usable_raises_auth_required(store):
    with pytest.raises(AuthRequired):
        auth.auth_headers(store, "http://x")
