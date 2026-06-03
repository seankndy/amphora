"""MPRIS2 D-Bus integration.

Exposing the standard ``org.mpris.MediaPlayer2`` interfaces does double duty
on modern Linux desktops:

* GNOME/KDE route hardware **media keys** (play/pause, next) to whichever
  MPRIS player is active — so this is how Amphora gets media-key support.
* The desktop shell shows a **persistent now-playing** widget (lock screen,
  quick-settings media controls) populated from our metadata.

The implementation is deliberately small and uses :mod:`Gio` D-Bus directly so
there is no extra dependency.
"""

from __future__ import annotations

from typing import Callable

from gi.repository import Gio, GLib

from .config import APP_ID
from .pandora import Song

BUS_NAME = "org.mpris.MediaPlayer2.Amphora"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
_TRACK_ID_BASE = "/net/kndy/Amphora/track/"

_INTROSPECTION_XML = """
<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition">
      <arg direction="in" name="TrackId" type="o"/>
      <arg direction="in" name="Position" type="x"/>
    </method>
    <method name="OpenUri"><arg direction="in" name="Uri" type="s"/></method>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
"""


class MPRIS:
    def __init__(
        self,
        *,
        on_play_pause: Callable[[], None],
        on_play: Callable[[], None],
        on_pause: Callable[[], None],
        on_stop: Callable[[], None],
        on_next: Callable[[], None],
        on_raise: Callable[[], None],
        on_quit: Callable[[], None],
        get_position_us: Callable[[], int],
        get_volume: Callable[[], float],
        set_volume: Callable[[float], None],
    ) -> None:
        self._cb = {
            "play_pause": on_play_pause,
            "play": on_play,
            "pause": on_pause,
            "stop": on_stop,
            "next": on_next,
            "raise": on_raise,
            "quit": on_quit,
        }
        self._get_position_us = get_position_us
        self._get_volume = get_volume
        self._set_volume = set_volume

        self._connection: Gio.DBusConnection | None = None
        self._reg_ids: list[int] = []
        self._playback_status = "Stopped"
        self._metadata: dict[str, GLib.Variant] = {}

        self._node = Gio.DBusNodeInfo.new_for_xml(_INTROSPECTION_XML)
        self._owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            None,
            self._on_name_lost,
        )

    # -- lifecycle ---------------------------------------------------------

    def _on_bus_acquired(self, connection: Gio.DBusConnection, _name: str) -> None:
        self._connection = connection
        for iface in self._node.interfaces:
            try:
                reg_id = connection.register_object(
                    OBJECT_PATH,
                    iface,
                    self._handle_method_call,
                    self._handle_get_property,
                    self._handle_set_property,
                )
                self._reg_ids.append(reg_id)
            except GLib.Error:
                pass

    def _on_name_lost(self, _connection: object, _name: str) -> None:
        # Another instance owns the name, or the bus is unavailable.
        self._connection = None

    def shutdown(self) -> None:
        if self._connection is not None:
            for reg_id in self._reg_ids:
                self._connection.unregister_object(reg_id)
            self._reg_ids = []
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0

    # -- state updates (called from the UI) --------------------------------

    def set_playback_status(self, status: str) -> None:
        """``status`` is one of 'playing', 'paused', 'stopped', 'buffering'."""
        mapped = {
            "playing": "Playing",
            "paused": "Paused",
            "buffering": "Playing",
        }.get(status, "Stopped")
        if mapped == self._playback_status:
            return
        self._playback_status = mapped
        self._emit_properties({"PlaybackStatus": GLib.Variant("s", mapped)})

    def set_song(self, song: Song) -> None:
        self._metadata = self._build_metadata(song)
        self._emit_properties(
            {"Metadata": GLib.Variant("a{sv}", self._metadata)}
        )

    def notify_volume(self, volume: float) -> None:
        self._emit_properties({"Volume": GLib.Variant("d", float(volume))})

    # -- helpers -----------------------------------------------------------

    def _build_metadata(self, song: Song) -> dict[str, GLib.Variant]:
        track_id = _TRACK_ID_BASE + (song.track_token[:24] or "current").replace(
            "-", ""
        )
        # mpris track ids must be valid object paths: keep it conservative.
        safe_id = "".join(c if c.isalnum() or c == "/" else "" for c in track_id)
        meta: dict[str, GLib.Variant] = {
            "mpris:trackid": GLib.Variant("o", safe_id or _TRACK_ID_BASE + "x"),
            "xesam:title": GLib.Variant("s", song.title or "Unknown"),
            "xesam:artist": GLib.Variant("as", [song.artist or "Unknown"]),
            "xesam:album": GLib.Variant("s", song.album or ""),
        }
        if song.duration:
            meta["mpris:length"] = GLib.Variant("x", int(song.duration) * 1_000_000)
        if song.art_url:
            meta["mpris:artUrl"] = GLib.Variant("s", song.art_url)
        return meta

    def _emit_properties(self, changed: dict[str, GLib.Variant]) -> None:
        if self._connection is None:
            return
        self._connection.emit_signal(
            None,
            OBJECT_PATH,
            "org.freedesktop.DBus.Properties",
            "PropertiesChanged",
            GLib.Variant(
                "(sa{sv}as)",
                ("org.mpris.MediaPlayer2.Player", changed, []),
            ),
        )

    # -- D-Bus vtable ------------------------------------------------------

    def _handle_method_call(
        self,
        _connection,
        _sender,
        _path,
        interface,
        method,
        _params,
        invocation,
    ) -> None:
        if interface == "org.mpris.MediaPlayer2":
            if method == "Raise":
                self._cb["raise"]()
            elif method == "Quit":
                self._cb["quit"]()
        elif interface == "org.mpris.MediaPlayer2.Player":
            actions = {
                "PlayPause": "play_pause",
                "Play": "play",
                "Pause": "pause",
                "Stop": "stop",
                "Next": "next",
            }
            if method in actions:
                self._cb[actions[method]]()
            # Previous / Seek / SetPosition / OpenUri are intentionally no-ops
            # (Pandora radio cannot rewind or seek).
        invocation.return_value(None)

    def _handle_get_property(
        self, _connection, _sender, _path, interface, prop
    ) -> GLib.Variant | None:
        if interface == "org.mpris.MediaPlayer2":
            return {
                "CanQuit": GLib.Variant("b", True),
                "CanRaise": GLib.Variant("b", True),
                "HasTrackList": GLib.Variant("b", False),
                "Identity": GLib.Variant("s", "Amphora"),
                "DesktopEntry": GLib.Variant("s", APP_ID),
                "SupportedUriSchemes": GLib.Variant("as", []),
                "SupportedMimeTypes": GLib.Variant("as", []),
            }.get(prop)

        if interface == "org.mpris.MediaPlayer2.Player":
            getters = {
                "PlaybackStatus": lambda: GLib.Variant("s", self._playback_status),
                "Rate": lambda: GLib.Variant("d", 1.0),
                "MinimumRate": lambda: GLib.Variant("d", 1.0),
                "MaximumRate": lambda: GLib.Variant("d", 1.0),
                "Metadata": lambda: GLib.Variant("a{sv}", self._metadata),
                "Volume": lambda: GLib.Variant("d", float(self._get_volume())),
                "Position": lambda: GLib.Variant("x", int(self._get_position_us())),
                "CanGoNext": lambda: GLib.Variant("b", True),
                "CanGoPrevious": lambda: GLib.Variant("b", False),
                "CanPlay": lambda: GLib.Variant("b", True),
                "CanPause": lambda: GLib.Variant("b", True),
                "CanSeek": lambda: GLib.Variant("b", False),
                "CanControl": lambda: GLib.Variant("b", True),
            }
            getter = getters.get(prop)
            return getter() if getter else None
        return None

    def _handle_set_property(
        self, _connection, _sender, _path, interface, prop, value
    ) -> bool:
        if interface == "org.mpris.MediaPlayer2.Player" and prop == "Volume":
            self._set_volume(float(value.get_double()))
            return True
        return False
