"""The main application window and session orchestration."""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

import requests

from ..config import Config
from ..mpris import MPRIS
from ..notifications import Notifier
from ..pandora import (
    PandoraClient,
    PandoraError,
    PandoraNetworkError,
    Song,
    Station,
)
from ..player import Player
from ..tasks import run_async
from .mini_view import MiniView

# Window geometry per display mode: (default_w, default_h, min_w, min_h).
# The mini minimum is kept large enough that the controls never clip — the
# overlaid chrome doesn't contribute to the window's own size request.
_NORMAL_GEOMETRY = (420, 720, 360, 520)
_MINI_GEOMETRY = (300, 300, 260, 240)
from .login import LoginView
from .player_view import PlayerView

# Refill the queue once this many songs (or fewer) remain.
_REFILL_THRESHOLD = 2


class AmphoraWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, config: Config) -> None:
        super().__init__(application=application)
        self.config = config
        self.client = PandoraClient()
        self.player = Player()

        self._stations: list[Station] = []
        self._queue: list[Song] = []
        self._current: Song | None = None
        self._current_station: Station | None = None
        self._refilling = False
        self._art_token = 0
        self._art_session = requests.Session()
        self._current_texture: Gdk.Texture | None = None
        self._display_mode = "normal"
        self._notifier = Notifier()

        self.set_title("Amphora")
        self.set_default_size(420, 720)
        self.set_size_request(360, 520)

        self._build_ui()
        self._wire_player()
        self._install_actions()
        self._setup_mpris()

        # Restore volume.
        vol = float(self.config.get("volume"))
        self.player.set_volume(vol)
        self._player_view.set_volume(vol)

        self._try_autologin()

    # -- UI construction ---------------------------------------------------

    def _build_ui(self) -> None:
        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        self._header = Adw.HeaderBar()
        toolbar.add_top_bar(self._header)

        # Display mode is switched via the menu's "Mini Player" item / Ctrl+M.

        # Station selector (hidden until logged in).
        self._station_model = Gtk.StringList()
        self._station_dropdown = Gtk.DropDown(model=self._station_model)
        self._station_dropdown.set_tooltip_text("Choose a station")
        self._station_dropdown.set_visible(False)
        self._station_dropdown.connect(
            "notify::selected", self._on_station_selected
        )
        self._header.set_title_widget(self._station_dropdown)

        # Main menu.
        menu = Gio.Menu()
        menu.append("Mini Player", "win.toggle-mini")
        menu.append("Refresh Stations", "win.refresh-stations")
        menu.append("Preferences", "win.preferences")
        menu.append("Sign Out", "win.logout")
        menu.append("About Amphora", "win.about")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_btn.set_menu_model(menu)
        menu_btn.set_tooltip_text("Main menu")
        self._header.pack_end(menu_btn)

        # Toast overlay wraps the stack so we can surface transient errors.
        self._toasts = Adw.ToastOverlay()
        toolbar.set_content(self._toasts)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        # Size to the visible child only, so mini mode can shrink the window
        # below the (much larger) normal player view's size.
        self._stack.set_hhomogeneous(False)
        self._stack.set_vhomogeneous(False)
        self._toasts.set_child(self._stack)

        self._login_view = LoginView()
        self._login_view.connect("submit", self._on_login_submit)
        self._stack.add_named(self._login_view, "login")

        self._player_view = PlayerView()
        self._player_view.connect("play-pause", lambda *_: self.player.toggle())
        self._player_view.connect("skip", lambda *_: self._skip())
        self._player_view.connect("love", lambda *_: self._rate(True))
        self._player_view.connect("ban", lambda *_: self._rate(False))
        self._player_view.connect("tired", lambda *_: self._tired())
        self._player_view.connect("volume-changed", self._on_volume_changed)
        self._stack.add_named(self._player_view, "player")

        self._mini_view = MiniView()
        self._mini_view.connect("play-pause", lambda *_: self.player.toggle())
        self._mini_view.connect("skip", lambda *_: self._skip())
        self._mini_view.connect("love", lambda *_: self._rate(True))
        self._mini_view.connect("ban", lambda *_: self._rate(False))
        self._mini_view.connect("tired", lambda *_: self._tired())
        self._mini_view.connect("exit-mini", lambda *_: self._set_display_mode("normal"))
        self._mini_view.connect("menu-request", self._show_mini_menu)
        self._stack.add_named(self._mini_view, "mini")

        # A simple loading page.
        status = Adw.StatusPage(
            title="Connecting…",
            icon_name="net.kndy.Amphora",
        )
        spinner = Adw.Spinner()
        spinner.set_size_request(48, 48)
        status.set_child(spinner)
        self._stack.add_named(status, "loading")

        self._stack.set_visible_child_name("login")

    def _wire_player(self) -> None:
        self.player.connect("eos", lambda *_: self._play_next())
        self.player.connect("error", self._on_player_error)
        self.player.connect("state-changed", self._on_player_state)
        self.player.connect("progress", self._on_player_progress)
        self.player.connect("buffering", self._on_player_buffering)

    def _install_actions(self) -> None:
        for name, handler in (
            ("preferences", self._show_preferences),
            ("about", self._show_about),
            ("logout", self._logout),
            ("refresh-stations", lambda *_: self._load_stations()),
            ("toggle-mini", self._toggle_mini),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

        # Switch to normal layout (used from the mini right-click menu).
        normal_action = Gio.SimpleAction.new("display-normal", None)
        normal_action.connect(
            "activate", lambda *_: self._set_display_mode("normal")
        )
        self.add_action(normal_action)

        # Select a station by index (used from the mini right-click menu).
        select_action = Gio.SimpleAction.new(
            "select-station", GLib.VariantType.new("i")
        )
        select_action.connect("activate", self._on_select_station_action)
        self.add_action(select_action)

    def _on_select_station_action(
        self, _action: Gio.SimpleAction, param: GLib.Variant
    ) -> None:
        index = param.get_int32()
        if 0 <= index < len(self._stations):
            self._station_dropdown.set_selected(index)

    def _setup_mpris(self) -> None:
        try:
            self._mpris: MPRIS | None = MPRIS(
                on_play_pause=lambda: self.player.toggle(),
                on_play=lambda: self.player.play(),
                on_pause=lambda: self.player.pause(),
                on_stop=lambda: self.player.stop(),
                on_next=lambda: self._skip(),
                on_raise=self._present_from_mpris,
                on_quit=lambda: self.get_application().quit(),
                get_position_us=self.player.get_position_us,
                get_volume=lambda: self.player.volume,
                set_volume=self._set_volume_from_mpris,
            )
        except Exception:  # noqa: BLE001 - D-Bus may be unavailable
            self._mpris = None

    def _present_from_mpris(self) -> None:
        self.present()

    def _set_volume_from_mpris(self, value: float) -> None:
        self.player.set_volume(value)
        self._player_view.set_volume(value)
        self.config.set("volume", value)

    # -- display mode (normal / mini) --------------------------------------

    def _toggle_mini(self, *_args: object) -> None:
        # Ctrl+M / menu: flip between mini and normal (only when signed in).
        if not self.client.is_logged_in:
            return
        self._set_display_mode(
            "normal" if self._display_mode == "mini" else "mini"
        )

    def _set_display_mode(self, mode: str) -> None:
        if mode not in ("normal", "mini"):
            mode = "normal"
        self._display_mode = mode
        self.config.set("display_mode", mode)
        self.config.save()
        self._apply_layout()

    def _apply_layout(self) -> None:
        """Reflect the current display mode + login state in the window."""
        logged_in = self.client.is_logged_in
        mini = logged_in and self._display_mode == "mini"
        if mini:
            geom = _MINI_GEOMETRY
        else:
            geom = _NORMAL_GEOMETRY

        # The header is hidden in mini mode so the art fills the window.
        self._header.set_visible(not mini)

        if logged_in:
            self._stack.set_visible_child_name("mini" if mini else "player")

        # Resize live: clear the min-size first so the window may shrink.
        self.set_size_request(geom[2], geom[3])
        self.set_default_size(geom[0], geom[1])

    def _show_mini_menu(self, mini_view: MiniView, x: float, y: float) -> None:
        menu = Gio.Menu()
        stations = Gio.Menu()
        for i, station in enumerate(self._stations):
            item = Gio.MenuItem.new(station.name, None)
            item.set_action_and_target_value(
                "win.select-station", GLib.Variant("i", i)
            )
            stations.append_item(item)
        menu.append_section("Stations", stations)
        menu.append("Normal View", "win.display-normal")

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(mini_view)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    def shutdown(self) -> None:
        """Release D-Bus resources; called on application shutdown."""
        self._notifier.close()
        if getattr(self, "_mpris", None) is not None:
            self._mpris.shutdown()

    # -- notifications -----------------------------------------------------

    def _notify_now_playing(self, song: Song, image_path: str | None) -> None:
        if not self.config.get("notifications"):
            return
        body = song.artist or ""
        if song.album:
            body = f"{body} — {song.album}" if body else song.album
        self._notifier.notify(
            song.title or "Now playing", body, image_path=image_path
        )

    # -- authentication ----------------------------------------------------

    def _try_autologin(self) -> None:
        username = self.config.get("username")
        password = self.config.lookup_password(username) if username else None
        if username and password:
            self._login_view.prefill(username)
            self._stack.set_visible_child_name("loading")
            self._do_login(username, password, remember=True)
        else:
            self._login_view.prefill(username)

    def _on_login_submit(self, _view: LoginView, email: str, password: str) -> None:
        self._do_login(email, password, remember=True)

    def _do_login(self, email: str, password: str, *, remember: bool) -> None:
        def attempt() -> None:
            self.client.login(email, password)

        def done(_result: object) -> None:
            self.config.set("username", email)
            if remember:
                self.config.store_password(email, password)
            self.config.save()
            self._load_stations()

        def failed(exc: Exception) -> None:
            self._stack.set_visible_child_name("login")
            self._login_view.show_error(self._error_text(exc))

        run_async(attempt, on_success=done, on_error=failed)

    def _logout(self, *_args: object) -> None:
        self.player.stop()
        self._notifier.close()
        username = self.config.get("username")
        if username:
            self.config.clear_password(username)
        self.client = PandoraClient()
        self._stations = []
        self._queue = []
        self._current = None
        self._current_station = None
        self._station_dropdown.set_visible(False)
        self._station_model.splice(0, self._station_model.get_n_items(), [])
        self._player_view.set_controls_sensitive(False)
        self._mini_view.set_controls_sensitive(False)
        self._login_view.set_busy(False)
        self._stack.set_visible_child_name("login")
        # Drop back to the normal window chrome while signed out.
        self._apply_layout()

    # -- stations ----------------------------------------------------------

    def _load_stations(self) -> None:
        self._stack.set_visible_child_name("loading")

        def done(stations: list[Station]) -> None:
            self._stations = stations
            self._station_model.splice(
                0, self._station_model.get_n_items(), [s.name for s in stations]
            )
            self._station_dropdown.set_visible(bool(stations))
            self._stack.set_visible_child_name("player")
            # Restore the saved display mode now that we're signed in.
            self._display_mode = self.config.get("display_mode") or "normal"
            self._apply_layout()
            if not stations:
                self._toast("No stations found on this account.")
                return
            index = self._initial_station_index()
            self._station_dropdown.set_selected(index)
            # If selection index is already 0, notify won't fire; start directly.
            if index == 0:
                self._play_station(self._stations[0])

        run_async(
            self._with_relogin,
            self.client.get_stations,
            on_success=done,
            on_error=self._on_api_error,
        )

    def _initial_station_index(self) -> int:
        last = self.config.get("last_station_token")
        for i, station in enumerate(self._stations):
            if station.token == last:
                return i
        return 0

    def _on_station_selected(self, dropdown: Gtk.DropDown, _param: object) -> None:
        index = dropdown.get_selected()
        if index == Gtk.INVALID_LIST_POSITION or index >= len(self._stations):
            return
        station = self._stations[index]
        if self._current_station and station.token == self._current_station.token:
            return
        self._play_station(station)

    def _play_station(self, station: Station) -> None:
        self._current_station = station
        self.config.set("last_station_token", station.token)
        self.config.save()
        self._queue = []
        self._current = None
        self.player.stop()
        self._refill_queue(then_play=True)

    # -- playback queue ----------------------------------------------------

    def _refill_queue(self, *, then_play: bool = False) -> None:
        if self._refilling or not self._current_station:
            return
        self._refilling = True
        station = self._current_station
        quality = self.config.get("audio_quality")

        def fetch() -> list[Song]:
            return self._with_relogin(
                self.client.get_playlist, station.token, quality
            )

        def done(songs: list[Song]) -> None:
            self._refilling = False
            # Ignore results if the station changed while fetching.
            if not self._current_station or station.token != self._current_station.token:
                return
            self._queue.extend(songs)
            if then_play and self._current is None:
                self._play_next()

        def failed(exc: Exception) -> None:
            self._refilling = False
            self._on_api_error(exc)

        run_async(fetch, on_success=done, on_error=failed)

    def _play_next(self) -> None:
        if not self._queue:
            self._refill_queue(then_play=True)
            return
        song = self._queue.pop(0)
        self._current = song
        self._player_view.set_song(song)
        self._mini_view.set_song(song)
        self._player_view.set_controls_sensitive(True)
        self._mini_view.set_controls_sensitive(True)
        self.player.load(song.audio_url, autoplay=True)
        if self._mpris is not None:
            self._mpris.set_song(song)
        # Notify now; refreshed with album art once it downloads (same id).
        if not song.art_url:
            self._notify_now_playing(song, None)
        self._load_art(song)
        if len(self._queue) <= _REFILL_THRESHOLD:
            self._refill_queue()

    def _skip(self) -> None:
        self.player.stop()
        self._play_next()

    # -- album art ---------------------------------------------------------

    def _load_art(self, song: Song) -> None:
        self._art_token += 1
        token = self._art_token
        url = song.art_url
        if not url:
            self._set_art(None)
            return

        def fetch() -> bytes:
            resp = self._art_session.get(url, timeout=20)
            resp.raise_for_status()
            return resp.content

        def done(data: bytes) -> None:
            if token != self._art_token:
                return  # a newer track superseded this art
            self._set_art(self._texture_from_bytes(data))
            self._notify_now_playing(song, self._cache_cover(data, token))

        def failed(_exc: Exception) -> None:
            if token == self._art_token:
                self._set_art(None)
                self._notify_now_playing(song, None)

        run_async(fetch, on_success=done, on_error=failed)

    @staticmethod
    def _cache_cover(data: bytes, token: int) -> str | None:
        """Write cover bytes to a cache file for the notification image.

        The filename is unique per track: notification daemons (GNOME Shell)
        cache the image by path, so reusing one filename makes the next song
        show the previous song's art. A fresh path forces a reload; older
        covers are pruned so the cache doesn't grow.
        """
        try:
            cache_dir = os.path.join(GLib.get_user_cache_dir(), "amphora")
            os.makedirs(cache_dir, exist_ok=True)
            current = f"cover-{token}"
            for name in os.listdir(cache_dir):
                if name.startswith("cover") and name != current:
                    try:
                        os.remove(os.path.join(cache_dir, name))
                    except OSError:
                        pass
            path = os.path.join(cache_dir, current)
            with open(path, "wb") as fh:
                fh.write(data)
            return GLib.filename_to_uri(path, None)
        except (OSError, GLib.Error):
            return None

    def _set_art(self, texture: Gdk.Texture | None) -> None:
        self._current_texture = texture
        self._player_view.set_art(texture)
        self._mini_view.set_art(texture)

    @staticmethod
    def _texture_from_bytes(data: bytes) -> Gdk.Texture | None:
        try:
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
            if pixbuf is None:
                return None
            return Gdk.Texture.new_for_pixbuf(pixbuf)
        except GLib.Error:
            return None

    # -- feedback ----------------------------------------------------------

    def _rate(self, positive: bool) -> None:
        song = self._current
        station = self._current_station
        if song is None or station is None:
            return
        song.rating = 1 if positive else -1

        def call() -> None:
            self._with_relogin(
                self.client.add_feedback, station.token, song.track_token, positive
            )

        run_async(
            call,
            on_success=lambda _r: self._toast(
                "Loved this song." if positive else "Banned — skipping."
            ),
            on_error=self._on_api_error,
        )
        if not positive:
            self._skip()

    def _tired(self) -> None:
        song = self._current
        if song is None:
            return

        def call() -> None:
            self._with_relogin(self.client.sleep_song, song.track_token)

        run_async(
            call,
            on_success=lambda _r: self._toast("Tired of this song — skipping."),
            on_error=self._on_api_error,
        )
        self._skip()

    # -- player callbacks --------------------------------------------------

    def _on_player_state(self, _player: Player, state: str) -> None:
        playing = state == "playing"
        self._player_view.set_playing(playing)
        self._mini_view.set_playing(playing)
        if self._mpris is not None:
            self._mpris.set_playback_status(state)

    def _on_player_progress(self, _player: Player, pos: float, dur: float) -> None:
        self._player_view.set_progress(pos, dur)
        self._mini_view.set_progress(pos, dur)

    def _on_player_buffering(self, _player: Player, percent: int) -> None:
        if percent < 100:
            self._player_view.pulse()

    def _on_player_error(self, _player: Player, message: str) -> None:
        self._toast(f"Playback error: {message}")
        # Skip past an unplayable track rather than getting stuck.
        GLib.timeout_add(1200, self._advance_after_error)

    def _advance_after_error(self) -> bool:
        self._play_next()
        return GLib.SOURCE_REMOVE

    # -- volume ------------------------------------------------------------

    def _on_volume_changed(self, _view: PlayerView, value: float) -> None:
        self.player.set_volume(value)
        self.config.set("volume", value)
        self.config.save()
        if self._mpris is not None:
            self._mpris.notify_volume(value)

    # -- error handling / helpers -----------------------------------------

    def _with_relogin(self, func, *args):
        """Call ``func``; on auth expiry, re-login once and retry.

        Runs inside a worker thread — must not touch the UI.
        """
        try:
            return func(*args)
        except PandoraError as exc:
            if exc.auth_expired and self.client.relogin():
                return func(*args)
            raise

    def _on_api_error(self, exc: Exception) -> None:
        if isinstance(exc, PandoraError) and exc.auth_expired:
            self._toast("Session expired. Please sign in again.")
            self._logout()
            return
        self._toast(self._error_text(exc))

    @staticmethod
    def _error_text(exc: Exception) -> str:
        if isinstance(exc, PandoraNetworkError):
            return "Could not reach Pandora. Check your connection."
        if isinstance(exc, PandoraError):
            return str(exc)
        return f"Something went wrong: {exc}"

    def _toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=message, timeout=4))

    # -- preferences / about ----------------------------------------------

    def _show_preferences(self, *_args: object) -> None:
        page = Adw.PreferencesPage(title="Preferences")
        group = Adw.PreferencesGroup(title="Playback")

        quality_row = Adw.ComboRow(title="Audio quality")
        quality_row.set_subtitle("Higher quality uses more bandwidth")
        qualities = Gtk.StringList()
        labels = ["High", "Medium", "Low"]
        keys = ["high", "medium", "low"]
        for label in labels:
            qualities.append(label)
        quality_row.set_model(qualities)
        current = self.config.get("audio_quality")
        quality_row.set_selected(keys.index(current) if current in keys else 0)

        def on_quality(row: Adw.ComboRow, _param: object) -> None:
            self.config.set("audio_quality", keys[row.get_selected()])
            self.config.save()

        quality_row.connect("notify::selected", on_quality)
        group.add(quality_row)

        notify_row = Adw.SwitchRow(title="Song change notifications")
        notify_row.set_subtitle("Show a notification when the track changes")
        notify_row.set_active(bool(self.config.get("notifications")))

        def on_notify(row: Adw.SwitchRow, _param: object) -> None:
            self.config.set("notifications", row.get_active())
            self.config.save()
            if not row.get_active():
                self._notifier.close()

        notify_row.connect("notify::active", on_notify)
        group.add(notify_row)
        page.add(group)

        dialog = Adw.PreferencesDialog()
        dialog.add(page)
        dialog.present(self)

    def _show_about(self, *_args: object) -> None:
        about = Adw.AboutDialog(
            application_name="Amphora",
            application_icon="net.kndy.Amphora",
            developer_name="Amphora contributors",
            version="0.1.0",
            comments=(
                "A modern libadwaita client for Pandora Internet Radio, "
                "in the spirit of Pithos."
            ),
            website="https://github.com/pithos/pithos",
            license_type=Gtk.License.GPL_3_0,
            copyright="© 2026 Amphora contributors",
        )
        about.add_legal_section(
            "Disclaimer",
            "Amphora is not affiliated with or endorsed by Pandora Media, LLC.",
            Gtk.License.UNKNOWN,
            None,
        )
        about.present(self)
