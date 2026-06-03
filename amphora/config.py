"""Persistent settings and secure credential storage.

Non-secret preferences live as JSON under the user's config dir; the Pandora
password is stored in the system keyring via libsecret when available, with a
graceful fallback to "not stored" (the user simply signs in again).
"""

from __future__ import annotations

import json
import os
from typing import Any

import gi

gi.require_version("Secret", "1")
from gi.repository import GLib, Secret  # noqa: E402

APP_ID = "net.kndy.Amphora"

_CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "amphora")
_CONFIG_FILE = os.path.join(_CONFIG_DIR, "settings.json")

_SECRET_SCHEMA = Secret.Schema.new(
    APP_ID,
    Secret.SchemaFlags.NONE,
    {"username": Secret.SchemaAttributeType.STRING},
)

_DEFAULTS: dict[str, Any] = {
    "username": "",
    "audio_quality": "high",  # high | medium | low
    "last_station_token": "",
    "volume": 0.7,
    "pause_on_lock": False,
    "notifications": True,
    "display_mode": "normal",  # normal | mini
}


class Config:
    def __init__(self) -> None:
        self._data: dict[str, Any] = dict(_DEFAULTS)
        self._load()

    # -- preferences -------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(_CONFIG_FILE, encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                self._data.update(
                    {k: v for k, v in stored.items() if k in _DEFAULTS}
                )
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        try:
            os.makedirs(_CONFIG_DIR, exist_ok=True)
            tmp = _CONFIG_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            os.replace(tmp, _CONFIG_FILE)
        except OSError:
            pass

    def get(self, key: str) -> Any:
        return self._data.get(key, _DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        if key in _DEFAULTS:
            self._data[key] = value

    # -- credentials -------------------------------------------------------

    def store_password(self, username: str, password: str) -> None:
        try:
            Secret.password_store_sync(
                _SECRET_SCHEMA,
                {"username": username},
                Secret.COLLECTION_DEFAULT,
                f"Amphora — {username}",
                password,
                None,
            )
        except GLib.Error:
            pass

    def lookup_password(self, username: str) -> str | None:
        if not username:
            return None
        try:
            return Secret.password_lookup_sync(
                _SECRET_SCHEMA, {"username": username}, None
            )
        except GLib.Error:
            return None

    def clear_password(self, username: str) -> None:
        try:
            Secret.password_clear_sync(
                _SECRET_SCHEMA, {"username": username}, None
            )
        except GLib.Error:
            pass
