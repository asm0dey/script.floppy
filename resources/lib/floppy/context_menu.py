"""Context-menu logic: toggle Kodi's watched state; the service's watcher pushes it to Floppy."""

from __future__ import annotations

import xbmc

from floppy.kodi_library import item_for, rpc, write_item


def _episode_ids(tvshow_id: int, season: int | None) -> list[int]:
    params: dict = {"tvshowid": tvshow_id}
    if season is not None:
        params["season"] = season
    return [e["episodeid"] for e in rpc("VideoLibrary.GetEpisodes", params).get("episodes", [])]


def run() -> None:
    label = xbmc.getInfoLabel
    dbtype, dbid = label("ListItem.DBType"), int(label("ListItem.DBID") or 0)
    if dbtype in ("movie", "episode"):
        targets = [(dbtype, dbid)]
        watched = int(label("ListItem.PlayCount") or 0) == 0
    elif dbtype == "season":
        show, season = int(label("ListItem.TVShowDBID") or 0), int(label("ListItem.Season") or 0)
        targets = [("episode", i) for i in _episode_ids(show, season)]
        watched = None
    elif dbtype == "tvshow":
        targets = [("episode", i) for i in _episode_ids(dbid, None)]
        watched = None
    else:
        return
    items = [i for i in (item_for(k, d) for k, d in targets) if i is not None]
    if watched is None:  # container: watch everything unless everything is already watched
        watched = not all(i.playcount > 0 for i in items)
    for item in items:
        if (item.playcount > 0) != watched:
            write_item(item, playcount=1 if watched else 0)
    rpc("JSONRPC.NotifyAll", {"sender": "script.floppy", "message": "floppy.flush"})
