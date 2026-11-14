"""Kodi library changes -> Floppy outbox, with an echo guard for writes the sync engine makes."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime

import xbmc

from floppy.kodi_library import KodiItem, item_for
from floppy.store import Store

_TTL = 30.0
_suppressed: dict[str, float] = {}


def suppress(ref_key: str) -> None:
    _suppressed[ref_key] = time.monotonic() + _TTL


def is_suppressed(ref_key: str) -> bool:
    expiry = _suppressed.pop(ref_key, None)
    return expiry is not None and time.monotonic() < expiry


def mark(store: Store, item: KodiItem, watched: bool) -> None:
    base = {**item.ref.payload(), "ref_key": item.ref.key()}
    if watched:
        played_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        store.enqueue(
            "scrobble",
            {
                **base,
                "action": "stop",
                "manual": True,
                "completed": True,
                "played_at": played_at,
            },
        )
    else:
        store.enqueue("scrobble", {**base, "action": "unwatch"})


def handle_notification(
    store: Store, method: str, data: str, recently_stopped: Callable[[str], bool]
) -> None:
    if method != "VideoLibrary.OnUpdate":
        return
    try:
        payload = json.loads(data)
        info = payload["item"]
        kind, playcount = info["type"], int(payload["playcount"])
    except (ValueError, KeyError, TypeError) as exc:
        xbmc.log(f"[Floppy] watcher: ignoring notification: {exc!r}", xbmc.LOGDEBUG)
        return
    if kind not in ("movie", "episode"):
        return
    try:
        item = item_for(kind, int(info["id"]))
        if item is None:
            return
        key = item.ref.key()
        if is_suppressed(key) or recently_stopped(key):
            return
        mark(store, item, playcount > 0)
    except Exception as exc:  # noqa: BLE001 - never raise into Kodi's notification callback
        xbmc.log(f"[Floppy] watcher: {exc!r}", xbmc.LOGWARNING)
