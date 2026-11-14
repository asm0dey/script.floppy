"""Item identity shared by Kodi and Floppy: a media type plus external ids."""

from __future__ import annotations

from dataclasses import dataclass

ID_NAMES = ("tmdb", "imdb", "tvdb")  # priority order for key()


@dataclass(frozen=True)
class Ref:
    media_type: str  # "movie" | "episode"
    ids: tuple[tuple[str, str], ...]  # sorted (name, value), only ID_NAMES, non-empty
    season: int | None
    episode: int | None

    @classmethod
    def from_uniqueid(
        cls,
        media_type: str,
        uniqueid: dict | None,
        season: int | None = None,
        episode: int | None = None,
    ) -> Ref | None:
        ids = tuple(sorted((k, str(v)) for k, v in (uniqueid or {}).items() if k in ID_NAMES and v))
        if not ids or media_type not in ("movie", "episode"):
            return None
        if media_type == "episode" and (season is None or episode is None):
            return None
        return cls(media_type, ids, season, episode)

    def _key(self, name: str, value: str) -> str:
        key = f"{self.media_type}:{name}:{value}"
        if self.media_type == "episode":
            key += f":{self.season}:{self.episode}"
        return key

    def keys(self) -> list[str]:
        return [self._key(n, v) for n, v in self.ids]

    def key(self) -> str:
        by_name = dict(self.ids)
        name = next(n for n in ID_NAMES if n in by_name)
        return self._key(name, by_name[name])

    def payload(self) -> dict:
        out: dict = {"media_type": self.media_type, "ids": dict(self.ids)}
        if self.media_type == "episode":
            out["season_number"] = self.season
            out["episode_number"] = self.episode
        return out


def ref_from_floppy(entry: dict) -> Ref | None:
    return Ref.from_uniqueid(
        entry.get("media_type", ""),
        entry.get("ids"),
        entry.get("season_number"),
        entry.get("episode_number"),
    )
