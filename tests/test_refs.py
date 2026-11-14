from floppy.refs import Ref, ref_from_floppy


def test_movie_ref_keys_and_payload():
    ref = Ref.from_uniqueid("movie", {"tmdb": "603", "imdb": "tt0133093", "unknown": "x"})
    assert ref is not None
    assert set(ref.keys()) == {"movie:imdb:tt0133093", "movie:tmdb:603"}
    assert ref.key() == "movie:tmdb:603"
    assert ref.payload() == {
        "media_type": "movie",
        "ids": {"imdb": "tt0133093", "tmdb": "603"},
    }


def test_episode_ref_uses_show_ids_and_numbers():
    ref = Ref.from_uniqueid("episode", {"tvdb": 81189}, season=1, episode=2)
    assert ref is not None
    assert ref.keys() == ["episode:tvdb:81189:1:2"]
    assert ref.payload() == {
        "media_type": "episode",
        "ids": {"tvdb": "81189"},
        "season_number": 1,
        "episode_number": 2,
    }


def test_ref_from_floppy_entry():
    entry = {
        "media_type": "episode",
        "ids": {"tmdb": 1, "x": "y", "imdb": ""},
        "season_number": 2,
        "episode_number": 3,
    }
    assert ref_from_floppy(entry) == Ref("episode", (("tmdb", "1"),), 2, 3)


def test_ref_from_floppy_rejects_unusable_entries():
    assert ref_from_floppy({"media_type": "show", "ids": {"tmdb": 1}}) is None
    assert ref_from_floppy({"media_type": "movie", "ids": {}}) is None
    assert ref_from_floppy({"media_type": "episode", "ids": {"tmdb": 1}}) is None
