"""Fail unless the release tag is "v" + the version in addon.xml.

Usage: check_version.py [TAG]   (TAG defaults to $GITHUB_REF_NAME)
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check_version(tag: str, addon_xml_path: str | Path) -> None:
    version = ET.parse(addon_xml_path).getroot().get("version")
    if tag != f"v{version}":
        raise SystemExit(f"tag {tag!r} != addon.xml version: want v{version}")


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GITHUB_REF_NAME", "")
    check_version(tag, ROOT / "addon.xml")


if __name__ == "__main__":
    main()
