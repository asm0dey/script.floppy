"""Read and write Kodi's video library over JSON-RPC."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import xbmc

from floppy.refs import Ref

_PAGE = 1000
_PLAY_FIELDS = ["uniqueid", "playcount", "lastplayed", "resume"]


@dataclass
class KodiItem:
    kind: str  # "movie" | "episode"
    dbid: int
    ref: Ref
    playcount: int
    lastplayed: str | None
    resume_position: float
    resume_total: float


def rpc(method: str, params: dict) -> dict:
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    if "error" in response:
        raise RuntimeError(response["error"].get("message", str(response["error"])))
    return response.get("result", {})


def _item(kind: str, dbid: int, d: dict, ref: Ref) -> KodiItem:
    resume = d.get("resume") or {}
    return KodiItem(
        kind,
        dbid,
        ref,
        int(d.get("playcount") or 0),
        d.get("lastplayed") or None,
        float(resume.get("position") or 0),
        float(resume.get("total") or 0),
    )


def _skip(kind: str, dbid: int) -> None:
    xbmc.log(f"[Floppy] skipping {kind} {dbid}: no usable ids", xbmc.LOGWARNING)


def load_library() -> list[KodiItem]:
    items: list[KodiItem] = []
    for m in rpc("VideoLibrary.GetMovies", {"properties": _PLAY_FIELDS}).get("movies", []):
        ref = Ref.from_uniqueid("movie", m.get("uniqueid"))
        if ref:
            items.append(_item("movie", m["movieid"], m, ref))
        else:
            _skip("movie", m["movieid"])

    shows = rpc("VideoLibrary.GetTVShows", {"properties": ["uniqueid"]}).get("tvshows", [])
    show_ids = {s["tvshowid"]: s.get("uniqueid") for s in shows}
    props = [*_PLAY_FIELDS, "season", "episode", "tvshowid"]
    start = 0
    while True:
        page = rpc(
            "VideoLibrary.GetEpisodes",
            {
                "properties": props,
                "limits": {"start": start, "end": start + _PAGE},
            },
        ).get("episodes", [])
        for e in page:
            ref = Ref.from_uniqueid(
                "episode", show_ids.get(e["tvshowid"]), e.get("season"), e.get("episode")
            )
            if ref:
                items.append(_item("episode", e["episodeid"], e, ref))
            else:
                _skip("episode", e["episodeid"])
        if len(page) < _PAGE:
            return items
        start += _PAGE


class LibraryIndex:
    def __init__(self, items: list[KodiItem]) -> None:
        self._items = items
        self._by_key: dict[str, KodiItem] = {}
        for item in items:
            for k in item.ref.keys():
                self._by_key.setdefault(k, item)

    def find(self, ref: Ref) -> KodiItem | None:
        return next((self._by_key[k] for k in ref.keys() if k in self._by_key), None)

    def __len__(self) -> int:
        return len(self._items)


def item_for(kind: str, dbid: int) -> KodiItem | None:
    if kind == "movie":
        d = rpc("VideoLibrary.GetMovieDetails", {"movieid": dbid, "properties": _PLAY_FIELDS})[
            "moviedetails"
        ]
        ref = Ref.from_uniqueid("movie", d.get("uniqueid"))
    else:
        d = rpc(
            "VideoLibrary.GetEpisodeDetails",
            {
                "episodeid": dbid,
                "properties": [*_PLAY_FIELDS, "season", "episode", "tvshowid"],
            },
        )["episodedetails"]
        show = rpc(
            "VideoLibrary.GetTVShowDetails", {"tvshowid": d["tvshowid"], "properties": ["uniqueid"]}
        )["tvshowdetails"]
        ref = Ref.from_uniqueid("episode", show.get("uniqueid"), d.get("season"), d.get("episode"))
    return _item(kind, dbid, d, ref) if ref else None


def write_item(
    item: KodiItem,
    *,
    playcount: int | None = None,
    lastplayed: str | None = None,
    resume: tuple[float, float] | None = None,
) -> None:
    params: dict = {f"{item.kind}id": item.dbid}
    if playcount is not None:
        params["playcount"] = playcount
    if lastplayed is not None:
        params["lastplayed"] = lastplayed
    if resume is not None:
        params["resume"] = {"position": resume[0], "total": resume[1]}
    rpc(
        "VideoLibrary.SetMovieDetails"
        if item.kind == "movie"
        else "VideoLibrary.SetEpisodeDetails",
        params,
    )


_KODI_FMT = "%Y-%m-%d %H:%M:%S"
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"
# datetime.UTC is 3.11+; some Kodi builds still ship 3.10.
_UTC = timezone.utc  # noqa: UP017


def utc_now_iso() -> str:
    return datetime.now(_UTC).strftime(_ISO_FMT)


def kodi_time_to_utc_iso(lastplayed: str) -> str | None:
    """Kodi local time -> UTC ISO; None when unparseable."""
    try:
        # Not datetime.strptime: in Kodi's embedded Python it can raise
        # "TypeError: 'NoneType' object is not callable" on a second interpreter.
        local = datetime(*time.strptime(lastplayed, _KODI_FMT)[:6]).astimezone()
        return local.astimezone(_UTC).strftime(_ISO_FMT)
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def utc_iso_to_kodi_time(iso: str) -> str | None:
    """UTC ISO (any offset) -> Kodi local time; None when unparseable."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_UTC)
        return dt.astimezone().strftime(_KODI_FMT)
    except (ValueError, TypeError, OSError, OverflowError, AttributeError):
        return None
