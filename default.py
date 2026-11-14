from __future__ import annotations

import sys
from pathlib import Path

import xbmcaddon
import xbmcvfs


def main() -> None:
    lib = Path(xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo("path"))) / "resources" / "lib"
    sys.path.insert(0, str(lib))
    from floppy import actions

    actions.main(sys.argv)


if __name__ == "__main__":
    main()
