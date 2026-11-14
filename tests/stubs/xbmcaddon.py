"""Fake ``xbmcaddon``: settings live in a module-level dict that tests may preload."""

from __future__ import annotations

SETTINGS: dict[str, str] = {}
INFO: dict[str, str] = {"id": "script.floppy", "profile": "/tmp/floppy-profile/"}


def reset() -> None:
    SETTINGS.clear()
    INFO.clear()
    INFO.update({"id": "script.floppy", "profile": "/tmp/floppy-profile/"})


class Addon:
    def __init__(self, id: str | None = None) -> None:  # noqa: A002
        self.id = id or INFO["id"]

    def getSetting(self, key: str) -> str:  # noqa: N802
        return SETTINGS.get(key, "")

    def setSetting(self, key: str, value: str) -> None:  # noqa: N802
        SETTINGS[key] = value

    def getAddonInfo(self, key: str) -> str:  # noqa: N802
        return INFO[key]

    def getLocalizedString(self, string_id: int) -> str:  # noqa: N802
        return f"string:{string_id}"

    def openSettings(self) -> None:  # noqa: N802
        pass
