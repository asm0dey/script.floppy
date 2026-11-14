from __future__ import annotations

from floppy.store import Store


def test_kv_get_set_and_clear(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    assert s.get("a") is None
    s.set("a", "1")
    s.set("a", "2")
    assert s.get("a") == "2"
    s.set("a", None)
    assert s.get("a") is None


def test_outbox_survives_reopen(tmp_path):
    path = str(tmp_path / "s.db")
    s = Store(path)
    first = s.enqueue("scrobble", {"n": 1})
    s.enqueue("scrobble", {"n": 2})
    s2 = Store(path)
    rows = s2.pending()
    assert [(k, p["n"]) for _, k, p in rows] == [("scrobble", 1), ("scrobble", 2)]
    s2.done(first)
    assert [p["n"] for _, _, p in s2.pending()] == [2]


def test_enqueue_adds_idempotency_key_but_keeps_given(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    s.enqueue("a", {})
    s.enqueue("a", {"idempotency_key": "mine"})
    keys = [p["idempotency_key"] for _, _, p in s.pending()]
    assert len(keys[0]) == 32 and keys[1] == "mine"


def test_pending_refs(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    s.enqueue("scrobble", {"ref_key": "movie:tmdb:603"})
    s.enqueue("scrobble", {"ref_key": "movie:tmdb:603"})
    s.enqueue("progress", {})
    assert s.pending_refs() == {"movie:tmdb:603"}


def test_two_handles_share_data(tmp_path):
    path = str(tmp_path / "s.db")
    a, b = Store(path), Store(path)
    a.set("k", "v")
    assert b.get("k") == "v"
