"""Minimal fake of Kodi's ``xbmc`` module. Unknown JSON-RPC methods fail the test."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR = 0, 1, 2, 3

RPC_HANDLERS: dict[str, Callable[[dict], Any]] = {}
LOG: list[tuple[int, str]] = []
INFO_LABELS: dict[str, str] = {}


def reset() -> None:
    RPC_HANDLERS.clear()
    LOG.clear()
    INFO_LABELS.clear()
    Monitor.instances.clear()
    Player.instances.clear()


def log(msg: str, level: int = LOGDEBUG) -> None:
    LOG.append((level, msg))


def getInfoLabel(name: str) -> str:  # noqa: N802
    return INFO_LABELS.get(name, "")


def executeJSONRPC(request: str) -> str:  # noqa: N802
    """Dispatch to RPC_HANDLERS[method](params); the handler returns the ``result``."""
    call = json.loads(request)
    method = call["method"]
    if method not in RPC_HANDLERS:
        raise AssertionError(f"unregistered JSON-RPC method: {method}")
    result = RPC_HANDLERS[method](call.get("params", {}))
    return json.dumps({"jsonrpc": "2.0", "id": call.get("id", 1), "result": result})


class Monitor:
    instances: list[Monitor] = []

    def __init__(self) -> None:
        self.aborted = False
        self.waits: list[float] = []
        Monitor.instances.append(self)

    def waitForAbort(self, timeout: float = 0) -> bool:  # noqa: N802
        self.waits.append(timeout)
        return self.aborted

    def abortRequested(self) -> bool:  # noqa: N802
        return self.aborted

    def onNotification(self, sender: str, method: str, data: str) -> None:  # noqa: N802
        pass

    def onSettingsChanged(self) -> None:  # noqa: N802
        pass


class InfoTagVideo:
    def __init__(self) -> None:
        self.media_type = ""
        self.unique_ids: dict[str, str] = {}
        self.season = -1
        self.episode = -1

    def getMediaType(self) -> str:  # noqa: N802
        return self.media_type

    def getUniqueIDs(self) -> dict[str, str]:  # noqa: N802
        return self.unique_ids

    def getSeason(self) -> int:  # noqa: N802
        return self.season

    def getEpisode(self) -> int:  # noqa: N802
        return self.episode


class Player:
    instances: list[Player] = []

    def __init__(self) -> None:
        self.playing_file: str | None = None
        self.time: float = 0.0
        self.total: float = 0.0
        self.infotag = InfoTagVideo()
        Player.instances.append(self)

    def isPlaying(self) -> bool:  # noqa: N802
        return self.playing_file is not None

    def getPlayingFile(self) -> str:  # noqa: N802
        if self.playing_file is None:
            raise RuntimeError("nothing is playing")
        return self.playing_file

    def getTime(self) -> float:  # noqa: N802
        return self.time

    def getTotalTime(self) -> float:  # noqa: N802
        return self.total

    def getVideoInfoTag(self) -> InfoTagVideo:  # noqa: N802
        return self.infotag

    def __getattr__(self, name: str) -> Any:
        # Callbacks (onAVStarted, ...) are defined by subclasses; anything else is a test bug.
        raise AttributeError(name)
