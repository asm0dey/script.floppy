"""Fake ``xbmcgui``: Dialog records every call in ``CALLS`` as ``(method, args)``."""

from __future__ import annotations

from typing import Any

CALLS: list[tuple[str, tuple[Any, ...]]] = []
# Answers for the dialogs that return something; tests may override.
YESNO_ANSWER = True


def reset() -> None:
    global YESNO_ANSWER
    CALLS.clear()
    YESNO_ANSWER = True


class Dialog:
    def notification(self, heading: str, message: str, icon: str = "", time: int = 5000) -> bool:
        CALLS.append(("notification", (heading, message)))
        return True

    def ok(self, heading: str, message: str) -> bool:
        CALLS.append(("ok", (heading, message)))
        return True

    def yesno(self, heading: str, message: str, *args: Any, **kwargs: Any) -> bool:
        CALLS.append(("yesno", (heading, message)))
        return YESNO_ANSWER

    def select(self, heading: str, items: list[str]) -> int:
        CALLS.append(("select", (heading, tuple(items))))
        return -1
