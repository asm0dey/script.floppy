from __future__ import annotations

import time

import pytest
import xbmc
import xbmcaddon
import xbmcgui
from floppy import service_loop
from floppy.http import FloppyError
from floppy.merge import MergePlan
from floppy.store import Store

SYNCS: list[dict] = []
FLUSHES: list[str] = []


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "s.db"))


@pytest.fixture
def fake_sync(monkeypatch):
    """Record run_sync / flush_outbox calls instead of talking to a server."""
    SYNCS.clear()
    FLUSHES.clear()

    def run_sync(store, server, *, confirm, suppress, force_baseline=False):
        SYNCS.append({"force_baseline": force_baseline, "confirm": confirm})
        return "incremental"

    monkeypatch.setattr(service_loop, "run_sync", run_sync)
    monkeypatch.setattr(service_loop, "flush_outbox", lambda s, server: FLUSHES.append(server))
    xbmcaddon.SETTINGS["server_url"] = "http://floppy.test"


def _notifications():
    return [args for kind, args in xbmcgui.CALLS if kind == "notification"]


def test_tick_runs_sync_after_interval_not_before(store, fake_sync):
    service = service_loop.Service(store)
    service.tick(1000.0)  # service start
    service.tick(1000.0 + 299)
    assert len(SYNCS) == 1
    service.tick(1000.0 + 300)
    assert len(SYNCS) == 2


def test_poll_minutes_setting_sets_interval(store, fake_sync):
    xbmcaddon.SETTINGS["poll_minutes"] = "1"
    service = service_loop.Service(store)
    service.tick(1000.0)
    service.tick(1060.0)
    assert len(SYNCS) == 2


def test_wake_triggers_sync(store, fake_sync):
    service = service_loop.Service(store)
    service.tick(1000.0)
    service.onNotification("xbmc", "System.OnWake", "{}")
    assert len(SYNCS) == 1  # never inside the callback
    service.tick(1005.0)
    assert len(SYNCS) == 2


def test_sync_30s_after_playback_stops(store, fake_sync):
    service = service_loop.Service(store)
    now = time.monotonic()
    service.tick(now)
    service.playback_stopped()
    service.tick(now + 20)
    assert len(SYNCS) == 1
    service.tick(now + 31)
    assert len(SYNCS) == 2


def test_flush_notification_only_flushes(store, fake_sync):
    service = service_loop.Service(store)
    service.tick(1000.0)
    service.onNotification("script.floppy", "Other.floppy.flush", "null")
    service.tick(1005.0)
    assert len(SYNCS) == 1 and len(FLUSHES) == 1


def test_restore_forces_baseline_with_yesno(store, fake_sync):
    service = service_loop.Service(store)
    service.tick(1000.0)
    service.onNotification("script.floppy", "Other.floppy.restore", "null")
    service.tick(1005.0)
    assert SYNCS[-1]["force_baseline"] is True
    xbmcgui.YESNO_ANSWER = False
    assert SYNCS[-1]["confirm"](MergePlan(push_floppy=2)) is False
    assert ("yesno", ("Floppy", MergePlan(push_floppy=2).summary())) in xbmcgui.CALLS


def test_scan_finished_rebaselines_silently_once_baselined(store, fake_sync):
    service = service_loop.Service(store)
    service.tick(1000.0)
    store.set("baseline_done", "1")
    service.onNotification("xbmc", "VideoLibrary.OnScanFinished", "null")
    service.tick(1005.0)
    assert SYNCS[-1]["force_baseline"] is True
    assert SYNCS[-1]["confirm"](MergePlan()) is True
    assert not any(kind == "yesno" for kind, _ in xbmcgui.CALLS)


def test_scan_finished_before_first_baseline_is_a_normal_sync(store, fake_sync):
    service = service_loop.Service(store)
    service.tick(1000.0)
    service.onNotification("xbmc", "VideoLibrary.OnScanFinished", "null")
    service.tick(1005.0)
    assert SYNCS[-1]["force_baseline"] is False


def test_sync_disabled_only_flushes_on_timer_but_sync_now_still_syncs(store, fake_sync):
    xbmcaddon.SETTINGS["sync_enabled"] = "false"
    service = service_loop.Service(store)
    service.tick(1000.0)
    service.onNotification("xbmc", "System.OnWake", "{}")
    service.tick(1005.0)
    assert SYNCS == [] and len(FLUSHES) == 2  # start-up and wake: flush only
    service.onNotification("script.floppy", "Other.floppy.sync", "null")
    service.tick(1010.0)
    assert len(SYNCS) == 1


def test_no_server_url_no_network(store, fake_sync):
    xbmcaddon.SETTINGS["server_url"] = ""
    service = service_loop.Service(store)
    service.tick(1000.0)
    assert SYNCS == [] and FLUSHES == []


def test_api_key_setting_mirrored_to_store(store, fake_sync):
    xbmcaddon.SETTINGS["api_key"] = "k1"
    service = service_loop.Service(store)
    service.tick(1000.0)
    assert store.get("api_key") == "k1"
    xbmcaddon.SETTINGS["api_key"] = ""
    service.tick(1005.0)
    assert store.get("api_key") is None


def test_auth_required_notifies_once_and_pauses(store, monkeypatch):
    # Real run_sync: no tokens in the store, so the bootstrap call raises AuthRequired.
    xbmcaddon.SETTINGS["server_url"] = "http://127.0.0.1:9"
    attempts = []
    real = service_loop.run_sync
    monkeypatch.setattr(
        service_loop, "run_sync", lambda *a, **kw: attempts.append(1) or real(*a, **kw)
    )
    service = service_loop.Service(store)
    service.tick(1000.0)
    service.tick(2000.0)
    assert len(attempts) == 1
    assert _notifications() == [("Floppy", "Please log in again")]
    assert store.get("auth_needed") == "1"


def test_floppy_error_logs_and_retries_next_interval(store, fake_sync, monkeypatch):
    def fail(*a, **kw):
        SYNCS.append({})
        raise FloppyError(None, None, {}, "offline")

    monkeypatch.setattr(service_loop, "run_sync", fail)
    service = service_loop.Service(store)
    service.tick(1000.0)
    service.tick(1005.0)
    service.tick(1300.0)
    assert len(SYNCS) == 2
    assert _notifications() == []
    assert any(lvl == xbmc.LOGWARNING and msg.startswith("[Floppy]") for lvl, msg in xbmc.LOG)


def test_unexpected_error_is_logged_and_loop_survives(store, fake_sync, monkeypatch):
    monkeypatch.setattr(service_loop, "run_sync", lambda *a, **kw: 1 / 0)
    service = service_loop.Service(store)
    service.tick(1000.0)
    assert any(lvl == xbmc.LOGERROR and "ZeroDivisionError" in msg for lvl, msg in xbmc.LOG)


def test_baseline_result_toasts_summary(store, fake_sync, monkeypatch):
    store.set("last_summary", "Push 1 to Floppy, mark 2 watched in Kodi, unwatch 0")
    monkeypatch.setattr(service_loop, "run_sync", lambda *a, **kw: "baseline")
    service_loop.Service(store).tick(1000.0)
    assert _notifications() == [("Floppy", "Push 1 to Floppy, mark 2 watched in Kodi, unwatch 0")]


def test_incremental_toasts_only_with_notify_sync_and_changes(store, fake_sync, monkeypatch):
    def sync(store, server, *, confirm, suppress, force_baseline=False):
        suppress("movie:tmdb:603", 0)
        return "incremental"

    monkeypatch.setattr(service_loop, "run_sync", sync)
    service = service_loop.Service(store)
    service.tick(1000.0)
    assert _notifications() == []
    xbmcaddon.SETTINGS["notify_sync"] = "true"
    service.tick(1300.0)
    assert _notifications() == [("Floppy", "Updated 1 item from Floppy")]


def test_notifications_reach_the_watcher(store, fake_sync, monkeypatch):
    seen = []
    monkeypatch.setattr(
        service_loop.watcher, "handle_notification", lambda s, m, d, rs: seen.append((m, d))
    )
    service = service_loop.Service(store)
    service.onNotification("xbmc", "VideoLibrary.OnUpdate", '{"x": 1}')
    assert seen == [("VideoLibrary.OnUpdate", '{"x": 1}')]


def test_player_only_when_scrobbling_enabled(store, fake_sync):
    service = service_loop.Service(store)
    service.tick(1000.0)
    assert service.player is not None
    xbmcaddon.SETTINGS["scrobble_enabled"] = "false"
    service.tick(1005.0)
    assert service.player is None


def test_sync_echoes_arriving_after_a_long_pass_are_suppressed(store, fake_sync, monkeypatch):
    # Kodi queues OnUpdate until the service thread is back in waitForAbort.
    clock = [1000.0]
    monkeypatch.setattr(service_loop.watcher.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(service_loop.watcher, "_expected", {})
    xbmc.RPC_HANDLERS["VideoLibrary.GetMovieDetails"] = lambda p: {
        "moviedetails": {"uniqueid": {"tmdb": "603"}}
    }

    def sync(store, server, *, confirm, suppress, force_baseline=False):
        suppress("movie:tmdb:603", 1)
        suppress("movie:tmdb:603", 1)  # duplicate copy
        clock[0] += 45  # longer than the old 30 s TTL
        return "incremental"

    monkeypatch.setattr(service_loop, "run_sync", sync)
    service = service_loop.Service(store)
    service.tick(1000.0)
    data = '{"item": {"id": 7, "type": "movie"}, "playcount": 1}'
    service.onNotification("xbmc", "VideoLibrary.OnUpdate", data)
    service.onNotification("xbmc", "VideoLibrary.OnUpdate", data)
    assert store.pending() == []


def test_user_toggle_after_resume_only_write_is_pushed_and_flushed(store, fake_sync, monkeypatch):
    monkeypatch.setattr(service_loop.watcher, "_expected", {})
    xbmc.RPC_HANDLERS["VideoLibrary.GetMovieDetails"] = lambda p: {
        "moviedetails": {"uniqueid": {"tmdb": "603"}}
    }

    def sync(store, server, *, confirm, suppress, force_baseline=False):
        suppress("movie:tmdb:603", 0)  # resume point only; playcount stays 0
        return "incremental"

    monkeypatch.setattr(service_loop, "run_sync", sync)
    service = service_loop.Service(store)
    service.tick(1000.0)
    echo = '{"item": {"id": 7, "type": "movie"}, "playcount": 0}'
    service.onNotification("xbmc", "VideoLibrary.OnUpdate", echo)  # Kodi echoes the write
    assert store.pending() == []
    data = '{"item": {"id": 7, "type": "movie"}, "playcount": 1}'
    service.onNotification("xbmc", "VideoLibrary.OnUpdate", data)  # the user's toggle
    [(_, _, p)] = store.pending()
    assert p["action"] == "stop"
    service.tick(1005.0)  # next tick, not the next poll interval
    assert len(FLUSHES) == 1


def test_declined_preview_is_not_asked_again_by_timer(store, fake_sync, monkeypatch):
    calls = []

    def sync(store, server, *, confirm, suppress, force_baseline=False):
        calls.append(force_baseline)
        return "cancelled"

    monkeypatch.setattr(service_loop, "run_sync", sync)
    service = service_loop.Service(store)
    service.tick(1000.0)
    assert store.get("baseline_declined") == "1"
    service.tick(1300.0)  # timer
    service.onNotification("xbmc", "System.OnWake", "{}")
    service.tick(1305.0)
    service.onNotification("xbmc", "VideoLibrary.OnScanFinished", "null")
    service.tick(1310.0)
    assert len(calls) == 1 and len(FLUSHES) == 3  # automatic triggers only flush
    service.onNotification("script.floppy", "Other.floppy.sync", "null")
    service.tick(1315.0)
    assert len(calls) == 2  # Sync now asks again
    service.onNotification("script.floppy", "Other.floppy.restore", "null")
    service.tick(1320.0)
    assert calls[-1] is True


def test_declined_restore_after_baseline_does_not_block_auto_sync(store, fake_sync, monkeypatch):
    store.set("baseline_done", "1")
    monkeypatch.setattr(service_loop, "run_sync", lambda *a, **kw: "cancelled")
    service_loop.Service(store).tick(1000.0)
    assert store.get("baseline_declined") is None


def test_api_key_change_clears_auth_needed_and_is_not_rewritten(store, fake_sync, monkeypatch):
    xbmcaddon.SETTINGS["api_key"] = "k1"
    store.set("api_key", "k1")
    store.set("auth_needed", "1")
    writes = []
    real_set = store.set
    monkeypatch.setattr(store, "set", lambda k, v: writes.append(k) or real_set(k, v))
    service = service_loop.Service(store)
    service.tick(1000.0)
    assert "api_key" not in writes and store.get("auth_needed") == "1"  # unchanged token
    xbmcaddon.SETTINGS["api_key"] = "k2"
    service.tick(1005.0)
    assert store.get("api_key") == "k2" and store.get("auth_needed") is None
    assert len(SYNCS) == 1


def test_rejected_api_token_toasts_once(store):
    from fake_floppy import FakeFloppy

    xbmcaddon.SETTINGS["api_key"] = "bad"
    with FakeFloppy() as fake:
        fake.route("POST", "/api/v1/sync/bootstrap/", lambda r: (401, {"detail": "no"}))
        xbmcaddon.SETTINGS["server_url"] = fake.url
        service = service_loop.Service(store)
        service.tick(1000.0)
        service.tick(2000.0)
        assert len(fake.requests) == 1
    assert _notifications() == [("Floppy", "Floppy rejected the API token")]
    assert store.get("auth_needed") == "1"
