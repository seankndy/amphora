"""Lightweight value objects for Pandora stations and songs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Quality preference order when reading ``audioUrlMap``.
_QUALITY_ORDER = {
    "high": ("highQuality", "mediumQuality", "lowQuality"),
    "medium": ("mediumQuality", "highQuality", "lowQuality"),
    "low": ("lowQuality", "mediumQuality", "highQuality"),
}

RATING_NONE = 0
RATING_LOVE = 1
RATING_BAN = -1


@dataclass(slots=True)
class Station:
    token: str
    id: str
    name: str
    is_quickmix: bool = False
    art_url: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Station":
        art = ""
        for art_entry in data.get("artUrl", []) if isinstance(data.get("artUrl"), list) else []:
            art = art_entry.get("url", "")
        return cls(
            token=data.get("stationToken", ""),
            id=data.get("stationId", ""),
            name=data.get("stationName", "Unknown station"),
            is_quickmix=bool(data.get("isQuickMix", False)),
            art_url=art or data.get("artUrl", "") if isinstance(data.get("artUrl"), str) else art,
        )


@dataclass(slots=True)
class Song:
    track_token: str
    title: str
    artist: str
    album: str
    audio_url: str
    art_url: str = ""
    album_detail_url: str = ""
    artist_detail_url: str = ""
    song_detail_url: str = ""
    station_id: str = ""
    rating: int = RATING_NONE
    duration: int = 0
    is_ad: bool = False
    bitrate: str = ""
    encoding: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any], quality: str = "high") -> "Song":
        audio_url, bitrate, encoding = cls._pick_audio(data, quality)
        # Pandora returns the largest art URL last; collect the biggest entry.
        art = ""
        cover = data.get("albumArtUrl")
        if isinstance(cover, str):
            art = cover
        return cls(
            track_token=data.get("trackToken", ""),
            title=data.get("songName", ""),
            artist=data.get("artistName", ""),
            album=data.get("albumName", ""),
            audio_url=audio_url,
            art_url=art,
            album_detail_url=data.get("albumDetailUrl", ""),
            artist_detail_url=data.get("artistDetailUrl", ""),
            song_detail_url=data.get("songDetailUrl", ""),
            station_id=data.get("stationId", ""),
            rating=int(data.get("songRating", 0) or 0),
            duration=int(data.get("trackLength", 0) or 0),
            bitrate=bitrate,
            encoding=encoding,
        )

    @staticmethod
    def _pick_audio(data: dict[str, Any], quality: str) -> tuple[str, str, str]:
        # ``additionalAudioUrl`` (when a single format was requested) wins.
        add = data.get("additionalAudioUrl")
        if isinstance(add, str) and add:
            return add, "", ""
        if isinstance(add, list) and add:
            return add[0], "", ""

        url_map = data.get("audioUrlMap") or {}
        for key in _QUALITY_ORDER.get(quality, _QUALITY_ORDER["high"]):
            entry = url_map.get(key)
            if entry and entry.get("audioUrl"):
                return (
                    entry["audioUrl"],
                    str(entry.get("bitrate", "")),
                    str(entry.get("encoding", "")),
                )
        return "", "", ""

    @property
    def is_loved(self) -> bool:
        return self.rating == RATING_LOVE

    @property
    def is_banned(self) -> bool:
        return self.rating == RATING_BAN

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.artist} — {self.title} ({self.album})"
