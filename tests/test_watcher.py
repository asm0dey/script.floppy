from __future__ import annotations

import json

import pytest
import xbmc
from floppy import watcher
from floppy.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "s.db"))


@pytest.fixture(autouse=True)
def _clear_guard():
    watcher._expected.clear()


def _movie(uid=None):
    xbmc.RPC_HANDLERS["VideoLibrary.GetMovieDetails"] = lambda p: {
        "moviedetails": {"uniqueid": uid or {"tmdb": "603"}}
    }


def _note(store, playcount, stopped=lambda k: False, kind="movie"):
    data = json.dumps({"item": {"id": 7, "type": kind}, "playcount": playcount})
    watcher.handle_notification(store, "VideoLibrary.OnUpdate", data, stopped)


def test_kodi_ui_toggle_pushes_watched(store):
    _movie()
    _note(store, 1)
    [(_, kind, p)] = store.pending()
    assert kind == "scrobble"
    assert (p["action"], p["manual"], p["completed"]) == ("stop", True, True)
    assert p["ref_key"] == "movie:tmdb:603"
    assert p["played_at"].endswith("Z")


def test_kodi_ui_unwatch_pushes_unwatch(store):
    _movie()
    _note(store, 0)
    [(_, _, p)] = store.pending()
    assert p["action"] == "unwatch"
    assert p["ref_key"] == "movie:tmdb:603"


def test_update_from_sync_is_not_echoed(store):
    _movie()
    watcher.suppress("movie:tmdb:603", 1)
    _note(store, 1)
    assert store.pending() == []
    _note(store, 1)  # one expectation, one notification
    assert len(store.pending()) == 1


def test_notifications_after_a_long_pass_are_still_suppressed(store, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(watcher.time, "monotonic", lambda: clock[0])
    _movie()
    watcher.suppress("movie:tmdb:603", 1)  # written at the start of the pass
    clock[0] = 1045.0  # the pass took 45 s; Kodi delivers OnUpdate only now
    watcher.arm_expiry()
    clock[0] = 1046.0
    _note(store, 1)
    assert store.pending() == []


def test_expectation_expires_60s_after_arming(store, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(watcher.time, "monotonic", lambda: clock[0])
    watcher.suppress("k", 1)
    clock[0] = 5000.0  # unarmed: never expires
    assert watcher.is_suppressed("k", 1)
    watcher.suppress("k", 1)
    watcher.arm_expiry()
    clock[0] = 5061.0
    assert not watcher.is_suppressed("k", 1)


def test_duplicate_copies_each_suppressed(store):
    _movie()
    watcher.suppress("movie:tmdb:603", 1)
    watcher.suppress("movie:tmdb:603", 1)  # two Kodi copies of one movie
    _note(store, 1)
    _note(store, 1)
    assert store.pending() == []


def test_two_writes_to_one_item_match_by_value(store):
    _movie()
    watcher.suppress("movie:tmdb:603", 1)
    watcher.suppress("movie:tmdb:603", 3)
    _note(store, 3)
    _note(store, 1)
    assert store.pending() == []


def test_toggle_to_a_different_value_is_pushed(store):
    _movie()
    watcher.suppress("movie:tmdb:603", 1)
    _note(store, 0)  # the user unwatched it: not the value sync wrote
    [(_, _, p)] = store.pending()
    assert p["action"] == "unwatch"


def test_playback_end_playcount_not_double_pushed(store):
    _movie()
    _note(store, 1, stopped=lambda k: True)
    assert store.pending() == []


def test_ignores_noise(store):
    watcher.handle_notification(store, "Player.OnPlay", "{}", lambda k: False)
    watcher.handle_notification(store, "VideoLibrary.OnUpdate", "not json", lambda k: False)
    watcher.handle_notification(
        store, "VideoLibrary.OnUpdate", json.dumps({"item": {"id": 7, "type": "movie"}}), bool
    )
    _note(store, 1, kind="tvshow")
    xbmc.RPC_HANDLERS["VideoLibrary.GetMovieDetails"] = lambda p: {"moviedetails": {}}
    _note(store, 1)  # no usable ids
    assert store.pending() == []


def test_failing_enqueue_is_logged_not_raised(store, monkeypatch):
    _movie()

    def boom(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(store, "enqueue", boom)
    _note(store, 1)
    assert any(lvl == xbmc.LOGWARNING and "db locked" in m for lvl, m in xbmc.LOG)
