"""Baseline merge: decide what a first sync or restore writes to Kodi and pushes to Floppy.

Pure: no I/O. Merge, never replace (spec "Sync engine -> Baseline", "Play counts").
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass, field
from datetime import datetime

from floppy.kodi_library import KodiItem, LibraryIndex, kodi_time_to_utc_iso, utc_iso_to_kodi_time
from floppy.refs import Ref, ref_from_floppy

_RESUME_TOLERANCE = 5  # seconds; smaller differences are the same resume point


@dataclass
class KodiWrite:
    item: KodiItem
    playcount: int | None  # None = leave unchanged
    lastplayed: str | None
    resume: tuple[float, float] | None


@dataclass
class MergePlan:
    to_kodi: list[KodiWrite] = field(default_factory=list)
    to_floppy: list[tuple[str, dict]] = field(default_factory=list)  # (outbox kind, payload)
    unwatch_kodi: int = 0
    watch_kodi: int = 0
    push_floppy: int = 0
    unmatched: int = 0

    def summary(self) -> str:
        text = (
            f"Push {self.push_floppy} to Floppy, mark {self.watch_kodi} watched in Kodi, "
            f"unwatch {self.unwatch_kodi}"
        )
        if self.unmatched:
            text += f" ({self.unmatched} not in Kodi library)"
        return text


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def plan_baseline(library: list[KodiItem], watched: list[dict], progress: list[dict]) -> MergePlan:
    index = LibraryIndex(library)
    unmatched: set[Ref] = set()
    rejected = 0

    def by_key(entries: list[dict]) -> dict[str, tuple[int, dict]]:
        nonlocal rejected
        out: dict[str, tuple[int, dict]] = {}
        for n, entry in enumerate(entries):
            ref = ref_from_floppy(entry)
            if ref is None:
                rejected += 1
            elif index.find(ref) is None:
                unmatched.add(ref)
            else:
                for k in ref.keys():
                    out.setdefault(k, (n, entry))
        return out

    def lookup(entries: dict[str, tuple[int, dict]], ref: Ref) -> dict | None:
        # Keyed by Kodi ref, not Kodi item, so duplicate Kodi copies all see Floppy's state;
        # several Floppy entries for one item: the first in Floppy's order wins.
        hits = [entries[k] for k in ref.keys() if k in entries]
        return min(hits, key=lambda h: h[0])[1] if hits else None

    floppy_watched = by_key(watched)
    floppy_progress = by_key(progress)
    plan = MergePlan()
    # Catch-up candidates grouped by identity: Kodi copies whose ref keys overlap are one item
    # and must add one play, from the copy with the latest lastplayed.
    group_of: dict[str, int] = {}
    groups: dict[int, tuple[set[str], KodiItem]] = {}
    group_ids = itertools.count()
    progress_pushes: list[tuple[str, dict]] = []

    for item in library:
        playcount: int | None = None
        lastplayed: str | None = None
        resume: tuple[float, float] | None = None
        ref = item.ref
        w = lookup(floppy_watched, ref)
        p = lookup(floppy_progress, ref)
        kodi_watched = item.playcount > 0
        newly_watched = False

        floppy_state = w.get("watched") if w else None
        if w and floppy_state is True:
            floppy_count = w.get("play_count") or 0
            if not kodi_watched:
                newly_watched = True
                playcount = max(1, floppy_count)
                if w.get("watched_at"):
                    lastplayed = utc_iso_to_kodi_time(w["watched_at"])
                plan.watch_kodi += 1
            elif item.playcount < floppy_count:
                playcount = floppy_count  # raise only; Floppy's count is never raised from Kodi
        elif kodi_watched and w is None:
            keys = set(ref.keys())
            best = item
            for gid in {group_of[k] for k in keys if k in group_of}:
                other_keys, other = groups.pop(gid)
                keys |= other_keys
                if (other.lastplayed or "") >= (best.lastplayed or ""):
                    best = other
            gid = next(group_ids)
            groups[gid] = (keys, best)
            for k in keys:
                group_of[k] = gid
        elif kodi_watched and floppy_state is False:  # explicitly unwatched: Floppy wins
            playcount = 0
            plan.unwatch_kodi += 1

        floppy_pos = 0 if not p or p.get("completed") else p.get("position_seconds") or 0
        floppy_resume = (float(floppy_pos), float(p.get("duration_seconds") or 0)) if p else None
        kodi_resume = item.resume_position > 0
        push_kodi_resume = False
        if floppy_pos and not kodi_resume:
            resume = floppy_resume
        elif kodi_resume and not floppy_pos:
            push_kodi_resume = True
        elif floppy_pos and abs(floppy_pos - item.resume_position) >= _RESUME_TOLERANCE:
            assert p is not None
            # Kodi has no resume timestamp; lastplayed stands in. Ties and gaps favour Floppy.
            updated = p.get("updated_at")
            kodi_newer = bool(
                item.lastplayed
                and updated
                and _parse(kodi_time_to_utc_iso(item.lastplayed)) > _parse(updated)
            )
            if kodi_newer:
                push_kodi_resume = True
            else:
                resume = floppy_resume
        if not (p and p.get("duration_seconds")):
            resume = None  # Kodi needs a total to show a resume point
        if newly_watched:
            resume = None  # a watched item has no resume point; Kodi's is left as is
            push_kodi_resume = False
        if push_kodi_resume:
            progress_pushes.append(
                (
                    "progress",
                    {
                        **ref.payload(),
                        "position_seconds": int(item.resume_position),
                        "duration_seconds": int(item.resume_total),
                        "completed": False,
                        "ref_key": ref.key(),
                    },
                )
            )

        if playcount is not None or resume is not None:
            plan.to_kodi.append(KodiWrite(item, playcount, lastplayed, resume))

    for _keys, item in groups.values():
        key = item.ref.key()
        played_at = kodi_time_to_utc_iso(item.lastplayed) if item.lastplayed else None
        payload = {
            **item.ref.payload(),
            "action": "stop",
            "manual": True,
            "completed": True,
            "ref_key": key,
            # UTC, so boxes in different timezones produce the same key for one viewing
            "idempotency_key": hashlib.sha256(f"{key}|{played_at or ''}".encode()).hexdigest(),
        }
        if played_at:
            payload["played_at"] = played_at
        plan.to_floppy.append(("scrobble", payload))
    # Progress after every stop, so a completing stop never lands after and clears it.
    plan.to_floppy += progress_pushes
    plan.push_floppy = len(groups)
    plan.unmatched = len(unmatched) + rejected
    return plan
