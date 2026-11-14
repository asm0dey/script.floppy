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
    watcher._suppressed.clear()


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
    watcher.suppress("movie:tmdb:603")
    _note(store, 1)
    assert store.pending() == []
    _note(store, 1)  # guard is consumed on first match
    assert len(store.pending()) == 1


def test_expired_suppression_is_ignored(store, monkeypatch):
    watcher.suppress("k")
    monkeypatch.setattr(watcher.time, "monotonic", lambda: 10_000_000.0)
    assert not watcher.is_suppressed("k")


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
