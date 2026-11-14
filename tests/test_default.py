from __future__ import annotations

from pathlib import Path

import pytest
import xbmc
import xbmcaddon
import xbmcgui
from fake_floppy import FakeFloppy
from floppy import actions
from floppy.store import Store

NOTIFIED: list[dict] = []


@pytest.fixture(autouse=True)
def notify_all():
    NOTIFIED.clear()
    xbmc.RPC_HANDLERS["JSONRPC.NotifyAll"] = lambda p: NOTIFIED.append(p) or "OK"


def _store() -> Store:
    # The same file the action opens: <profile>/state.db
    return Store(str(Path(xbmcaddon.INFO["profile"]) / "state.db"))


def test_login_shows_code_and_stores_tokens():
    xbmc.INFO_LABELS["System.FriendlyName"] = "Living room"
    device = {
        "device_code": "d",
        "user_code": "ABCD-1234",
        "verification_uri": "https://floppy.test/device",
        "interval": 0,
        "expires_in": 60,
    }
    tokens = {"access_token": "a1", "refresh_token": "r1", "expires_in": 3600}
    with FakeFloppy() as fake:
        fake.route("POST", "/oauth/register", lambda r: (200, {"client_id": "cid"}))
        fake.route("POST", "/oauth/device/authorization", lambda r: (200, device))
        fake.route("POST", "/oauth/token", lambda r: (200, tokens))
        xbmcaddon.SETTINGS["server_url"] = fake.url
        Path(xbmcaddon.INFO["profile"]).mkdir(parents=True)
        _store().set("auth_needed", "1")
        actions.main(["default.py", "login"])
        assert fake.requests[0].form == {"client_name": "Kodi on Living room"}
    created = [args for kind, args in xbmcgui.CALLS if kind == "progress.create"]
    assert len(created) == 1
    assert "ABCD-1234" in created[0][1] and "https://floppy.test/device" in created[0][1]
    store = _store()
    assert store.get("access_token") == "a1" and store.get("refresh_token") == "r1"
    assert store.get("auth_needed") is None
    assert ("notification", ("Floppy", "Logged in to Floppy")) in xbmcgui.CALLS
    assert NOTIFIED == [{"sender": "script.floppy", "message": "floppy.sync"}]


def test_login_cancelled_stores_nothing():
    xbmcgui.PROGRESS_CANCELED = True
    device = {"device_code": "d", "user_code": "U", "verification_uri": "v", "interval": 0}
    with FakeFloppy() as fake:
        fake.route("POST", "/oauth/register", lambda r: (200, {"client_id": "cid"}))
        fake.route("POST", "/oauth/device/authorization", lambda r: (200, device))
        xbmcaddon.SETTINGS["server_url"] = fake.url
        actions.main(["default.py", "login"])
    assert _store().get("access_token") is None
    assert NOTIFIED == []


SYNC_KEYS = (
    "access_token",
    "refresh_token",
    "access_expires_at",
    "api_key",
    "sync_context",
    "cursor_watched",
    "cursor_progress",
    "baseline_done",
    "last_summary",
    "auth_needed",
)


@pytest.mark.parametrize("revoke_status", [200, 500])
def test_logout_clears_sync_state(revoke_status):
    Path(xbmcaddon.INFO["profile"]).mkdir(parents=True)
    store = _store()
    for key in SYNC_KEYS:
        store.set(key, "x")
    store.set("refresh_token", "r1")
    store.set("client_id", "cid")
    with FakeFloppy() as fake:
        fake.route("POST", "/oauth/revoke", lambda r: (revoke_status, {}))
        xbmcaddon.SETTINGS["server_url"] = fake.url
        actions.main(["default.py", "logout"])
        assert fake.requests[0].form == {"token": "r1", "client_id": "cid"}
    assert all(store.get(k) is None for k in SYNC_KEYS)
    assert store.get("client_id") == "cid"


@pytest.mark.parametrize(
    ("action", "message"),
    [
        ("sync_now", "floppy.sync"),
        ("restore", "floppy.restore"),
        ("push_history", "floppy.restore"),
    ],
)
def test_sync_actions_ask_the_service(action, message):
    actions.main(["default.py", action])
    assert NOTIFIED == [{"sender": "script.floppy", "message": message}]
