"""Build dist/script.floppy-<version>.zip containing only the addon's runtime files."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON_ID = "script.floppy"


def main() -> None:
    version = ET.parse(ROOT / "addon.xml").getroot().get("version")
    out = ROOT / "dist" / f"{ADDON_ID}-{version}.zip"
    out.parent.mkdir(exist_ok=True)
    files = [ROOT / "addon.xml", *sorted(ROOT.glob("*.py")), ROOT / "LICENSE"]
    files += sorted(
        p
        for p in (ROOT / "resources").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f.exists():
                z.write(f, f"{ADDON_ID}/{f.relative_to(ROOT).as_posix()}")
    print(out)


if __name__ == "__main__":
    main()
