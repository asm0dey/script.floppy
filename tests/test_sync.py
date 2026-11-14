from __future__ import annotations

import pytest
import xbmc
from fake_floppy import FakeFloppy
from floppy.http import FloppyError
from floppy.kodi_library import utc_iso_to_kodi_time
from floppy.store import Store
from floppy.sync import run_sync

MOVIE_KEY = "movie:tmdb:10"
EP_KEY = "episode:tvdb:100:1:2"


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    s.set("api_key", "k")
    return s


@pytest.fixture
def kodi_lib():
    """Stub Kodi library: fill ``movies``/``episodes``; ``sets`` records Set*Details calls."""
    lib = {"movies": [], "episodes": [], "sets": []}
    shows = [{"tvshowid": 1, "uniqueid": {"tvdb": "100"}}]
    xbmc.RPC_HANDLERS.update(
        {
            "VideoLibrary.GetMovies": lambda p: {"movies": lib["movies"]},
            "VideoLibrary.GetTVShows": lambda p: {"tvshows": shows},
            "VideoLibrary.GetEpisodes": lambda p: {
                "episodes": lib["episodes"] if p["limits"]["start"] == 0 else []
            },
            "VideoLibrary.SetMovieDetails": lambda p: lib["sets"].append(p) or "OK",
            "VideoLibrary.SetEpisodeDetails": lambda p: lib["sets"].append(p) or "OK",
        }
    )
    return lib


def _movie(dbid, tmdb, playcount=0, position=0.0, total=0.0):
    return {
        "movieid": dbid,
        "uniqueid": {"tmdb": tmdb},
        "playcount": playcount,
        "lastplayed": "",
        "resume": {"position": position, "total": total},
    }


def _episode(dbid, playcount=0):
    return {
        "episodeid": dbid,
        "tvshowid": 1,
        "season": 1,
        "episode": 2,
        "uniqueid": {},
        "playcount": playcount,
        "lastplayed": "",
        "resume": {"position": 0, "total": 0},
    }


def _watched(tmdb, watched: object = True, play_count=1, watched_at=None):
    return {
        "media_type": "movie",
        "ids": {"tmdb": tmdb},
        "watched": watched,
        "play_count": play_count,
        "watched_at": watched_at,
    }


def _baseline_routes(fake, watched_pages=((),), progress=()):
    """Bootstrap + snapshots; ``watched_pages`` is a list of result lists, chained via ``after``."""
    bootstrap = {"contract": 2, "context": "ctx"}
    fake.route("POST", "/api/v1/sync/bootstrap/", lambda r: (200, bootstrap))

    def snapshot(r):
        if r.query["resource"] == "progress":
            return 200, {"results": [], "has_more": False, "after": None, "cursor": "cp"}
        n = int(r.query.get("after", "0"))
        more = n + 1 < len(watched_pages)
        body = {"results": list(watched_pages[n]), "has_more": more, "after": str(n + 1)}
        return 200, {**body, "cursor": "cw"}

    fake.route("GET", "/api/v1/sync/snapshot/", snapshot)
    progress_page = {"pagination": {}, "results": list(progress)}
    fake.route("GET", "/api/v1/playback/progress/", lambda r: (200, progress_page))


def _feed_routes(fake, watched=(), progress=()):
    for path, results in (
        ("/api/v1/sync/changes/", watched),
        ("/api/v1/sync/progress-changes/", progress),
    ):
        fake.route(
            "GET",
            path,
            lambda r, res=results: (
                200,
                {"results": list(res), "has_more": False, "next_cursor": r.query["cursor"] + "+"},
            ),
        )


def _synced(store):
    store.set("sync_context", "ctx")
    store.set("cursor_watched", "cw")
    store.set("cursor_progress", "cp")
    store.set("baseline_done", "1")


def _never(plan):
    raise AssertionError("confirm must not be called")


def test_first_baseline_asks_and_cancel_writes_nothing(store, kodi_lib):
    kodi_lib["movies"] = [_movie(1, "10"), _movie(2, "20", playcount=1)]
    asked = []
    with FakeFloppy() as fake:
        _baseline_routes(fake, [[_watched("10")]])
        status = run_sync(
            store, fake.url, confirm=lambda p: asked.append(p) or False, suppress=lambda k, pc: None
        )
        paths = [r.path for r in fake.requests]
    assert status == "cancelled"
    assert asked and asked[0].watch_kodi == 1 and asked[0].push_floppy == 1
    assert kodi_lib["sets"] == []
    assert store.pending() == []
    assert "/api/v1/scrobble/" not in paths
    for key in ("sync_context", "cursor_watched", "cursor_progress", "baseline_done"):
        assert store.get(key) is None


def test_first_baseline_applies_after_confirm(store, kodi_lib):
    kodi_lib["movies"] = [_movie(1, "10"), _movie(2, "20", playcount=1)]
    suppressed = []
    plans = []
    at = "2026-01-01T10:00:00Z"
    with FakeFloppy() as fake:
        _baseline_routes(fake, [[_watched("10", play_count=2, watched_at=at)]])
        fake.route("POST", "/api/v1/scrobble/", lambda r: (200, {}))
        status = run_sync(
            store,
            fake.url,
            confirm=lambda p: plans.append(p) or True,
            suppress=lambda k, pc: suppressed.append((k, pc)),
        )
        scrobbles = [r.json for r in fake.requests if r.path == "/api/v1/scrobble/"]
    assert status == "baseline"
    assert kodi_lib["sets"] == [
        {"movieid": 1, "playcount": 2, "lastplayed": utc_iso_to_kodi_time(at)}
    ]
    assert suppressed == [(MOVIE_KEY, 2)]
    assert [s["ids"] for s in scrobbles] == [{"tmdb": "20"}]
    assert store.pending() == []
    assert store.get("sync_context") == "ctx"
    assert (store.get("cursor_watched"), store.get("cursor_progress")) == ("cw", "cp")
    assert store.get("baseline_done") == "1"
    assert store.get("last_summary") == plans[0].summary()


def test_baseline_deferred_when_library_empty(store, kodi_lib):
    with FakeFloppy() as fake:
        _baseline_routes(fake, [[_watched("10")]])
        assert run_sync(store, fake.url, confirm=_never, suppress=lambda k, pc: None) == "deferred"
    assert kodi_lib["sets"] == []
    assert store.get("sync_context") is None


def test_sync_initializing_defers(store, kodi_lib):
    kodi_lib["movies"] = [_movie(1, "10")]
    with FakeFloppy() as fake:
        fake.route(
            "POST", "/api/v1/sync/bootstrap/", lambda r: (409, {"code": "sync_initializing"})
        )
        assert run_sync(store, fake.url, confirm=_never, suppress=lambda k, pc: None) == "deferred"
        assert len(fake.requests) == 1
    assert store.get("sync_context") is None


def test_snapshot_pages_followed_via_after(store, kodi_lib):
    kodi_lib["movies"] = [_movie(1, "10"), _movie(2, "20")]
    with FakeFloppy() as fake:
        _baseline_routes(fake, [[_watched("10")], [_watched("20")]])
        run_sync(store, fake.url, confirm=lambda p: True, suppress=lambda k, pc: None)
        afters = [
            r.query.get("after")
            for r in fake.requests
            if r.path == "/api/v1/sync/snapshot/" and r.query["resource"] == "watched"
        ]
    assert afters == [None, "1"]
    assert sorted(s["movieid"] for s in kodi_lib["sets"]) == [1, 2]


def test_incremental_applies_unwatch_from_feed(store, kodi_lib):
    _synced(store)
    kodi_lib["episodes"] = [_episode(7, playcount=1)]
    entry = {
        "media_type": "episode",
        "ids": {"tvdb": "100"},
        "season_number": 1,
        "episode_number": 2,
        "watched": False,
        "play_count": 0,
    }
    suppressed = []
    with FakeFloppy() as fake:
        _feed_routes(fake, watched=[entry])
        assert run_sync(
            store, fake.url, confirm=_never, suppress=lambda k, pc: suppressed.append((k, pc))
        ) == ("incremental")
        feed = next(r for r in fake.requests if r.path == "/api/v1/sync/changes/")
    assert feed.query == {"contract": "2", "context": "ctx", "cursor": "cw", "limit": "500"}
    assert kodi_lib["sets"] == [{"episodeid": 7, "playcount": 0}]
    assert suppressed == [(EP_KEY, 0)]
    assert (store.get("cursor_watched"), store.get("cursor_progress")) == ("cw+", "cp+")


def test_incremental_skips_item_with_pending_local_change(store, kodi_lib):
    _synced(store)
    kodi_lib["movies"] = [_movie(1, "10", playcount=1)]
    store.enqueue("scrobble", {"action": "stop", "ref_key": MOVIE_KEY})
    with FakeFloppy() as fake:
        fake.route("POST", "/api/v1/scrobble/", lambda r: (500, {}))
        _feed_routes(fake, watched=[_watched("10", watched=False)])
        run_sync(store, fake.url, confirm=_never, suppress=lambda k, pc: None)
    assert kodi_lib["sets"] == []
    assert len(store.pending()) == 1


def test_feed_skips_unknown_watched_value(store, kodi_lib):
    _synced(store)
    kodi_lib["movies"] = [_movie(1, "10", playcount=1), _movie(2, "20")]
    with FakeFloppy() as fake:
        _feed_routes(fake, watched=[_watched("10", watched=None), _watched("20", watched="yes")])
        run_sync(store, fake.url, confirm=_never, suppress=lambda k, pc: None)
    assert kodi_lib["sets"] == []


def test_watched_feed_raises_playcount_once_per_run(store, kodi_lib):
    _synced(store)
    kodi_lib["movies"] = [_movie(1, "10")]
    with FakeFloppy() as fake:
        _feed_routes(fake, watched=[_watched("10", play_count=0), _watched("10", play_count=0)])
        run_sync(store, fake.url, confirm=_never, suppress=lambda k, pc: None)
    assert kodi_lib["sets"] == [{"movieid": 1, "playcount": 1}]


def test_progress_feed_sets_and_clears_resume(store, kodi_lib):
    _synced(store)
    kodi_lib["movies"] = [_movie(1, "10", position=300, total=1000), _movie(2, "20")]
    progress = [
        {"media_type": "movie", "ids": {"tmdb": "10"}, "completed": True, "position_seconds": 0},
        {"media_type": "movie", "ids": {"tmdb": "20"}, "completed": False,
         "position_seconds": 60, "duration_seconds": 1200},
    ]  # fmt: skip
    suppressed = []
    with FakeFloppy() as fake:
        _feed_routes(fake, progress=progress)
        run_sync(store, fake.url, confirm=_never, suppress=lambda k, pc: suppressed.append((k, pc)))
    assert kodi_lib["sets"] == [
        {"movieid": 1, "resume": {"position": 0, "total": 0}},
        {"movieid": 2, "resume": {"position": 60, "total": 1200}},
    ]
    assert suppressed == [(MOVIE_KEY, 0), ("movie:tmdb:20", 0)]  # resume only: unchanged


def test_progress_feed_clears_resume_on_completed(store, kodi_lib):
    _synced(store)
    kodi_lib["movies"] = [_movie(1, "10", position=300, total=1000)]
    entry = {"media_type": "movie", "ids": {"tmdb": "10"}, "completed": True}
    with FakeFloppy() as fake:
        _feed_routes(fake, progress=[entry, entry])
        run_sync(store, fake.url, confirm=_never, suppress=lambda k, pc: None)
    assert kodi_lib["sets"] == [{"movieid": 1, "resume": {"position": 0, "total": 0}}]


def test_cursor_expired_triggers_silent_rebaseline(store, kodi_lib):
    _synced(store)
    store.set("sync_context", "old")
    kodi_lib["movies"] = [_movie(1, "10")]
    with FakeFloppy() as fake:
        fake.route("GET", "/api/v1/sync/changes/", lambda r: (409, {"code": "cursor_expired"}))
        _baseline_routes(fake, [[_watched("10")]])
        assert run_sync(store, fake.url, confirm=_never, suppress=lambda k, pc: None) == "baseline"
    assert store.get("sync_context") == "ctx"
    assert kodi_lib["sets"] == [{"movieid": 1, "playcount": 1}]


def test_force_baseline_asks_again(store, kodi_lib):
    _synced(store)
    kodi_lib["movies"] = [_movie(1, "10")]
    with FakeFloppy() as fake:
        _baseline_routes(fake, [[_watched("10")]])
        status = run_sync(
            store,
            fake.url,
            confirm=lambda p: False,
            suppress=lambda k, pc: None,
            force_baseline=True,
        )
    assert status == "cancelled"
    assert kodi_lib["sets"] == []
    assert store.get("cursor_watched") == "cw"  # untouched old state


def test_outbox_flushed_before_pull(store, kodi_lib):
    _synced(store)
    store.enqueue("scrobble", {"action": "stop", "ref_key": "x"})
    with FakeFloppy() as fake:
        fake.route("POST", "/api/v1/scrobble/", lambda r: (200, {}))
        _feed_routes(fake)
        run_sync(store, fake.url, confirm=_never, suppress=lambda k, pc: None)
        paths = [r.path for r in fake.requests]
    assert paths.index("/api/v1/scrobble/") < paths.index("/api/v1/sync/changes/")


def test_cursor_stored_per_page(store, kodi_lib):
    _synced(store)
    kodi_lib["movies"] = [_movie(1, "10")]

    def changes(r):
        if r.query["cursor"] == "cw":
            return 200, {"results": [_watched("10")], "has_more": True, "next_cursor": "c2"}
        return 500, {}

    with FakeFloppy() as fake:
        fake.route("GET", "/api/v1/sync/changes/", changes)
        with pytest.raises(FloppyError):
            run_sync(store, fake.url, confirm=_never, suppress=lambda k, pc: None)
    assert store.get("cursor_watched") == "c2"
    assert kodi_lib["sets"] == [{"movieid": 1, "playcount": 1}]


def test_baseline_honours_pending_outbox(store, kodi_lib):
    # A stop queued while Floppy was failing: Kodi watched both, Floppy unwatched "20".
    kodi_lib["movies"] = [_movie(1, "10", playcount=1), _movie(2, "20", playcount=1)]
    store.enqueue("scrobble", {"action": "stop", "ref_key": MOVIE_KEY})
    store.enqueue("scrobble", {"action": "stop", "ref_key": "movie:tmdb:20"})
    plans = []
    with FakeFloppy() as fake:
        fake.route("POST", "/api/v1/scrobble/", lambda r: (500, {}))
        _baseline_routes(fake, [[_watched("20", watched=False)]])
        run_sync(store, fake.url, confirm=lambda p: plans.append(p) or True,
                 suppress=lambda k, pc: None)  # fmt: skip
    assert kodi_lib["sets"] == []  # no unwatch of "20"
    assert [p["ref_key"] for _, _, p in store.pending()] == [MOVIE_KEY, "movie:tmdb:20"]
    assert (plans[0].push_floppy, plans[0].unwatch_kodi) == (0, 0)  # no catch-up for "10"


def test_bad_feed_entry_is_skipped_not_stalling(store, kodi_lib, monkeypatch):
    _synced(store)
    kodi_lib["movies"] = [_movie(1, "10"), _movie(2, "20")]
    from floppy import sync

    real = sync._apply

    def apply(feed, entry, item, suppress):
        if item.dbid == 1:
            raise TypeError("bad entry")
        real(feed, entry, item, suppress)

    monkeypatch.setattr(sync, "_apply", apply)
    with FakeFloppy() as fake:
        _feed_routes(fake, watched=[_watched("10"), _watched("20", watched_at="garbage")])
        assert run_sync(store, fake.url, confirm=_never, suppress=lambda k, pc: None) == (
            "incremental"
        )
    assert kodi_lib["sets"] == [{"movieid": 2, "playcount": 1}]  # unparseable time: no lastplayed
    assert store.get("cursor_watched") == "cw+"
    assert any(lvl == xbmc.LOGWARNING and "bad entry" in m for lvl, m in xbmc.LOG)


def test_invalid_context_rebaselines(store, kodi_lib):
    _synced(store)
    kodi_lib["movies"] = [_movie(1, "10")]
    with FakeFloppy() as fake:
        fake.route("GET", "/api/v1/sync/changes/", lambda r: (400, {"code": "invalid_context"}))
        _baseline_routes(fake, [[_watched("10")]])
        assert run_sync(store, fake.url, confirm=_never, suppress=lambda k, pc: None) == (
            "baseline"
        )


def _cursorless_routes(fake, boot_cursors):
    boot = {"contract": 2, "context": "ctx", "cursors": boot_cursors}
    fake.route("POST", "/api/v1/sync/bootstrap/", lambda r: (200, boot))
    page = {"results": [_watched("10")], "has_more": False, "after": None}
    fake.route("GET", "/api/v1/sync/snapshot/", lambda r: (200, page))
    fake.route("GET", "/api/v1/playback/progress/", lambda r: (200, {"results": []}))


def test_snapshot_without_cursor_falls_back_to_bootstrap_cursors(store, kodi_lib):
    kodi_lib["movies"] = [_movie(1, "10")]
    with FakeFloppy() as fake:
        _cursorless_routes(fake, {"watched": "bw", "progress": "bp"})
        assert run_sync(store, fake.url, confirm=lambda p: True, suppress=lambda k, pc: None) == (
            "baseline"
        )
    assert (store.get("cursor_watched"), store.get("cursor_progress")) == ("bw", "bp")


def test_no_cursor_anywhere_aborts_baseline_before_confirm(store, kodi_lib):
    kodi_lib["movies"] = [_movie(1, "10")]
    with FakeFloppy() as fake:
        _cursorless_routes(fake, {"watched": "bw"})
        assert run_sync(store, fake.url, confirm=_never, suppress=lambda k, pc: None) == (
            "deferred"
        )
    assert kodi_lib["sets"] == []
    for key in ("sync_context", "cursor_watched", "cursor_progress", "baseline_done"):
        assert store.get(key) is None
    assert any(lvl == xbmc.LOGERROR and "cursor" in m for lvl, m in xbmc.LOG)
