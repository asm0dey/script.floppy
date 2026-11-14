"""Background service: the only process that syncs. Triggers queue work; ``tick`` does it."""

from __future__ import annotations

import time
import traceback
from pathlib import Path

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from floppy import watcher
from floppy.auth import AuthRequired
from floppy.http import FloppyError
from floppy.merge import MergePlan
from floppy.player import FloppyPlayer
from floppy.store import Store
from floppy.sync import flush_outbox, run_sync

_STOP_DELAY = 30.0  # seconds after playback stops before syncing
# onNotification method -> queued work
_TRIGGERS = {
    "Other.floppy.flush": "flush",
    "Other.floppy.sync": "sync",
    "Other.floppy.restore": "restore",
    "VideoLibrary.OnScanFinished": "rescan",
    "System.OnWake": "auto",
}


def open_store() -> Store:
    profile = Path(xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo("profile")))
    profile.mkdir(parents=True, exist_ok=True)
    return Store(str(profile / "state.db"))


def _flag(addon: xbmcaddon.Addon, key: str, default: bool) -> bool:
    value = addon.getSetting(key)
    return default if value == "" else value == "true"


def _confirm(plan: MergePlan) -> bool:
    return bool(xbmcgui.Dialog().yesno("Floppy", plan.summary()))


def _toast(message: str) -> None:
    xbmcgui.Dialog().notification("Floppy", message)


class Service(xbmc.Monitor):
    def __init__(self, store: Store) -> None:
        super().__init__()
        self._store = store
        self._pending: set[str] = set()
        self._last_sync = float("-inf")  # timer is due at once: the start-up sync
        self._sync_after: float | None = None
        self._changed = 0
        self.player: FloppyPlayer | None = None
        self._player_server = ""

    def run(self) -> None:
        while not self.abortRequested():
            self.tick(time.monotonic())
            if self.waitForAbort(5):
                break

    def onNotification(self, sender: str, method: str, data: str) -> None:  # noqa: N802
        if work := _TRIGGERS.get(method):
            self._pending.add(work)
        player = self.player
        watcher.handle_notification(
            self._store, method, data, player.recently_stopped if player else lambda key: False
        )

    def playback_stopped(self) -> None:
        self._sync_after = time.monotonic() + _STOP_DELAY

    def tick(self, now: float) -> None:
        try:
            self._tick(now)
        except Exception:  # noqa: BLE001 - one bad tick must never end the service
            xbmc.log(f"[Floppy] service tick failed:\n{traceback.format_exc()}", xbmc.LOGERROR)

    def _update_player(self, enabled: bool, server: str) -> None:
        if not (enabled and server):
            self.player = None
        elif self.player is None or self._player_server != server:
            self.player = FloppyPlayer(self._store, server, self.playback_stopped)
            self._player_server = server
        if self.player is not None:
            self.player.tick()

    def _tick(self, now: float) -> None:
        addon = xbmcaddon.Addon()
        server = addon.getSetting("server_url").strip()
        if api_key := addon.getSetting("api_key").strip():
            self._store.set("api_key", api_key)
        self._update_player(_flag(addon, "scrobble_enabled", True), server)
        if not server or self._store.get("auth_needed"):
            return

        try:
            poll = max(1, int(addon.getSetting("poll_minutes") or 5))
        except ValueError:
            poll = 5
        timer_due = now - self._last_sync >= poll * 60
        stop_due = self._sync_after is not None and now >= self._sync_after
        work = self._pending
        if timer_due or stop_due:
            work.add("auto")
        if not work:
            return
        self._pending, self._sync_after = set(), None
        if "auto" in work or "sync" in work or "restore" in work:
            self._last_sync = now  # a failure retries at the next interval, not every tick

        sync_enabled = _flag(addon, "sync_enabled", True)
        auto = sync_enabled and bool(work & {"auto", "rescan"})
        silent = False
        try:
            if "restore" in work:
                result = self._sync(server, force_baseline=True, confirm=_confirm)
            elif "rescan" in work and sync_enabled and self._store.get("baseline_done"):
                # Silent merge-only rebaseline so items added to Kodi pick up Floppy state.
                silent = True
                result = self._sync(server, force_baseline=True, confirm=lambda plan: True)
            elif "sync" in work or auto:
                result = self._sync(server, confirm=_confirm)
            else:
                flush_outbox(self._store, server)
                return
        except AuthRequired:
            self._store.set("auth_needed", "1")
            _toast("Please log in again")
            return
        except FloppyError as e:
            xbmc.log(f"[Floppy] sync failed, retrying next interval: {e}", xbmc.LOGWARNING)
            return
        if result == "baseline" and not (silent and not self._changed):  # no toast per scan
            _toast(self._store.get("last_summary") or "Synced with Floppy")
        elif result == "incremental" and self._changed and _flag(addon, "notify_sync", False):
            plural = "" if self._changed == 1 else "s"
            _toast(f"Updated {self._changed} item{plural} from Floppy")

    def _sync(self, server: str, **kw) -> str:
        self._changed = 0

        def suppress(ref_key: str) -> None:
            self._changed += 1  # run_sync suppresses exactly the items it writes to Kodi
            watcher.suppress(ref_key)

        return run_sync(self._store, server, suppress=suppress, **kw)


def main() -> None:
    xbmc.log("[Floppy] service started", xbmc.LOGINFO)
    Service(open_store()).run()
