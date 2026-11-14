from __future__ import annotations

from pathlib import Path

import pytest
from check_version import check_version


def addon_xml_with(tmp_path: Path, version: str) -> Path:
    path = tmp_path / "addon.xml"
    path.write_text(f'<addon id="script.floppy" version="{version}"/>')
    return path


def test_tag_must_match_addon_version(tmp_path: Path) -> None:
    check_version("v1.2.0", addon_xml_with(tmp_path, "1.2.0"))
    with pytest.raises(SystemExit, match="v1.2.1"):
        check_version("v1.2.1", addon_xml_with(tmp_path, "1.2.0"))
