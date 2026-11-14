import hashlib

from floppy.kodi_library import KodiItem, kodi_time_to_utc_iso, utc_iso_to_kodi_time
from floppy.merge import KodiWrite, MergePlan, plan_baseline
from floppy.refs import Ref

MOVIE = Ref("movie", (("imdb", "tt1"), ("tmdb", "11")), None, None)


def _item(playcount=0, lastplayed=None, resume=0.0, total=0.0, dbid=1, ref=MOVIE):
    return KodiItem("movie", dbid, ref, playcount, lastplayed, resume, total)


def _w(watched: object = True, play_count=1, watched_at=None, ids=None):
    return {
        "media_type": "movie",
        "ids": ids or {"imdb": "tt1"},
        "watched": watched,
        "play_count": play_count,
        "watched_at": watched_at,
    }


def _p(
    position: float | None = 600,
    duration: float | None = 6000,
    completed=False,
    updated_at="2024-01-01T00:00:00Z",
    ids=None,
):
    return {
        "media_type": "movie",
        "ids": ids or {"tmdb": "11"},
        "position_seconds": position,
        "duration_seconds": duration,
        "completed": completed,
        "updated_at": updated_at,
    }


def test_floppy_watched_kodi_not_sets_kodi():
    item = _item()
    plan = plan_baseline([item], [_w(play_count=3, watched_at="2024-05-06T07:08:09Z")], [])
    assert plan.to_kodi == [KodiWrite(item, 3, utc_iso_to_kodi_time("2024-05-06T07:08:09Z"), None)]
    assert (plan.watch_kodi, plan.push_floppy, plan.unwatch_kodi) == (1, 0, 0)
    assert plan.to_floppy == []


def test_floppy_watched_kodi_not_min_one_play_no_date():
    item = _item()
    plan = plan_baseline([item], [_w(play_count=0, watched_at=None)], [])
    assert plan.to_kodi == [KodiWrite(item, 1, None, None)]


def test_kodi_watched_floppy_has_no_state_pushes_with_lastplayed():
    item = _item(playcount=2, lastplayed="2024-03-04 05:06:07")
    plan = plan_baseline([item], [], [])
    key = MOVIE.key()
    assert plan.to_floppy == [
        (
            "scrobble",
            {
                **MOVIE.payload(),
                "action": "stop",
                "manual": True,
                "completed": True,
                "played_at": kodi_time_to_utc_iso("2024-03-04 05:06:07"),
                "ref_key": key,
                "idempotency_key": hashlib.sha256(
                    f"{key}|{kodi_time_to_utc_iso('2024-03-04 05:06:07')}".encode()
                ).hexdigest(),
            },
        )
    ]
    assert plan.push_floppy == 1
    assert plan.to_kodi == []


def test_kodi_watched_without_lastplayed_omits_played_at():
    plan = plan_baseline([_item(playcount=1)], [], [])
    ((kind, payload),) = plan.to_floppy
    assert kind == "scrobble"
    assert "played_at" not in payload
    assert payload["idempotency_key"] == hashlib.sha256(f"{MOVIE.key()}|".encode()).hexdigest()


def test_kodi_watched_floppy_explicitly_unwatched_unwatches_kodi():
    item = _item(playcount=1, lastplayed="2024-03-04 05:06:07")
    plan = plan_baseline([item], [_w(watched=False, play_count=0)], [])
    assert plan.to_kodi == [KodiWrite(item, 0, None, None)]
    assert plan.unwatch_kodi == 1
    assert plan.to_floppy == []


def test_both_watched_raises_kodi_count_only():
    low = _item(playcount=1)
    plan = plan_baseline([low], [_w(play_count=3)], [])
    assert plan.to_kodi == [KodiWrite(low, 3, None, None)]
    assert (plan.watch_kodi, plan.push_floppy) == (0, 0)
    assert plan.to_floppy == []

    high = _item(playcount=3)
    plan = plan_baseline([high], [_w(play_count=1)], [])
    assert plan.to_kodi == []
    assert plan.to_floppy == []


def test_both_unwatched_nothing():
    for watched in ([], [_w(watched=False, play_count=0)]):
        plan = plan_baseline([_item()], watched, [])
        assert plan.to_kodi == []
        assert plan.to_floppy == []
        assert (plan.watch_kodi, plan.push_floppy, plan.unwatch_kodi, plan.unmatched) == (0,) * 4


def test_resume_from_floppy_when_kodi_has_none():
    item = _item()
    plan = plan_baseline([item], [], [_p(position=600, duration=6000)])
    assert plan.to_kodi == [KodiWrite(item, None, None, (600.0, 6000.0))]
    assert plan.to_floppy == []


def test_floppy_completed_or_zero_progress_is_no_resume():
    for p in (_p(completed=True), _p(position=None), _p(position=0)):
        assert plan_baseline([_item()], [], [p]).to_kodi == []


def test_kodi_resume_pushed_when_floppy_has_none():
    plan = plan_baseline([_item(resume=120.7, total=5400.2)], [], [_p(completed=True)])
    assert plan.to_floppy == [
        (
            "progress",
            {
                **MOVIE.payload(),
                "position_seconds": 120,
                "duration_seconds": 5400,
                "completed": False,
                "ref_key": MOVIE.key(),
            },
        )
    ]
    assert plan.push_floppy == 0
    assert plan.to_kodi == []


def test_kodi_resume_kept_when_kodi_lastplayed_newer():
    item = _item(resume=300, total=6000, lastplayed="2024-06-10 12:00:00")
    plan = plan_baseline([item], [], [_p(position=900, updated_at="2024-06-01T00:00:00Z")])
    assert plan.to_kodi == []
    assert plan.to_floppy == [
        (
            "progress",
            {
                **MOVIE.payload(),
                "position_seconds": 300,
                "duration_seconds": 6000,
                "completed": False,
                "ref_key": MOVIE.key(),
            },
        )
    ]


def test_floppy_resume_wins_when_newer_or_kodi_has_no_lastplayed():
    for lastplayed in ("2024-06-01 12:00:00", None):
        item = _item(resume=300, total=6000, lastplayed=lastplayed)
        plan = plan_baseline(
            [item], [], [_p(position=900, updated_at="2024-06-10T00:00:00.123456+00:00")]
        )
        assert plan.to_kodi == [KodiWrite(item, None, None, (900.0, 6000.0))]
        assert plan.to_floppy == []


def test_resume_tie_break_under_five_seconds_does_nothing():
    item = _item(resume=300, total=6000, lastplayed="2024-06-10 12:00:00")
    plan = plan_baseline([item], [], [_p(position=304.9, updated_at="2024-06-01T00:00:00Z")])
    assert plan.to_kodi == []
    assert plan.to_floppy == []


def test_newly_watched_drops_floppy_resume_and_merges_into_one_write():
    item = _item()
    plan = plan_baseline([item], [_w()], [_p()])
    assert plan.to_kodi == [KodiWrite(item, 1, None, None)]


def test_newly_watched_does_not_push_kodi_resume():
    item = _item(resume=300, total=6000, lastplayed="2024-06-10 12:00:00")
    for progress in ([], [_p(position=900, updated_at="2024-06-01T00:00:00Z")]):
        plan = plan_baseline([item], [_w()], progress)
        assert plan.to_floppy == []
        assert plan.to_kodi == [KodiWrite(item, 1, None, None)]  # Kodi resume left unchanged


def test_unwatch_and_resume_merge_into_one_write():
    item = _item(playcount=1)
    plan = plan_baseline([item], [_w(watched=False)], [_p(position=600, duration=6000)])
    assert plan.to_kodi == [KodiWrite(item, 0, None, (600.0, 6000.0))]


def test_unmatched_floppy_entries_are_counted_not_applied():
    other = {"imdb": "tt999"}
    plan = plan_baseline(
        [_item()],
        [_w(ids=other), _w(ids=other), {"media_type": "movie", "ids": {}, "watched": True}],
        [_p(ids=other)],
    )
    assert plan.to_kodi == []
    assert plan.to_floppy == []
    assert plan.unmatched == 2  # tt999 once across both lists, plus the id-less entry


def test_duplicate_floppy_entries_for_one_item_use_first():
    item = _item()
    plan = plan_baseline(
        [item], [_w(watched=False, ids={"imdb": "tt1"}), _w(play_count=5, ids={"tmdb": "11"})], []
    )
    assert plan.to_kodi == []
    assert plan.unmatched == 0


def test_summary_text():
    plan = MergePlan([], [], unwatch_kodi=3, watch_kodi=1234, push_floppy=12, unmatched=0)
    assert plan.summary() == "Push 12 to Floppy, mark 1234 watched in Kodi, unwatch 3"
    plan.unmatched = 7
    assert plan.summary() == (
        "Push 12 to Floppy, mark 1234 watched in Kodi, unwatch 3 (7 not in Kodi library)"
    )


def test_catch_up_idempotency_key_is_device_independent():
    lastplayed = "2024-03-04 05:06:07"
    a = plan_baseline([_item(playcount=1, lastplayed=lastplayed, dbid=1)], [], [])
    b = plan_baseline([_item(playcount=4, lastplayed=lastplayed, dbid=99)], [], [])
    assert a.to_floppy[0][1]["idempotency_key"] == b.to_floppy[0][1]["idempotency_key"]


def test_duplicate_kodi_copies_both_see_floppy_state():
    a, b = _item(playcount=1, dbid=1), _item(playcount=1, dbid=2)
    plan = plan_baseline([a, b], [_w(play_count=1)], [])
    assert plan.to_floppy == []  # neither copy invents a catch-up play


def test_duplicate_kodi_copies_catch_up_once_with_latest_lastplayed():
    copies = [
        _item(playcount=1, lastplayed="2024-03-04 05:06:07", dbid=1),
        _item(playcount=1, lastplayed="2024-08-01 20:00:00", dbid=2),
        _item(playcount=1, lastplayed=None, dbid=3),
    ]
    plan = plan_baseline(copies, [], [])
    assert plan.push_floppy == 1
    ((kind, payload),) = plan.to_floppy
    assert kind == "scrobble"
    assert payload["played_at"] == kodi_time_to_utc_iso("2024-08-01 20:00:00")


def test_floppy_row_without_watched_bool_changes_nothing():
    item = _item(playcount=1, lastplayed="2024-03-04 05:06:07")
    no_flag = _w()
    del no_flag["watched"]
    for entry in (no_flag, _w(watched=None), _w(watched=0)):
        plan = plan_baseline([item], [entry], [])
        assert plan.to_kodi == []
        assert plan.to_floppy == []
        assert (plan.unwatch_kodi, plan.push_floppy) == (0, 0)


def test_catch_up_once_for_copies_with_overlapping_ids():
    imdb_only = Ref("movie", (("imdb", "tt1"),), None, None)
    copies = [
        _item(playcount=1, lastplayed="2024-08-01 20:00:00", dbid=1, ref=imdb_only),
        _item(playcount=1, lastplayed="2024-03-04 05:06:07", dbid=2),  # tmdb + imdb
    ]
    plan = plan_baseline(copies, [], [])
    assert plan.push_floppy == 1
    ((_, payload),) = plan.to_floppy
    assert payload["played_at"] == kodi_time_to_utc_iso("2024-08-01 20:00:00")


def test_scrobbles_come_before_progress_pushes():
    other = Ref("movie", (("imdb", "tt2"),), None, None)
    library = [
        _item(resume=300, total=6000, dbid=1),  # progress push
        _item(playcount=1, dbid=2, ref=other),  # catch-up
    ]
    plan = plan_baseline(library, [], [])
    assert [kind for kind, _ in plan.to_floppy] == ["scrobble", "progress"]


def test_floppy_resume_without_duration_is_not_written():
    for duration in (None, 0):
        assert plan_baseline([_item()], [], [_p(duration=duration)]).to_kodi == []


def test_episode_through_plan_baseline():
    ref = Ref("episode", (("tvdb", "81189"),), 2, 5)
    item = KodiItem("episode", 7, ref, 1, "2024-03-04 05:06:07", 0.0, 0.0)
    entry = {
        "media_type": "episode",
        "ids": {"tvdb": "81189"},
        "season_number": 2,
        "episode_number": 5,
        "watched": True,
        "play_count": 4,
    }
    other_ep = {**entry, "episode_number": 6}
    plan = plan_baseline([item], [entry, other_ep], [])
    assert plan.to_kodi == [KodiWrite(item, 4, None, None)]
    assert plan.unmatched == 1  # episode 6 is not in the library

    ((kind, payload),) = plan_baseline([item], [], []).to_floppy
    assert kind == "scrobble"
    assert payload["ref_key"] == "episode:tvdb:81189:2:5"
    assert (payload["season_number"], payload["episode_number"]) == (2, 5)
