"""Kodi library changes -> Floppy outbox, with an echo guard for writes the sync engine makes."""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import xbmc

from floppy.kodi_library import KodiItem, item_for, utc_now_iso
from floppy.store import Store

# Kodi delivers Monitor callbacks only between service ticks, so a long sync pass's
# notifications arrive after it ends: entries expire _TTL seconds after arm_expiry().
_TTL = 60.0
_expected: dict[tuple[str, int], list[float | None]] = {}  # (ref_key, playcount) -> expiries


def suppress(ref_key: str, playcount: int) -> None:
    """Expect one Kodi update setting ``ref_key`` to ``playcount``; never expires until armed."""
    _expected.setdefault((ref_key, playcount), []).append(None)


def arm_expiry() -> None:
    """Start the clock on every unarmed expectation; the service calls it after each pass."""
    deadline = time.monotonic() + _TTL
    for expiries in _expected.values():
        expiries[:] = [deadline if e is None else e for e in expiries]


def is_suppressed(ref_key: str, playcount: int) -> bool:
    """Consume one live expectation matching this update, if any."""
    now = time.monotonic()
    expiries = [e for e in _expected.pop((ref_key, playcount), []) if e is None or e > now]
    if not expiries:
        return False
    expiries.pop()
    if expiries:
        _expected[(ref_key, playcount)] = expiries
    return True


def mark(store: Store, item: KodiItem, watched: bool) -> None:
    base = {**item.ref.payload(), "ref_key": item.ref.key()}
    if watched:
        played_at = utc_now_iso()
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
) -> bool:
    """Queue a Kodi-side watched toggle for Floppy; True when something was queued."""
    if method != "VideoLibrary.OnUpdate":
        return False
    try:
        payload = json.loads(data)
        info = payload["item"]
        kind, playcount = info["type"], int(payload["playcount"])
    except (ValueError, KeyError, TypeError) as exc:
        xbmc.log(f"[Floppy] watcher: ignoring notification: {exc!r}", xbmc.LOGDEBUG)
        return False
    if kind not in ("movie", "episode"):
        return False
    try:
        item = item_for(kind, int(info["id"]))
        if item is None:
            return False
        key = item.ref.key()
        if is_suppressed(key, playcount) or recently_stopped(key):
            return False
        mark(store, item, playcount > 0)
        return True
    except Exception as exc:  # noqa: BLE001 - never raise into Kodi's notification callback
        xbmc.log(f"[Floppy] watcher: {exc!r}", xbmc.LOGWARNING)
        return False
