from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VISIBLE = (
    "String.IsEqual(ListItem.DBType,movie) | String.IsEqual(ListItem.DBType,episode)"
    " | String.IsEqual(ListItem.DBType,season) | String.IsEqual(ListItem.DBType,tvshow)"
)


def test_manifest_declares_entry_points() -> None:
    addon = ET.parse(ROOT / "addon.xml").getroot()
    assert addon.get("id") == "script.floppy"
    assert addon.get("name") == "Floppy"
    assert addon.get("provider-name") == "asm0dey"

    imports = {i.get("addon"): i.get("version") for i in addon.iterfind("requires/import")}
    assert imports == {"xbmc.python": "3.0.1"}

    points = {e.get("point"): e for e in addon.iterfind("extension")}
    assert points["xbmc.service"].get("library") == "service.py"
    assert points["xbmc.python.script"].get("library") == "default.py"

    items = points["kodi.context.item"].findall("menu/item")
    assert items
    for item in items:
        assert item.get("library") == "context.py"
        assert item.findtext("visible") == VISIBLE
