"""The :class:`Adw.Application` entry point."""

from __future__ import annotations

import importlib.resources as resources

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from .config import APP_ID, Config
from .ui import AmphoraWindow


class AmphoraApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.config = Config()
        self._window: AmphoraWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self._load_css()
        self._load_icons()
        self._setup_actions()

    def do_activate(self) -> None:
        if self._window is None:
            self._window = AmphoraWindow(self, self.config)
        self._window.present()

    def do_shutdown(self) -> None:
        if self._window is not None:
            self._window.shutdown()
        Adw.Application.do_shutdown(self)

    def _load_css(self) -> None:
        try:
            css = (
                resources.files("amphora.ui")
                .joinpath("style.css")
                .read_text(encoding="utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError):
            return
        provider = Gtk.CssProvider()
        provider.load_from_string(css)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

    def _load_icons(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        try:
            icons_dir = str(resources.files("amphora.ui").joinpath("icons"))
        except (FileNotFoundError, ModuleNotFoundError):
            return
        theme = Gtk.IconTheme.get_for_display(display)
        theme.add_search_path(icons_dir)

    def _setup_actions(self) -> None:
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        self.set_accels_for_action("win.toggle-mini", ["<Primary>m"])


def main() -> int:
    app = AmphoraApplication()
    return app.run(None)
