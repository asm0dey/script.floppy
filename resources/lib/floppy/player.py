"""Player monitor: live scrobbles (best effort) and durable stop/progress writes (outbox)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import xbmc

from .auth import AuthRequired, call
from .http import FloppyError
from .kodi_library import item_for, rpc, utc_now_iso
from .refs import Ref
from .store import Store

_COMPLETED_RATIO = 0.9  # Floppy's is_played rule (PLAYBACK_SCROBBLE_MIN_PROGRESS_RATIO)
_RECENT = 60.0


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True).start()


class FloppyPlayer(xbmc.Player):
    def __init__(
        self,
        store: Store,
        server: str,
        on_stopped: Callable[[], None],
        spawn: Callable[[Callable[[], None]], None] = _thread,
    ) -> None:
        super().__init__()
        self._store = store
        self._server = server
        self._on_stopped = on_stopped
        self._spawn = spawn  # runs network sends off the Kodi callback thread
        self._ref: Ref | None = None
        self._position = 0
        self._duration = 0
        self._stopped_at: dict[str, float] = {}

    def recently_stopped(self, ref_key: str) -> bool:
        at = self._stopped_at.get(ref_key)
        return at is not None and time.monotonic() - at < _RECENT

    def tick(self) -> None:
        """Record the playback position; the service calls this every few seconds."""
        if self._ref is None:
            return
        try:
            self._position = int(self.getTime())
            self._duration = int(self.getTotalTime())
        except RuntimeError:  # player stopped between the callback and the call
            pass

    def _identify(self) -> Ref | None:
        try:
            players = rpc("Player.GetActivePlayers", {})
            video = next((p for p in players if p.get("type") == "video"), None)
            if video is not None:
                item = rpc("Player.GetItem", {"playerid": video["playerid"], "properties": []})
                item = item.get("item", {})
                if item.get("type") in ("movie", "episode") and item.get("id", -1) > 0:
                    found = item_for(item["type"], item["id"])
                    return found.ref if found else None
        except (RuntimeError, KeyError):
            pass  # not a library item: fall through to the infotag
        try:
            tag = self.getVideoInfoTag()
        except RuntimeError:  # not playing video (audio)
            return None
        # Infotag ids of an episode are the episode's, not the show's: unusable.
        if tag.getMediaType() != "movie":
            return None
        return Ref.from_uniqueid("movie", tag.getUniqueIDs())

    def _send_live(self, action: str) -> None:
        if self._ref is None:
            return
        self.tick()
        body = {"action": action, **self._ref.payload(), **self._times()}

        def send() -> None:
            try:
                call(self._store, self._server, "POST", "/api/v1/scrobble/", json_body=body)
            except (FloppyError, AuthRequired) as e:
                xbmc.log(f"[Floppy] live {action} not sent: {e}", xbmc.LOGDEBUG)

        self._spawn(send)

    def _times(self) -> dict:
        return {"position_seconds": self._position, "duration_seconds": self._duration}

    def onAVStarted(self) -> None:  # noqa: N802
        self._position = self._duration = 0
        self._ref = self._identify()
        if self._ref is None:
            xbmc.log("[Floppy] playing item has no usable ids; not scrobbling", xbmc.LOGDEBUG)
            return
        self._send_live("start")

    def onPlayBackPaused(self) -> None:  # noqa: N802
        self._send_live("pause")

    def onPlayBackResumed(self) -> None:  # noqa: N802
        self._send_live("start")

    def onPlayBackStopped(self) -> None:  # noqa: N802
        self._finish(ended=False)

    def onPlayBackEnded(self) -> None:  # noqa: N802
        self._finish(ended=True)

    def _finish(self, *, ended: bool) -> None:
        ref, self._ref = self._ref, None
        if ref is None or self._duration <= 0:
            return
        position = self._duration if ended else min(self._position, self._duration)
        completed = position >= _COMPLETED_RATIO * self._duration
        key = ref.key()
        times = {"position_seconds": position, "duration_seconds": self._duration}
        self._store.enqueue(
            "scrobble",
            {
                "action": "stop",
                **ref.payload(),
                **times,
                "completed": completed,
                "played_at": utc_now_iso(),  # a flush after an outage must not redate the play
                "ref_key": key,
            },
        )
        self._store.enqueue(
            "progress",
            {
                **ref.payload(),
                "position_seconds": None if completed else position,
                "duration_seconds": self._duration,
                "completed": completed,
                "ref_key": key,
            },
        )
        if completed:  # only then does Kodi bump the playcount itself
            self._stopped_at[key] = time.monotonic()
        self._on_stopped()
