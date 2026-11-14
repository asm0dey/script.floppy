from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ITEM = "(String.IsEqual(ListItem.DBType,movie) | String.IsEqual(ListItem.DBType,episode))"
# ListItem.PlayCount may be "" for an unwatched item, so compare as an integer.
MENU = {
    "30040": f"{ITEM} + !Integer.IsGreater(ListItem.PlayCount,0)",
    "30041": f"{ITEM} + Integer.IsGreater(ListItem.PlayCount,0)",
    "30042": "String.IsEqual(ListItem.DBType,season) | String.IsEqual(ListItem.DBType,tvshow)",
}
SETTINGS = ROOT / "resources" / "settings.xml"
STRINGS = ROOT / "resources" / "language" / "resource.language.en_gb" / "strings.po"


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
    assert {i.findtext("label"): i.findtext("visible") for i in items} == MENU
    assert all(i.get("library") == "context.py" for i in items)


def test_settings_ids_and_actions() -> None:
    root = ET.parse(SETTINGS).getroot()
    assert root.get("version") == "1"
    settings = {s.get("id"): s for s in root.iter("setting")}
    assert set(settings) == {
        "server_url", "login", "api_key", "scrobble_enabled", "sync_enabled",
        "poll_minutes", "notify_sync", "sync_now", "restore", "push_history", "logout",
    }  # fmt: skip
    for sid, s in settings.items():
        if s.get("type") == "action":
            assert s.findtext("data") == f"RunScript(script.floppy,{sid})"
    assert settings["api_key"].findtext("control/hidden") == "true"
    assert settings["api_key"].findtext("level") == "2"
    assert settings["poll_minutes"].findtext("default") == "5"
    assert settings["poll_minutes"].findtext("constraints/minimum") == "1"
    assert settings["poll_minutes"].findtext("constraints/maximum") == "60"
    defaults = {k: settings[k].findtext("default") for k in ("scrobble_enabled", "sync_enabled")}
    assert defaults == {"scrobble_enabled": "true", "sync_enabled": "true"}
    assert settings["notify_sync"].findtext("default") == "false"


def test_every_label_id_has_a_string() -> None:
    defined = set(re.findall(r'^msgctxt "#(\d+)"$', STRINGS.read_text(), re.M))
    used = {e.get("label") for e in ET.parse(SETTINGS).getroot().iter() if e.get("label")}
    used |= {e.text for e in ET.parse(SETTINGS).getroot().iter("heading")}
    used |= {e.text for e in ET.parse(ROOT / "addon.xml").getroot().iter("label")}
    assert used and used <= defined
