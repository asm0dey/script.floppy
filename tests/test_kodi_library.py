import xbmc
from floppy import kodi_library as kl
from floppy.refs import Ref, ref_from_floppy


def _register(movies=(), shows=(), episodes=()):
    xbmc.RPC_HANDLERS["VideoLibrary.GetMovies"] = lambda p: {"movies": list(movies)}
    xbmc.RPC_HANDLERS["VideoLibrary.GetTVShows"] = lambda p: {"tvshows": list(shows)}

    def get_episodes(p):
        s, e = p["limits"]["start"], p["limits"]["end"]
        return {"episodes": list(episodes)[s:e]}

    xbmc.RPC_HANDLERS["VideoLibrary.GetEpisodes"] = get_episodes


def test_load_library_movies_and_episodes():
    _register(
        movies=[
            {
                "movieid": 1,
                "uniqueid": {"imdb": "tt1"},
                "playcount": 2,
                "lastplayed": "2024-01-02 03:04:05",
                "resume": {"position": 10.5, "total": 100.0},
            }
        ],
        shows=[{"tvshowid": 5, "uniqueid": {"tvdb": "81189"}}],
        episodes=[
            {
                "episodeid": 7,
                "tvshowid": 5,
                "season": 1,
                "episode": 2,
                "lastplayed": "",
                "resume": {"position": 0, "total": 0},
            }
        ],
    )
    movie, ep = kl.load_library()
    assert (movie.kind, movie.dbid, movie.playcount, movie.resume_position) == ("movie", 1, 2, 10.5)
    assert movie.lastplayed == "2024-01-02 03:04:05"
    assert (ep.kind, ep.dbid, ep.playcount, ep.lastplayed) == ("episode", 7, 0, None)
    assert ep.ref.key() == "episode:tvdb:81189:1:2"


def test_episode_without_show_ids_has_no_ref():
    _register(
        shows=[{"tvshowid": 5, "uniqueid": {}}],
        episodes=[{"episodeid": 7, "tvshowid": 5, "season": 1, "episode": 2}],
    )
    assert kl.load_library() == []
    assert any("[Floppy]" in m and lvl == xbmc.LOGWARNING for lvl, m in xbmc.LOG)


def test_episodes_are_paged():
    eps = [{"episodeid": i, "tvshowid": 5, "season": 1, "episode": i} for i in range(1, 2501)]
    _register(shows=[{"tvshowid": 5, "uniqueid": {"tvdb": "1"}}], episodes=eps)
    assert len(kl.load_library()) == 2500


def test_index_matches_floppy_entry_by_any_id():
    _register(movies=[{"movieid": 1, "uniqueid": {"imdb": "tt1"}}])
    index = kl.LibraryIndex(kl.load_library())
    entry = {"media_type": "movie", "ids": {"tmdb": "9", "imdb": "tt1"}}
    ref = ref_from_floppy(entry)
    assert ref is not None
    found = index.find(ref)
    assert found is not None and found.dbid == 1
    assert len(index) == 1
    assert index.find(Ref("movie", (("tmdb", "9"),), None, None)) is None


def test_item_for_single_lookup():
    xbmc.RPC_HANDLERS["VideoLibrary.GetMovieDetails"] = lambda p: {
        "moviedetails": {"movieid": p["movieid"], "uniqueid": {"tmdb": "603"}, "playcount": 1}
    }
    item = kl.item_for("movie", 3)
    assert item is not None and item.dbid == 3 and item.ref.key() == "movie:tmdb:603"


def test_item_for_episode_uses_show_ids():
    xbmc.RPC_HANDLERS["VideoLibrary.GetEpisodeDetails"] = lambda p: {
        "episodedetails": {"episodeid": 7, "tvshowid": 5, "season": 1, "episode": 2}
    }
    xbmc.RPC_HANDLERS["VideoLibrary.GetTVShowDetails"] = lambda p: {
        "tvshowdetails": {"tvshowid": 5, "uniqueid": {"tvdb": "9"}}
    }
    item = kl.item_for("episode", 7)
    assert item is not None and item.ref.key() == "episode:tvdb:9:1:2"


def test_write_item_sends_set_episode_details():
    seen = {}

    def handler(params):
        seen.update(params)
        return {}

    xbmc.RPC_HANDLERS["VideoLibrary.SetEpisodeDetails"] = handler
    ref = Ref("episode", (("tvdb", "1"),), 1, 1)
    item = kl.KodiItem("episode", 7, ref, 0, None, 0.0, 0.0)
    kl.write_item(item, playcount=1, lastplayed="2024-01-02 03:04:05", resume=(0, 0))
    assert seen == {
        "episodeid": 7,
        "playcount": 1,
        "lastplayed": "2024-01-02 03:04:05",
        "resume": {"position": 0, "total": 0},
    }


def test_write_item_omits_none_fields():
    seen = {}
    xbmc.RPC_HANDLERS["VideoLibrary.SetMovieDetails"] = lambda p: seen.update(p) or {}
    item = kl.KodiItem("movie", 3, Ref("movie", (("tmdb", "1"),), None, None), 0, None, 0.0, 0.0)
    kl.write_item(item, playcount=2)
    assert seen == {"movieid": 3, "playcount": 2}


def test_time_roundtrip(monkeypatch):
    import time

    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    try:
        x = "2024-07-01 12:30:00"
        iso = kl.kodi_time_to_utc_iso(x)
        assert iso == "2024-07-01T10:30:00Z"
        assert kl.utc_iso_to_kodi_time(iso) == x
        assert kl.utc_iso_to_kodi_time("2024-07-01T10:30:00.123+00:00") == x
    finally:
        monkeypatch.undo()
        time.tzset()


def test_time_helpers_return_none_on_bad_input():
    assert kl.kodi_time_to_utc_iso("not a time") is None
    assert kl.kodi_time_to_utc_iso("9999-99-99 00:00:00") is None
    assert kl.utc_iso_to_kodi_time("garbage") is None
    assert kl.utc_iso_to_kodi_time(None) is None  # type: ignore[arg-type]


def test_kodi_time_parse_avoids_datetime_strptime(monkeypatch):
    # Kodi's embedded Python can leave datetime.strptime unusable on a second interpreter.
    class NoStrptime(kl.datetime):
        @classmethod
        def strptime(cls, *a):
            raise TypeError("'NoneType' object is not callable")

    monkeypatch.setattr(kl, "datetime", NoStrptime)
    assert kl.kodi_time_to_utc_iso("2024-07-01 12:30:00") is not None
