from __future__ import annotations

import pytest
import xbmc
from floppy import context_menu

CALLS: list[tuple[str, dict]] = []


@pytest.fixture(autouse=True)
def rpc():
    CALLS.clear()
    plays = {1: 0, 2: 0, 3: 1}  # episode id -> playcount
    xbmc.RPC_HANDLERS["VideoLibrary.GetEpisodes"] = lambda p: {
        "episodes": [{"episodeid": i} for i in plays]
    }
    xbmc.RPC_HANDLERS["VideoLibrary.GetEpisodeDetails"] = lambda p: {
        "episodedetails": {
            "playcount": plays[p["episodeid"]],
            "season": 1,
            "episode": p["episodeid"],
            "tvshowid": 9,
        }
    }
    xbmc.RPC_HANDLERS["VideoLibrary.GetTVShowDetails"] = lambda p: {
        "tvshowdetails": {"uniqueid": {"tvdb": "81189"}}
    }
    xbmc.RPC_HANDLERS["VideoLibrary.SetEpisodeDetails"] = lambda p: CALLS.append(("set", p)) or "OK"
    xbmc.RPC_HANDLERS["VideoLibrary.SetMovieDetails"] = lambda p: CALLS.append(("set", p)) or "OK"
    xbmc.RPC_HANDLERS["JSONRPC.NotifyAll"] = lambda p: CALLS.append(("notify", p)) or "OK"


def _labels(**kw):
    xbmc.INFO_LABELS.update({f"ListItem.{k}": str(v) for k, v in kw.items()})


def test_context_mark_season_marks_all_episodes():
    _labels(DBType="season", DBID=5, TVShowDBID=9, Season=1)
    context_menu.run()
    sets = [p for k, p in CALLS if k == "set"]
    assert sorted(p["episodeid"] for p in sets) == [1, 2]  # episode 3 already watched
    assert all(p["playcount"] == 1 for p in sets)
    assert CALLS[-1] == ("notify", {"sender": "script.floppy", "message": "floppy.flush"})
    assert sum(k == "notify" for k, _ in CALLS) == 1


def test_context_season_all_watched_unwatches_all():
    xbmc.RPC_HANDLERS["VideoLibrary.GetEpisodes"] = lambda p: {"episodes": [{"episodeid": 3}]}
    _labels(DBType="tvshow", DBID=9)
    context_menu.run()
    assert [p for k, p in CALLS if k == "set"] == [{"episodeid": 3, "playcount": 0}]


def test_context_unwatch_sets_playcount_zero():
    xbmc.RPC_HANDLERS["VideoLibrary.GetMovieDetails"] = lambda p: {
        "moviedetails": {"uniqueid": {"tmdb": "603"}, "playcount": 2}
    }
    _labels(DBType="movie", DBID=4, PlayCount=2)
    context_menu.run()
    assert CALLS == [
        ("set", {"movieid": 4, "playcount": 0}),
        ("notify", {"sender": "script.floppy", "message": "floppy.flush"}),
    ]
