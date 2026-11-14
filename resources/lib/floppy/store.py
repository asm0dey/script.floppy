"""SQLite key/value store plus an outbox queue; safe across Kodi processes."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
    payload TEXT NOT NULL, created REAL NOT NULL);
"""


class Store:
    def __init__(self, path: str) -> None:
        self._path = path
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        # Connection per call: short transactions, no cross-thread/process state.
        return sqlite3.connect(self._path, timeout=10, isolation_level=None)

    def get(self, key: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str | None) -> None:
        with self._conn() as c:
            if value is None:
                c.execute("DELETE FROM kv WHERE key=?", (key,))
            else:
                c.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)", (key, value))

    def enqueue(self, kind: str, payload: dict) -> int:
        payload.setdefault("idempotency_key", uuid.uuid4().hex)
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO outbox(kind, payload, created) VALUES (?, ?, ?)",
                (kind, json.dumps(payload), time.time()),
            )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def pending(self) -> list[tuple[int, str, dict]]:
        with self._conn() as c:
            rows = c.execute("SELECT id, kind, payload FROM outbox ORDER BY id").fetchall()
        return [(i, k, json.loads(p)) for i, k, p in rows]

    def done(self, id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM outbox WHERE id=?", (id,))

    def pending_refs(self) -> set[str]:
        return {p["ref_key"] for _, _, p in self.pending() if "ref_key" in p}
