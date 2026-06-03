"""The sign-in view shown before a session is established."""

from __future__ import annotations

from gi.repository import Adw, GObject, Gtk


class LoginView(Gtk.Box):
    """A clamped Pandora sign-in form.

    Emits ``submit`` with (email, password) when the user attempts to log in.
    """

    __gsignals__ = {
        "submit": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)

        clamp = Adw.Clamp(maximum_size=400, tightening_threshold=360)
        self.append(clamp)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(12)
        box.set_margin_end(12)
        clamp.set_child(box)

        icon = Gtk.Image.new_from_icon_name("net.kndy.Amphora")
        icon.set_pixel_size(96)
        box.append(icon)

        title = Gtk.Label(label="Sign in to Pandora")
        title.add_css_class("title-1")
        box.append(title)

        subtitle = Gtk.Label(
            label="Enter your Pandora account to start listening."
        )
        subtitle.add_css_class("dim-label")
        subtitle.set_wrap(True)
        subtitle.set_justify(Gtk.Justification.CENTER)
        box.append(subtitle)

        group = Adw.PreferencesGroup()
        box.append(group)

        self._email = Adw.EntryRow(title="Email")
        self._email.set_input_purpose(Gtk.InputPurpose.EMAIL)
        group.add(self._email)

        self._password = Adw.PasswordEntryRow(title="Password")
        group.add(self._password)

        self._error = Gtk.Label()
        self._error.add_css_class("error")
        self._error.set_wrap(True)
        self._error.set_visible(False)
        box.append(self._error)

        self._button = Gtk.Button(label="Sign In")
        self._button.add_css_class("suggested-action")
        self._button.add_css_class("pill")
        self._button.set_halign(Gtk.Align.CENTER)
        self._button.connect("clicked", self._on_submit)
        box.append(self._button)

        self._spinner = Adw.Spinner()
        self._spinner.set_visible(False)
        self._spinner.set_size_request(32, 32)
        box.append(self._spinner)

        self._email.connect("entry-activated", self._on_submit)
        self._password.connect("entry-activated", self._on_submit)

    # -- public API --------------------------------------------------------

    def prefill(self, email: str) -> None:
        if email:
            self._email.set_text(email)
            self._password.grab_focus()

    def set_busy(self, busy: bool) -> None:
        self._button.set_sensitive(not busy)
        self._email.set_sensitive(not busy)
        self._password.set_sensitive(not busy)
        self._spinner.set_visible(busy)
        if busy:
            self._error.set_visible(False)

    def show_error(self, message: str) -> None:
        self.set_busy(False)
        self._error.set_text(message)
        self._error.set_visible(True)

    # -- internal ----------------------------------------------------------

    def _on_submit(self, *_args: object) -> None:
        email = self._email.get_text().strip()
        password = self._password.get_text()
        if not email or not password:
            self.show_error("Please enter both your email and password.")
            return
        self.set_busy(True)
        self.emit("submit", email, password)
