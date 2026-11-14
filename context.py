from __future__ import annotations

import sys
from pathlib import Path

import xbmcaddon
import xbmcvfs


def main() -> None:
    lib = Path(xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo("path"))) / "resources" / "lib"
    sys.path.insert(0, str(lib))
    from floppy import context_menu

    context_menu.run()


if __name__ == "__main__":
    main()
