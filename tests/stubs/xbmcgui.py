"""Fake ``xbmcgui``: Dialog records every call in ``CALLS`` as ``(method, args)``."""

from __future__ import annotations

from typing import Any

CALLS: list[tuple[str, tuple[Any, ...]]] = []
# Answers for the dialogs that return something; tests may override.
YESNO_ANSWER = True
PROGRESS_CANCELED = False


def reset() -> None:
    global YESNO_ANSWER, PROGRESS_CANCELED
    CALLS.clear()
    YESNO_ANSWER = True
    PROGRESS_CANCELED = False


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


class DialogProgress:
    def create(self, heading: str, message: str = "") -> None:
        CALLS.append(("progress.create", (heading, message)))

    def update(self, percent: int, message: str = "") -> None:
        CALLS.append(("progress.update", (percent, message)))

    def iscanceled(self) -> bool:
        return PROGRESS_CANCELED

    def close(self) -> None:
        CALLS.append(("progress.close", ()))
