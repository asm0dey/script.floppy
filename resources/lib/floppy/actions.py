"""Settings-screen actions, run as RunScript(script.floppy,<action>). The service does syncing."""

from __future__ import annotations

import time

import xbmc
import xbmcaddon
import xbmcgui

from floppy.auth import poll_login, start_login
from floppy.http import FloppyError, request
from floppy.kodi_library import rpc
from floppy.service_loop import open_store
from floppy.store import Store

_LOGOUT_KEYS = (
    "access_token",
    "refresh_token",
    "access_expires_at",
    "api_key",
    "sync_context",
    "cursor_watched",
    "cursor_progress",
    "baseline_done",
    "last_summary",
    "auth_needed",
)


def _ask_service(message: str) -> None:
    rpc("JSONRPC.NotifyAll", {"sender": "script.floppy", "message": message})


def login(store: Store, server: str) -> None:
    dialog = xbmcgui.Dialog()
    if not server:
        dialog.ok("Floppy", "Set the server URL first.")
        return
    client_name = f"Kodi on {xbmc.getInfoLabel('System.FriendlyName')}"[:60]
    try:
        device = start_login(store, server, client_name)
    except FloppyError as e:
        dialog.ok("Floppy", f"Login failed: {e}")
        return
    progress = xbmcgui.DialogProgress()
    progress.create(
        "Floppy",
        f"Go to {device.get('verification_uri', '')}\nand enter the code {device['user_code']}",
    )
    monitor = xbmc.Monitor()

    def cancelled() -> bool:
        return progress.iscanceled() or monitor.abortRequested()

    def sleep(seconds: float) -> None:
        end = time.monotonic() + seconds
        while not cancelled() and (left := end - time.monotonic()) > 0:
            monitor.waitForAbort(min(1.0, left))  # 1 s steps keep Cancel responsive

    try:
        ok = poll_login(store, server, device, sleep=sleep, cancelled=cancelled)
    finally:
        progress.close()
    if not ok:
        return
    store.set("auth_needed", None)
    dialog.notification("Floppy", "Logged in to Floppy")
    _ask_service("floppy.sync")  # the service runs the first baseline, which asks first


def logout(store: Store, server: str) -> None:
    token = store.get("refresh_token") or store.get("access_token")
    if server and token:
        try:
            request(
                "POST",
                f"{server.rstrip('/')}/oauth/revoke",
                form={"token": token, "client_id": store.get("client_id") or ""},
            )
        except FloppyError as e:  # best effort: the local tokens go regardless
            xbmc.log(f"[Floppy] token revoke failed: {e}", xbmc.LOGWARNING)
    for key in _LOGOUT_KEYS:
        store.set(key, None)
    xbmcgui.Dialog().notification("Floppy", "Logged out of Floppy")


def main(argv: list[str]) -> None:
    action = argv[1] if len(argv) > 1 else ""
    if action == "sync_now":
        _ask_service("floppy.sync")
    elif action in ("restore", "push_history"):  # catch-up is part of the baseline merge
        _ask_service("floppy.restore")
    elif action in ("login", "logout"):
        server = xbmcaddon.Addon().getSetting("server_url").strip()
        (login if action == "login" else logout)(open_store(), server)
    else:
        xbmcaddon.Addon().openSettings()
