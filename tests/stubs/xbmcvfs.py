"""Fake ``xbmcvfs``: ``special://`` paths map to a per-test root set by conftest."""

from __future__ import annotations

ROOT = "/tmp/floppy-kodi"


def translatePath(path: str) -> str:  # noqa: N802
    return path.replace("special://", ROOT + "/", 1) if path.startswith("special://") else path
