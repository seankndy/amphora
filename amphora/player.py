"""A thin GStreamer ``playbin`` wrapper exposed as a GObject.

Emits signals the UI can connect to instead of poking at the bus directly.
Pandora is a radio service: tracks are streamed and not seekable, so the
player only exposes play/pause/stop, volume and progress reporting.
"""

from __future__ import annotations

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, GObject, Gst  # noqa: E402

Gst.init(None)


class Player(GObject.Object):
    __gsignals__ = {
        # Emitted when the current track finishes naturally.
        "eos": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # (message,)
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # ("playing" | "paused" | "stopped" | "buffering",)
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # (position_seconds, duration_seconds)
        "progress": (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
        # (percent 0-100,)
        "buffering": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self) -> None:
        super().__init__()
        self._playbin = Gst.ElementFactory.make("playbin3", "amphora-player")
        if self._playbin is None:  # pragma: no cover - very old gstreamer
            self._playbin = Gst.ElementFactory.make("playbin", "amphora-player")
        if self._playbin is None:
            raise RuntimeError("GStreamer 'playbin' element is unavailable")

        self._volume = 1.0
        self._buffering = False
        self._current_state = "stopped"

        bus = self._playbin.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::error", self._on_error)
        bus.connect("message::buffering", self._on_buffering)
        bus.connect("message::state-changed", self._on_state_changed)

        self._tick_id = GLib.timeout_add(500, self._tick)

    # -- transport ---------------------------------------------------------

    def load(self, uri: str, *, autoplay: bool = True) -> None:
        self._playbin.set_state(Gst.State.NULL)
        self._playbin.set_property("uri", uri)
        self._apply_volume()
        if autoplay:
            self.play()

    def play(self) -> None:
        self._playbin.set_state(Gst.State.PLAYING)

    def pause(self) -> None:
        self._playbin.set_state(Gst.State.PAUSED)

    def toggle(self) -> None:
        if self._current_state == "playing":
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self._playbin.set_state(Gst.State.NULL)
        self._set_state("stopped")

    @property
    def state(self) -> str:
        return self._current_state

    def get_position_us(self) -> int:
        """Current playback position in microseconds (0 if unknown)."""
        ok, position = self._playbin.query_position(Gst.Format.TIME)
        if not ok or position < 0:
            return 0
        return position // 1000  # ns -> us

    @property
    def is_playing(self) -> bool:
        return self._current_state == "playing"

    # -- volume ------------------------------------------------------------

    @property
    def volume(self) -> float:
        return self._volume

    def set_volume(self, value: float) -> None:
        """Set volume on a 0.0–1.0 linear slider (mapped to a cubic curve)."""
        self._volume = max(0.0, min(1.0, value))
        self._apply_volume()

    def _apply_volume(self) -> None:
        # Perceptual (cubic) mapping feels right under a linear slider.
        self._playbin.set_property("volume", self._volume**3)

    # -- bus handlers ------------------------------------------------------

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        self._set_state("stopped")
        self.emit("eos")

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, _debug = msg.parse_error()
        self.stop()
        self.emit("error", err.message if err else "Playback error")

    def _on_buffering(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        percent = msg.parse_buffering()
        self.emit("buffering", percent)
        if percent < 100:
            if not self._buffering:
                self._buffering = True
                self._playbin.set_state(Gst.State.PAUSED)
            self._set_state("buffering")
        elif self._buffering:
            self._buffering = False
            self._playbin.set_state(Gst.State.PLAYING)

    def _on_state_changed(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        if msg.src is not self._playbin:
            return
        _old, new, _pending = msg.parse_state_changed()
        if self._buffering:
            return
        if new == Gst.State.PLAYING:
            self._set_state("playing")
        elif new == Gst.State.PAUSED:
            self._set_state("paused")
        elif new == Gst.State.NULL:
            self._set_state("stopped")

    def _set_state(self, state: str) -> None:
        if state != self._current_state:
            self._current_state = state
            self.emit("state-changed", state)

    # -- progress ----------------------------------------------------------

    def _tick(self) -> bool:
        if self._current_state in ("playing", "paused"):
            ok_pos, position = self._playbin.query_position(Gst.Format.TIME)
            ok_dur, duration = self._playbin.query_duration(Gst.Format.TIME)
            if ok_pos and ok_dur and duration > 0:
                self.emit(
                    "progress",
                    position / Gst.SECOND,
                    duration / Gst.SECOND,
                )
        return GLib.SOURCE_CONTINUE
