from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "tests" / "stubs", _ROOT / "resources" / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import xbmc  # noqa: E402
import xbmcaddon  # noqa: E402
import xbmcgui  # noqa: E402
import xbmcvfs  # noqa: E402


@pytest.fixture(autouse=True)
def kodi(tmp_path):
    """Reset every stub between tests; ``xbmcvfs.ROOT`` points at a fresh tmp dir."""
    xbmc.reset()
    xbmcaddon.reset()
    xbmcgui.reset()
    xbmcvfs.ROOT = str(tmp_path)
    xbmcaddon.INFO["profile"] = str(tmp_path / "profile") + "/"
    yield
