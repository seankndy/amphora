"""The compact 'mini' now-playing view.

Album art fills the (small, square) window. Moving the pointer over it fades
in a chrome overlay with the transport controls, a thin progress bar and a
button to restore the normal layout. Right-clicking requests a station menu.
"""

from __future__ import annotations

from gi.repository import Gdk, GObject, Gtk, Pango

from ..pandora import Song


class MiniView(Gtk.Overlay):
    __gsignals__ = {
        "play-pause": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "skip": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "love": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "ban": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "tired": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "exit-mini": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # (x, y) in widget coordinates — window pops a station menu there.
        "menu-request": (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
    }

    def __init__(self) -> None:
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)

        # -- background placeholder (behind the art) -----------------------
        placeholder = Gtk.Image.new_from_icon_name("audio-headphones-symbolic")
        placeholder.set_pixel_size(64)
        placeholder.add_css_class("dim-label")
        placeholder.set_halign(Gtk.Align.CENTER)
        placeholder.set_valign(Gtk.Align.CENTER)
        bg = Gtk.Box()
        bg.add_css_class("mini-bg")
        bg.append(placeholder)
        placeholder.set_hexpand(True)
        placeholder.set_vexpand(True)
        self.set_child(bg)

        # -- album art (fills, on top of placeholder) ----------------------
        self._art = Gtk.Picture()
        self._art.set_content_fit(Gtk.ContentFit.COVER)
        self._art.set_hexpand(True)
        self._art.set_vexpand(True)
        self._art.set_can_target(False)
        self.add_overlay(self._art)

        # -- hover chrome (controls), fades in/out -------------------------
        self._chrome_revealer = Gtk.Revealer()
        self._chrome_revealer.set_transition_type(
            Gtk.RevealerTransitionType.CROSSFADE
        )
        self._chrome_revealer.set_transition_duration(180)
        self._chrome_revealer.set_reveal_child(False)
        self._chrome_revealer.set_hexpand(True)
        self._chrome_revealer.set_vexpand(True)
        self.add_overlay(self._chrome_revealer)

        chrome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        chrome.add_css_class("mini-chrome")
        self._chrome_revealer.set_child(chrome)

        # Top row: restore-to-normal button.
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        top.set_margin_top(6)
        top.set_margin_end(6)
        top.set_margin_start(6)
        restore = Gtk.Button(icon_name="view-restore-symbolic")
        restore.add_css_class("osd")
        restore.add_css_class("circular")
        restore.set_tooltip_text("Normal view")
        restore.set_halign(Gtk.Align.END)
        restore.set_hexpand(True)
        restore.connect("clicked", lambda *_: self.emit("exit-mini"))
        top.append(restore)
        chrome.append(top)

        # Spacer pushes the metadata + controls to the bottom.
        spacer = Gtk.Box(vexpand=True)
        chrome.append(spacer)

        # Track metadata, shown just above the controls on hover.
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        info.add_css_class("mini-info")
        info.set_halign(Gtk.Align.CENTER)
        info.set_margin_start(8)
        info.set_margin_end(8)
        info.set_margin_bottom(12)

        self._title = Gtk.Label()
        self._title.add_css_class("mini-title")
        self._artist = Gtk.Label()
        self._artist.add_css_class("mini-artist")
        self._album = Gtk.Label()
        self._album.add_css_class("mini-album")
        for lbl in (self._title, self._artist, self._album):
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(24)
            lbl.set_single_line_mode(True)
            lbl.set_halign(Gtk.Align.CENTER)
            info.append(lbl)
        chrome.append(info)

        # Bottom: transport controls.
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls.set_halign(Gtk.Align.CENTER)
        controls.set_margin_bottom(8)
        chrome.append(controls)

        self._ban_btn = self._button("amphora-ban-symbolic", "Ban", "ban")
        self._love_btn = self._button("amphora-thumbs-up-symbolic", "Love", "love")
        self._play_btn = self._button(
            "media-playback-start-symbolic", "Play / Pause", "play-pause"
        )
        self._tired_btn = self._button(
            "amphora-thumbs-down-symbolic", "Tired of this song", "tired"
        )
        self._skip_btn = self._button(
            "media-skip-forward-symbolic", "Skip", "skip"
        )
        for btn in (
            self._ban_btn,
            self._love_btn,
            self._play_btn,
            self._tired_btn,
            self._skip_btn,
        ):
            controls.append(btn)

        # Thin progress along the very bottom.
        self._progress = Gtk.ProgressBar()
        self._progress.add_css_class("osd")
        self._progress.add_css_class("mini-progress")
        chrome.append(self._progress)

        # -- interaction ---------------------------------------------------
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._reveal(True))
        motion.connect("leave", lambda *_: self._reveal(False))
        self.add_controller(motion)

        right_click = Gtk.GestureClick()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_secondary)
        self.add_controller(right_click)

        # Dragging the art (anywhere not on a button) moves the whole window.
        # A drag gesture only fires once the pointer actually moves, so plain
        # clicks fall through to the buttons untouched.
        drag = Gtk.GestureDrag()
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        self.add_controller(drag)

        self.set_controls_sensitive(False)

    # -- construction helpers ---------------------------------------------

    def _button(self, icon: str, tooltip: str, signal: str) -> Gtk.Button:
        btn = Gtk.Button(icon_name=icon)
        btn.add_css_class("osd")
        btn.add_css_class("circular")
        btn.set_tooltip_text(tooltip)
        btn.connect("clicked", lambda *_: self.emit(signal))
        return btn

    # -- public API --------------------------------------------------------

    def set_controls_sensitive(self, sensitive: bool) -> None:
        for btn in (
            self._ban_btn,
            self._love_btn,
            self._play_btn,
            self._tired_btn,
            self._skip_btn,
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
        self._album.set_visible(bool(song.album))
        self.set_tooltip_text(
            f"{song.artist} — {song.title}" if song.artist else song.title
        )
        if song.is_loved:
            self._love_btn.add_css_class("loved")
        else:
            self._love_btn.remove_css_class("loved")
        self._progress.set_fraction(0.0)

    def set_art(self, texture: Gdk.Texture | None) -> None:
        self._art.set_paintable(texture)

    def set_progress(self, position: float, duration: float) -> None:
        if duration > 0:
            self._progress.set_fraction(min(1.0, position / duration))

    # -- internal ----------------------------------------------------------

    def _reveal(self, reveal: bool) -> None:
        self._chrome_revealer.set_reveal_child(reveal)

    def _on_secondary(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        self.emit("menu-request", x, y)

    def _on_drag_begin(
        self, gesture: Gtk.GestureDrag, start_x: float, start_y: float
    ) -> None:
        # Don't move the window if the drag started on a control.
        if self._is_over_button(start_x, start_y):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        root = self.get_native()
        if root is None:
            return
        surface = root.get_surface()
        if surface is None or not hasattr(surface, "begin_move"):
            return
        device = gesture.get_current_event_device()
        if device is None:
            return
        surface.begin_move(
            device,
            Gdk.BUTTON_PRIMARY,
            start_x,
            start_y,
            gesture.get_current_event_time(),
        )

    def _is_over_button(self, x: float, y: float) -> bool:
        widget = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and widget is not self:
            if isinstance(widget, Gtk.Button):
                return True
            widget = widget.get_parent()
        return False
