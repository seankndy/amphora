"""The 'now playing' view: album art, track metadata and controls."""

from __future__ import annotations

from gi.repository import Adw, GObject, Gdk, GLib, Gtk

from ..pandora import Song


def _format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class PlayerView(Gtk.Box):
    """Now-playing UI.

    Emits high-level intents the window translates into player/API calls:
    ``play-pause``, ``skip``, ``love``, ``ban``, ``tired``.
    """

    __gsignals__ = {
        "play-pause": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "skip": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "love": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "ban": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "tired": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "volume-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_vexpand(True)
        self._duration = 0.0

        clamp = Adw.Clamp(maximum_size=520, vexpand=True, valign=Gtk.Align.CENTER)
        self.append(clamp)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        outer.set_margin_top(24)
        outer.set_margin_bottom(24)
        outer.set_margin_start(18)
        outer.set_margin_end(18)
        clamp.set_child(outer)

        # -- album art -----------------------------------------------------
        art_frame = Gtk.Overlay()
        art_frame.set_halign(Gtk.Align.CENTER)
        outer.append(art_frame)

        self._art = Gtk.Picture()
        self._art.set_size_request(320, 320)
        self._art.set_content_fit(Gtk.ContentFit.COVER)
        self._art.add_css_class("album-art")
        self._art.set_overflow(Gtk.Overflow.HIDDEN)
        self._art_placeholder()
        art_frame.set_child(self._art)

        # -- metadata ------------------------------------------------------
        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        meta.set_halign(Gtk.Align.CENTER)
        outer.append(meta)

        self._title = Gtk.Label(label="Not playing")
        self._title.add_css_class("title-1")
        self._title.set_wrap(True)
        self._title.set_justify(Gtk.Justification.CENTER)
        self._title.set_max_width_chars(34)
        meta.append(self._title)

        self._artist = Gtk.Label()
        self._artist.add_css_class("title-3")
        self._artist.set_wrap(True)
        self._artist.set_justify(Gtk.Justification.CENTER)
        meta.append(self._artist)

        self._album = Gtk.Label()
        self._album.add_css_class("dim-label")
        self._album.set_wrap(True)
        self._album.set_justify(Gtk.Justification.CENTER)
        meta.append(self._album)

        # -- progress ------------------------------------------------------
        progress = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        outer.append(progress)

        self._elapsed = Gtk.Label(label="0:00")
        self._elapsed.add_css_class("numeric")
        self._elapsed.add_css_class("caption")
        progress.append(self._elapsed)

        self._progress = Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER)
        progress.append(self._progress)

        self._remaining = Gtk.Label(label="-0:00")
        self._remaining.add_css_class("numeric")
        self._remaining.add_css_class("caption")
        progress.append(self._remaining)

        # -- transport controls -------------------------------------------
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        controls.set_halign(Gtk.Align.CENTER)
        outer.append(controls)

        self._ban_btn = self._icon_button(
            "amphora-ban-symbolic", "Ban — never play this song", "ban"
        )
        controls.append(self._ban_btn)

        self._play_btn = Gtk.Button()
        self._play_btn.set_icon_name("media-playback-start-symbolic")
        self._play_btn.add_css_class("circular")
        self._play_btn.add_css_class("pill")
        self._play_btn.add_css_class("suggested-action")
        self._play_btn.set_size_request(56, 56)
        self._play_btn.set_tooltip_text("Play / Pause")
        self._play_btn.connect("clicked", lambda *_: self.emit("play-pause"))
        controls.append(self._play_btn)

        self._love_btn = self._icon_button(
            "amphora-thumbs-up-symbolic", "Love this song", "love"
        )
        controls.append(self._love_btn)

        self._tired_btn = self._icon_button(
            "amphora-thumbs-down-symbolic", "Tired — take a break from this song", "tired"
        )
        controls.append(self._tired_btn)

        self._skip_btn = self._icon_button(
            "media-skip-forward-symbolic", "Skip", "skip"
        )
        controls.append(self._skip_btn)

        # Volume lives in the row as a speaker icon; clicking it reveals a
        # compact slider in a popover instead of taking a whole bottom row.
        self._volume = Gtk.Scale.new_with_range(
            Gtk.Orientation.VERTICAL, 0.0, 1.0, 0.01
        )
        self._volume.set_inverted(True)  # up = louder
        self._volume.set_draw_value(False)
        self._volume.set_size_request(-1, 130)
        self._volume.set_vexpand(True)
        self._volume.connect("value-changed", self._on_volume)

        vol_pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vol_pop_box.set_margin_top(10)
        vol_pop_box.set_margin_bottom(10)
        vol_pop_box.set_margin_start(2)
        vol_pop_box.set_margin_end(2)
        vol_pop_box.append(self._volume)

        vol_popover = Gtk.Popover()
        vol_popover.set_child(vol_pop_box)

        self._volume_btn = Gtk.MenuButton()
        self._volume_btn.set_icon_name("audio-volume-high-symbolic")
        self._volume_btn.add_css_class("circular")
        self._volume_btn.add_css_class("flat")
        self._volume_btn.set_valign(Gtk.Align.CENTER)
        self._volume_btn.set_tooltip_text("Volume")
        self._volume_btn.set_popover(vol_popover)
        controls.append(self._volume_btn)

        self.set_controls_sensitive(False)

    # -- construction helpers ---------------------------------------------

    def _icon_button(self, icon: str, tooltip: str, signal: str) -> Gtk.Button:
        btn = Gtk.Button()
        btn.set_icon_name(icon)
        btn.add_css_class("circular")
        btn.add_css_class("flat")
        # Center vertically so the button keeps its natural (round) size in the
        # row next to the larger play/pause button — otherwise it stretches to
        # the row height and the hover highlight becomes a tall pill.
        btn.set_valign(Gtk.Align.CENTER)
        btn.set_tooltip_text(tooltip)
        btn.connect("clicked", lambda *_: self.emit(signal))
        return btn

    def _art_placeholder(self) -> None:
        self._art.set_paintable(None)

    # -- public API --------------------------------------------------------

    def set_volume(self, value: float) -> None:
        self._volume.set_value(value)
        self._update_volume_icon(value)

    def _update_volume_icon(self, value: float) -> None:
        if value <= 0.0:
            name = "audio-volume-muted-symbolic"
        elif value < 0.34:
            name = "audio-volume-low-symbolic"
        elif value < 0.67:
            name = "audio-volume-medium-symbolic"
        else:
            name = "audio-volume-high-symbolic"
        self._volume_btn.set_icon_name(name)

    def set_controls_sensitive(self, sensitive: bool) -> None:
        for btn in (
            self._ban_btn,
            self._love_btn,
            self._tired_btn,
            self._skip_btn,
            self._play_btn,
        ):
            btn.set_sensitive(sensitive)

    def set_playing(self, playing: bool) -> None:
        self._play_btn.set_icon_name(
            "media-playback-pause-symbolic"
            if playing
            else "media-playback-start-symbolic"
        )

    def set_song(self, song: Song) -> None:
        self._title.set_text(song.title or "Unknown title")
        self._artist.set_text(song.artist or "")
        self._album.set_text(song.album or "")
        self._duration = float(song.duration or 0)
        self._progress.set_fraction(0.0)
        self._elapsed.set_text("0:00")
        self._remaining.set_text(
            f"-{_format_time(self._duration)}" if self._duration else "-0:00"
        )
        self._update_rating_buttons(song)
        self._art_placeholder()

    def set_art(self, texture: Gdk.Texture | None) -> None:
        if texture is not None:
            self._art.set_paintable(texture)
        else:
            self._art_placeholder()

    def set_progress(self, position: float, duration: float) -> None:
        if duration <= 0:
            return
        self._duration = duration
        self._progress.set_fraction(min(1.0, position / duration))
        self._elapsed.set_text(_format_time(position))
        self._remaining.set_text(f"-{_format_time(duration - position)}")

    def pulse(self, label: str = "Buffering…") -> None:
        self._progress.pulse()

    # -- internal ----------------------------------------------------------

    def _update_rating_buttons(self, song: Song) -> None:
        if song.is_loved:
            self._love_btn.add_css_class("loved")
        else:
            self._love_btn.remove_css_class("loved")
        self._ban_btn.set_sensitive(not song.is_banned)

    def _on_volume(self, scale: Gtk.Scale) -> None:
        value = scale.get_value()
        self._update_volume_icon(value)
        self.emit("volume-changed", value)
