from __future__ import annotations

import pytest
import xbmc
from fake_floppy import FakeFloppy
from floppy.store import Store
from floppy.sync import flush_outbox


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    s.set("api_key", "k")
    return s


def _scrobble(n):
    return {"action": "stop", "media_type": "movie", "ids": {"tmdb": str(n)}, "ref_key": f"r{n}"}


def test_flush_strips_internal_keys_and_sends_idempotency_header(store):
    store.enqueue("scrobble", {**_scrobble(1), "idempotency_key": "ik"})
    store.enqueue("progress", {"media_type": "movie", "ids": {"tmdb": "1"}, "ref_key": "r1"})
    with FakeFloppy() as fake:
        fake.route("POST", "/api/v1/scrobble/", lambda r: (200, {}))
        fake.route("PUT", "/api/v1/playback/progress/", lambda r: (200, {}))
        assert flush_outbox(store, fake.url) == 2
        first, second = fake.requests
    assert first.headers["Idempotency-Key"] == "ik"
    assert first.json == {"action": "stop", "media_type": "movie", "ids": {"tmdb": "1"}}
    assert second.json == {"media_type": "movie", "ids": {"tmdb": "1"}}
    assert store.pending() == []


def test_flush_drops_400_keeps_on_500(store):
    for n in (1, 2, 3):
        store.enqueue("scrobble", _scrobble(n))
    answers = iter([(400, {"detail": "bad"}), (200, {}), (500, {})])
    with FakeFloppy() as fake:
        fake.route("POST", "/api/v1/scrobble/", lambda r: next(answers))
        assert flush_outbox(store, fake.url) == 1
    assert [p["ref_key"] for _, _, p in store.pending()] == ["r3"]


def test_flush_keeps_on_429_and_logs_retry(store):
    store.enqueue("scrobble", _scrobble(1))
    with FakeFloppy() as fake:
        fake.route("POST", "/api/v1/scrobble/", lambda r: (429, {}))
        assert flush_outbox(store, fake.url) == 0
    assert len(store.pending()) == 1
    assert any("retrying later" in m for _, m in xbmc.LOG)


def test_flush_drops_idempotency_conflict_and_continues(store):
    store.enqueue("scrobble", _scrobble(1))
    store.enqueue("scrobble", _scrobble(2))
    conflict = {"error": {"code": "idempotency_conflict", "type": "conflict_error"}}
    answers = iter([(409, conflict), (200, {})])
    with FakeFloppy() as fake:
        fake.route("POST", "/api/v1/scrobble/", lambda r: next(answers))
        assert flush_outbox(store, fake.url) == 1
    assert store.pending() == []
    assert any(
        lvl == xbmc.LOGWARNING and "status=409 code=idempotency_conflict" in m
        for lvl, m in xbmc.LOG
    )
