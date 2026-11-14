from __future__ import annotations

import time

import pytest
import xbmc
from fake_floppy import FakeFloppy
from floppy.player import FloppyPlayer
from floppy.store import Store
from floppy.sync import flush_outbox

MOVIE_UID = {"tmdb": "603"}


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    s.set("api_key", "k")
    return s


def _library(kind="movie", uniqueid=MOVIE_UID):
    xbmc.RPC_HANDLERS["Player.GetActivePlayers"] = lambda p: [{"playerid": 1, "type": "video"}]
    xbmc.RPC_HANDLERS["Player.GetItem"] = lambda p: {"item": {"id": 7, "type": kind}}
    if kind == "movie":
        xbmc.RPC_HANDLERS["VideoLibrary.GetMovieDetails"] = lambda p: {
            "moviedetails": {"uniqueid": uniqueid}
        }
    else:
        xbmc.RPC_HANDLERS["VideoLibrary.GetEpisodeDetails"] = lambda p: {
            "episodedetails": {"season": 2, "episode": 5, "tvshowid": 3}
        }
        xbmc.RPC_HANDLERS["VideoLibrary.GetTVShowDetails"] = lambda p: {
            "tvshowdetails": {"uniqueid": {"tvdb": "81189"}}
        }


def _player(store, server="http://127.0.0.1:1", stopped=None):
    return FloppyPlayer(store, server, stopped or (lambda: None), spawn=lambda fn: fn())


def _play(player, position, total):
    player.total = total
    player.time = position
    player.onAVStarted()
    player.tick()


def test_movie_stop_enqueues_scrobble_and_progress(store):
    _library()
    p = _player(store)
    _play(p, 8150, 8160)
    p.onPlayBackStopped()
    (_, k1, scrobble), (_, k2, progress) = store.pending()
    assert (k1, k2) == ("scrobble", "progress")
    assert {k: v for k, v in scrobble.items() if k != "idempotency_key"} == {
        "action": "stop",
        "media_type": "movie",
        "ids": {"tmdb": "603"},
        "position_seconds": 8150,
        "duration_seconds": 8160,
        "ref_key": "movie:tmdb:603",
    }
    assert progress["position_seconds"] is None and progress["completed"] is True
    assert progress["ref_key"] == "movie:tmdb:603"
    assert p.recently_stopped("movie:tmdb:603")
    assert not p.recently_stopped("movie:tmdb:1")


def test_partial_stop_keeps_position(store):
    _library()
    p = _player(store)
    _play(p, 600, 8160)
    p.onPlayBackStopped()
    assert store.pending()[1][2]["position_seconds"] == 600
    assert store.pending()[1][2]["completed"] is False


def test_ended_uses_full_duration(store):
    _library()
    p = _player(store)
    _play(p, 100, 8160)
    p.onPlayBackEnded()
    assert store.pending()[0][2]["position_seconds"] == 8160


def test_episode_scrobble_has_show_ids_and_numbers(store):
    _library("episode")
    p = _player(store)
    _play(p, 100, 2400)
    p.onPlayBackStopped()
    body = store.pending()[0][2]
    assert body["ids"] == {"tvdb": "81189"}
    assert (body["season_number"], body["episode_number"]) == (2, 5)


def test_start_sent_immediately_not_queued(store):
    _library()
    with FakeFloppy() as fake:
        fake.route("POST", "/api/v1/scrobble/", lambda r: (200, {}))
        p = _player(store, fake.url)
        _play(p, 0, 8160)
        (req,) = fake.requests
    assert req.json["action"] == "start" and req.json["ids"] == {"tmdb": "603"}
    assert store.pending() == []


def test_pause_and_resume_actions(store):
    _library()
    with FakeFloppy() as fake:
        fake.route("POST", "/api/v1/scrobble/", lambda r: (200, {}))
        p = _player(store, fake.url)
        _play(p, 0, 8160)
        p.time = 50
        p.onPlayBackPaused()
        p.onPlayBackResumed()
        actions = [(r.json["action"], r.json["position_seconds"]) for r in fake.requests]
    assert actions == [("start", 0), ("pause", 50), ("start", 50)]


def test_live_send_errors_are_swallowed(store):
    _library()
    p = _player(store, "http://127.0.0.1:1")
    _play(p, 0, 8160)
    assert store.pending() == []


def test_stop_while_offline_is_queued_and_flushed_later(store):
    _library()
    called = []
    p = _player(store, "http://127.0.0.1:1", lambda: called.append(time.monotonic()))
    _play(p, 600, 8160)
    t0 = time.monotonic()
    p.onPlayBackStopped()
    assert called and called[0] - t0 < 0.5
    assert len(store.pending()) == 2
    with FakeFloppy() as fake:
        fake.route("POST", "/api/v1/scrobble/", lambda r: (200, {}))
        fake.route("PUT", "/api/v1/playback/progress/", lambda r: (200, {}))
        assert flush_outbox(store, fake.url) == 2
    assert store.pending() == []


def test_item_without_ids_is_not_scrobbled(store):
    _library(uniqueid={"unknown": "x"})
    called = []
    p = _player(store, stopped=lambda: called.append(1))
    _play(p, 100, 8160)
    p.onPlayBackStopped()
    assert store.pending() == [] and called == []


def test_non_library_movie_uses_infotag(store):
    xbmc.RPC_HANDLERS["Player.GetActivePlayers"] = lambda p: [{"playerid": 1, "type": "video"}]
    xbmc.RPC_HANDLERS["Player.GetItem"] = lambda p: {"item": {"type": "unknown"}}
    p = _player(store)
    p.infotag.media_type = "movie"
    p.infotag.unique_ids = {"imdb": "tt0133093"}
    _play(p, 100, 8160)
    p.onPlayBackStopped()
    assert store.pending()[0][2]["ids"] == {"imdb": "tt0133093"}


def test_non_library_episode_is_skipped(store):
    xbmc.RPC_HANDLERS["Player.GetActivePlayers"] = lambda p: [{"playerid": 1, "type": "video"}]
    xbmc.RPC_HANDLERS["Player.GetItem"] = lambda p: {"item": {"type": "unknown"}}
    p = _player(store)
    p.infotag.media_type = "episode"
    p.infotag.unique_ids = {"tvdb": "1"}
    _play(p, 100, 2400)
    p.onPlayBackStopped()
    assert store.pending() == []
