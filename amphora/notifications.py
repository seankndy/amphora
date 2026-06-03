"""Desktop notifications via the freedesktop ``Notifications`` service.

We deliberately bypass :meth:`Gio.Application.send_notification` (GNOME's
``org.gtk.Notifications`` backend), whose banners are unreliable for an app
run from source — they can be filed silently into the tray, suppressed, or
not shown on update. The freedesktop spec interface (the same one
``notify-send`` and libnotify use) reliably raises a banner and supports
``replaces_id`` so each track change updates the same notification in place
while still re-displaying it.
"""

from __future__ import annotations

from gi.repository import Gio, GLib

_DEST = "org.freedesktop.Notifications"
_PATH = "/org/freedesktop/Notifications"
_IFACE = "org.freedesktop.Notifications"


class Notifier:
    def __init__(self, app_name: str = "Amphora") -> None:
        self._app_name = app_name
        self._last_id = 0
        try:
            self._conn: Gio.DBusConnection | None = Gio.bus_get_sync(
                Gio.BusType.SESSION, None
            )
        except GLib.Error:
            self._conn = None

    def notify(
        self,
        summary: str,
        body: str = "",
        *,
        icon: str = "net.kndy.Amphora",
        image_path: str | None = None,
        timeout_ms: int = 6000,
    ) -> None:
        if self._conn is None:
            return

        hints: dict[str, GLib.Variant] = {
            # Normal urgency so it raises a banner (not just a tray entry).
            "urgency": GLib.Variant("y", 1),
            # Hint to the shell that this is a transient, replaceable notice.
            "category": GLib.Variant("s", "x-gnome.music"),
            "desktop-entry": GLib.Variant("s", "net.kndy.Amphora"),
        }
        if image_path:
            hints["image-path"] = GLib.Variant("s", image_path)

        params = GLib.Variant(
            "(susssasa{sv}i)",
            (
                self._app_name,
                self._last_id,  # replaces_id: 0 = new, else update existing
                icon,  # app icon; image-path hint (below) takes visual priority
                summary,
                body,
                [],  # actions
                hints,
                timeout_ms,
            ),
        )
        self._conn.call(
            _DEST, _PATH, _IFACE, "Notify", params,
            GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, -1, None,
            self._on_notified,
        )

    def _on_notified(self, conn: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        try:
            value = conn.call_finish(result)
            self._last_id = value.unpack()[0]
        except GLib.Error:
            pass

    def close(self) -> None:
        if self._conn is None or not self._last_id:
            return
        self._conn.call(
            _DEST, _PATH, _IFACE, "CloseNotification",
            GLib.Variant("(u)", (self._last_id,)),
            None, Gio.DBusCallFlags.NONE, -1, None, None,
        )
        self._last_id = 0
