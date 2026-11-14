"""Sync engine: outbox flush, baseline merge and change-feed pulls (Floppy is the truth)."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlencode

import xbmc

from .auth import call
from .http import FloppyError
from .kodi_library import KodiItem, LibraryIndex, load_library, utc_iso_to_kodi_time, write_item
from .merge import MergePlan, plan_baseline
from .refs import ref_from_floppy
from .store import Store

_ENDPOINTS = {
    "scrobble": ("POST", "/api/v1/scrobble/"),
    "progress": ("PUT", "/api/v1/playback/progress/"),
}
_RETRY_LATER = (401, 429)
# Kodi write callback: (ref_key, playcount the item has after the write).
OnWrite = Callable[[str, int], None]


def flush_outbox(store: Store, server: str) -> int:
    """Send queued writes oldest first; returns how many were sent successfully.

    Drops items the server rejects for good (4xx, including 409 idempotency_conflict: a
    reused key never succeeds), stops at the first retryable failure and lets
    AuthRequired propagate.
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
                xbmc.log(f"[Floppy] {kind} {id_} not sent, retrying later: {e}", xbmc.LOGINFO)
                return sent
            xbmc.log(
                f"[Floppy] dropping {kind} {id_}: status={e.status} code={e.code}", xbmc.LOGWARNING
            )
            store.done(id_)
            continue
        store.done(id_)
        sent += 1
    return sent


_REBASELINE = {
    (409, "cursor_expired"),
    (409, "cursor_invalid"),
    (409, "sync_reset_required"),
    (409, "sync_not_initialized"),
    (400, "invalid_context"),
}
_FEEDS = (
    ("watched", "/api/v1/sync/changes/", "cursor_watched"),
    ("progress", "/api/v1/sync/progress-changes/", "cursor_progress"),
)
_PROGRESS_PAGE = 200
_RESUME_TOLERANCE = 5  # seconds


def run_sync(
    store: Store,
    server: str,
    *,
    confirm: Callable[[MergePlan], bool],
    suppress: OnWrite,
    force_baseline: bool = False,
) -> str:
    """One sync pass; returns "deferred", "cancelled", "baseline" or "incremental".

    ``suppress`` is called before every Kodi write. AuthRequired and FloppyErrors other
    than the re-baseline codes propagate.
    """
    flush_outbox(store, server)
    if force_baseline or not store.get("sync_context"):
        ask = force_baseline or not store.get("baseline_done")
        return _baseline(store, server, confirm if ask else None, suppress)
    try:
        _pull_feeds(store, server, suppress)
    except FloppyError as e:
        if (e.status, e.code) not in _REBASELINE:
            raise
        xbmc.log(f"[Floppy] feed says {e.code}: new baseline", xbmc.LOGINFO)
        ask = not store.get("baseline_done")
        return _baseline(store, server, confirm if ask else None, suppress)
    return "incremental"


def _get(store: Store, server: str, path: str, **query) -> dict:
    query = {k: v for k, v in query.items() if v is not None}
    return call(store, server, "GET", f"{path}?{urlencode(query)}")


def _baseline(
    store: Store,
    server: str,
    confirm: Callable[[MergePlan], bool] | None,
    suppress: OnWrite,
) -> str:
    try:
        boot = call(store, server, "POST", "/api/v1/sync/bootstrap/")
    except FloppyError as e:
        if e.status == 409 and e.code == "sync_initializing":
            return "deferred"
        raise
    context = boot["context"]
    boot_cursors = boot.get("cursors") or {}
    snap = "/api/v1/sync/snapshot/"
    watched: list[dict] = []
    cursor_watched = after = None
    while True:
        page = _get(
            store, server, snap, context=context, resource="watched", after=after, limit=500
        )
        watched += page.get("results") or []
        cursor_watched = cursor_watched or page.get("cursor")  # earliest: replays, never misses
        after = page.get("after")
        if not page.get("has_more") or not after:
            break
    # Cursor before reading progress, so a change made meanwhile comes through the feed.
    cursor_progress = _get(store, server, snap, context=context, resource="progress", limit=1).get(
        "cursor"
    )
    # The bootstrap cursors predate both snapshots: a fallback that replays, never misses.
    cursor_watched = cursor_watched or boot_cursors.get("watched")
    cursor_progress = cursor_progress or boot_cursors.get("progress")
    if not (cursor_watched and cursor_progress):
        xbmc.log("[Floppy] baseline aborted: Floppy sent no feed cursor", xbmc.LOGERROR)
        return "deferred"
    progress: list[dict] = []
    offset = 0
    while True:
        page = _get(
            store, server, "/api/v1/playback/progress/", limit=_PROGRESS_PAGE, offset=offset
        )
        results = page.get("results") or []
        progress += results
        pagination = page.get("pagination") or {}
        if (
            len(results) < _PROGRESS_PAGE
            or pagination.get("has_next") is False
            or ("next" in pagination and not pagination["next"])
        ):
            break
        offset += _PROGRESS_PAGE

    library = load_library()
    if not library:
        return "deferred"  # library not scanned yet: never treat it as "nothing watched"
    plan = plan_baseline(library, watched, progress, store.pending_refs())
    if confirm is not None and not confirm(plan):
        return "cancelled"
    for w in plan.to_kodi:
        suppress(w.item.ref.key(), w.item.playcount if w.playcount is None else w.playcount)
        write_item(w.item, playcount=w.playcount, lastplayed=w.lastplayed, resume=w.resume)
    for kind, payload in plan.to_floppy:
        store.enqueue(kind, payload)
    store.set("sync_context", context)
    store.set("cursor_watched", cursor_watched)
    store.set("cursor_progress", cursor_progress)
    store.set("baseline_done", "1")
    store.set("last_summary", plan.summary())
    xbmc.log(f"[Floppy] baseline applied: {plan.summary()}", xbmc.LOGINFO)
    flush_outbox(store, server)
    return "baseline"


def _pull_feeds(store: Store, server: str, suppress: OnWrite) -> None:
    context = store.get("sync_context")
    index: LibraryIndex | None = None  # loaded only once a page has results
    for feed, path, cursor_key in _FEEDS:
        cursor = store.get(cursor_key)
        while True:
            page = _get(store, server, path, contract=2, context=context, cursor=cursor, limit=500)
            results = page.get("results") or []
            if results:
                if index is None:
                    index = LibraryIndex(load_library())
                pending = store.pending_refs()
                unmatched = 0
                for entry in results:
                    try:
                        ref = ref_from_floppy(entry)
                        item = index.find(ref) if ref else None
                        if item is None:
                            unmatched += 1
                        elif item.ref.key() not in pending:  # local unflushed change wins
                            _apply(feed, entry, item, suppress)
                    except Exception as exc:  # noqa: BLE001 - one bad entry must not stall the feed
                        xbmc.log(
                            f"[Floppy] {feed} feed: skipping {entry!r}: {exc!r}", xbmc.LOGWARNING
                        )
                if unmatched:
                    xbmc.log(f"[Floppy] {feed} feed: {unmatched} not in Kodi library", xbmc.LOGINFO)
            cursor = page.get("next_cursor") or cursor
            store.set(cursor_key, cursor)
            if not page.get("has_more") or not page.get("next_cursor"):
                break


def _apply(feed: str, entry: dict, item: KodiItem, suppress: OnWrite) -> None:
    """Write one feed entry to Kodi if it differs; keeps ``item`` in step with what was written."""
    if feed == "watched":
        state = entry.get("watched")
        if state is True:
            target = max(entry.get("play_count") or 0, 1)
            if item.playcount >= target:
                return
            at = entry.get("watched_at")
            lastplayed = utc_iso_to_kodi_time(at) if at else None
            suppress(item.ref.key(), target)
            write_item(item, playcount=target, lastplayed=lastplayed)
            item.playcount = target
            item.lastplayed = lastplayed or item.lastplayed
        elif state is False and item.playcount > 0:
            suppress(item.ref.key(), 0)
            write_item(item, playcount=0)
            item.playcount = 0
        return
    pos = entry.get("position_seconds")
    dur = entry.get("duration_seconds")
    done = entry.get("completed")
    if not done and pos and pos > 0 and dur and dur > 0:
        if abs(pos - item.resume_position) < _RESUME_TOLERANCE:
            return
        resume = (float(pos), float(dur))
    elif (done or pos is None) and item.resume_position > 0:
        resume = (0.0, 0.0)
    else:
        return
    suppress(item.ref.key(), item.playcount)  # Kodi may echo the unchanged playcount
    write_item(item, resume=resume)
    item.resume_position, item.resume_total = resume
