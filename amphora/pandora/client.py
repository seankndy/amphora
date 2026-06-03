"""A small client for Pandora's JSON API (v5).

This implements the same protocol Pithos uses: a partner login that yields a
sync-time offset, followed by a user login.  Subsequent requests carry the
user auth token and an encrypted, hex-encoded JSON body.

The client is deliberately synchronous (built on :mod:`requests`); callers are
expected to run it off the GTK main thread.
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from .crypt import PandoraCrypt
from .models import Song, Station

# Built-in "android" partner credentials, as used by Pithos for years.
DEFAULT_CLIENT: dict[str, str] = {
    "decryptionKey": "R=U!LH$O2B#",
    "encryptionKey": "6#26FRL$ZWD",
    "partnerUsername": "android",
    "partnerPassword": "AC7IBG09A3DTSYM4R41UJWL07VLN8JI7",
    "deviceModel": "android-generic",
    "apiHost": "tuner.pandora.com/services/json/",
    "version": "5",
}

# A subset of Pandora's documented error codes worth naming.
ERROR_MESSAGES: dict[int, str] = {
    0: "Internal Pandora error.",
    1001: "Auth token expired — please sign in again.",
    1002: "Invalid auth token.",
    1003: "Listener not authorized — a Pandora One subscription may be required.",
    1004: "User not authorized.",
    1005: "Station limit reached.",
    1006: "Station does not exist.",
    1009: "Device not found.",
    1010: "Partner not authorized.",
    1011: "Invalid username.",
    1012: "Invalid password.",
    1023: "Device model is invalid.",
    1039: "Too many requests — please wait a moment.",
}


class PandoraError(Exception):
    """Raised when the API returns ``stat != "ok"``."""

    def __init__(self, code: int | None, message: str | None) -> None:
        self.code = code
        self.api_message = message
        friendly = ERROR_MESSAGES.get(code or -1)
        super().__init__(friendly or message or f"Pandora error {code}")

    @property
    def auth_expired(self) -> bool:
        return self.code in (1001, 1002)


class PandoraNetworkError(Exception):
    """Raised for transport-level failures."""


class PandoraClient:
    def __init__(self, client: dict[str, str] | None = None) -> None:
        cfg = client or DEFAULT_CLIENT
        self._crypt = PandoraCrypt(cfg["encryptionKey"], cfg["decryptionKey"])
        self._partner_username = cfg["partnerUsername"]
        self._partner_password = cfg["partnerPassword"]
        self._device_model = cfg["deviceModel"]
        self._version = cfg["version"]
        self._api_host = cfg["apiHost"]

        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "Amphora/0.1 (libadwaita Pandora client)",
                "Content-Type": "text/plain;charset=utf-8",
            }
        )

        self._time_offset: float = 0.0
        self.partner_id: str | None = None
        self.partner_auth_token: str | None = None
        self.user_id: str | None = None
        self.user_auth_token: str | None = None
        self._credentials: tuple[str, str] | None = None

    # -- low level ---------------------------------------------------------

    @property
    def sync_time(self) -> int:
        return int(time.time() + self._time_offset)

    @property
    def is_logged_in(self) -> bool:
        return bool(self.user_auth_token)

    def _request(
        self,
        method: str,
        body: dict[str, Any] | None = None,
        *,
        encrypt: bool = True,
    ) -> dict[str, Any]:
        body = dict(body or {})
        params: dict[str, str] = {"method": method}

        if self.partner_id:
            params["partner_id"] = self.partner_id

        if method == "auth.userLogin":
            params["auth_token"] = self.partner_auth_token or ""
            body["partnerAuthToken"] = self.partner_auth_token
        elif self.user_auth_token:
            params["auth_token"] = self.user_auth_token
            body["userAuthToken"] = self.user_auth_token
            if self.user_id:
                params["user_id"] = self.user_id

        if method != "auth.partnerLogin":
            body["syncTime"] = self.sync_time

        payload = json.dumps(body)
        if encrypt:
            payload = self._crypt.encrypt(payload)

        url = f"https://{self._api_host}"
        try:
            resp = self._session.post(url, params=params, data=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise PandoraNetworkError(str(exc)) from exc
        except ValueError as exc:  # bad JSON
            raise PandoraNetworkError(f"Malformed response: {exc}") from exc

        if data.get("stat") != "ok":
            raise PandoraError(data.get("code"), data.get("message"))
        return data.get("result", {}) or {}

    # -- authentication ----------------------------------------------------

    def _partner_login(self) -> None:
        result = self._request(
            "auth.partnerLogin",
            {
                "username": self._partner_username,
                "password": self._partner_password,
                "deviceModel": self._device_model,
                "version": self._version,
                "includeUrls": True,
            },
            encrypt=False,
        )
        self.partner_id = result["partnerId"]
        self.partner_auth_token = result["partnerAuthToken"]
        sync = self._crypt.decrypt_sync_time(result["syncTime"])
        self._time_offset = sync - time.time()

    def login(self, username: str, password: str) -> None:
        """Authenticate as a Pandora listener."""
        self._credentials = (username, password)
        self._partner_login()
        result = self._request(
            "auth.userLogin",
            {
                "loginType": "user",
                "username": username,
                "password": password,
                "includePandoraOneInfo": True,
                "includeSubscriptionExpiration": True,
                "returnStationList": False,
            },
        )
        self.user_id = result["userId"]
        self.user_auth_token = result["userAuthToken"]

    def relogin(self) -> bool:
        """Re-authenticate using cached credentials. Returns success."""
        if not self._credentials:
            return False
        self.user_auth_token = None
        self.user_id = None
        self.login(*self._credentials)
        return True

    # -- content -----------------------------------------------------------

    def get_stations(self) -> list[Station]:
        result = self._request("user.getStationList", {})
        stations = [Station.from_json(s) for s in result.get("stations", [])]
        stations.sort(key=lambda s: (not s.is_quickmix, s.name.lower()))
        return stations

    def get_playlist(self, station_token: str, quality: str = "high") -> list[Song]:
        result = self._request(
            "station.getPlaylist",
            {
                "stationToken": station_token,
                "includeTrackLength": True,
                "audioAdPlaybackEnabled": False,
            },
        )
        songs: list[Song] = []
        for item in result.get("items", []):
            if not item.get("songName"):
                continue  # advertisement token, skip
            song = Song.from_json(item, quality=quality)
            if song.audio_url:
                songs.append(song)
        return songs

    def add_feedback(self, station_token: str, track_token: str, positive: bool) -> None:
        self._request(
            "station.addFeedback",
            {
                "stationToken": station_token,
                "trackToken": track_token,
                "isPositive": positive,
            },
        )

    def sleep_song(self, track_token: str) -> None:
        """'Tired of this song' — suppress it for ~30 days."""
        self._request("user.sleepSong", {"trackToken": track_token})

    def create_station_from_track(self, music_token: str) -> Station:
        result = self._request(
            "station.createStation", {"trackToken": music_token}
        )
        return Station.from_json(result)
