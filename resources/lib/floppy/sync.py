"""Sync engine pieces: outbox flush (more to come)."""

from __future__ import annotations

import xbmc

from .auth import call
from .http import FloppyError
from .store import Store

_ENDPOINTS = {
    "scrobble": ("POST", "/api/v1/scrobble/"),
    "progress": ("PUT", "/api/v1/playback/progress/"),
}
_RETRY_LATER = (401, 409, 429)


def flush_outbox(store: Store, server: str) -> int:
    """Send queued writes oldest first; returns how many were sent successfully.

    Drops items the server rejects for good (4xx), stops at the first retryable failure
    and lets AuthRequired (401 after refresh) propagate.
    """
    sent = 0
    for id_, kind, payload in store.pending():
        method, path = _ENDPOINTS[kind]
        body = {k: v for k, v in payload.items() if k not in ("ref_key", "idempotency_key")}
        headers = {"Idempotency-Key": payload["idempotency_key"]}
        try:
            call(store, server, method, path, json_body=body, headers=headers)
        except FloppyError as e:
            if e.status is None or e.status >= 500 or e.status in _RETRY_LATER:
                return sent
            xbmc.log(
                f"[Floppy] dropping {kind} {id_}: status={e.status} code={e.code}", xbmc.LOGWARNING
            )
            store.done(id_)
            continue
        store.done(id_)
        sent += 1
    return sent
