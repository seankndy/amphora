"""Run blocking work on a background thread, deliver results on the GTK loop.

The Pandora client is synchronous; calling it directly would freeze the UI.
``run_async`` runs a callable in a worker thread and marshals the result (or
exception) back to the main loop via ``GLib.idle_add``.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from gi.repository import GLib


def run_async(
    func: Callable[..., Any],
    *args: Any,
    on_success: Callable[[Any], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
) -> None:
    def worker() -> None:
        try:
            result = func(*args)
        except Exception as exc:  # noqa: BLE001 - forwarded to caller
            if on_error is not None:
                GLib.idle_add(on_error, exc)
            return
        if on_success is not None:
            GLib.idle_add(on_success, result)

    threading.Thread(target=worker, daemon=True).start()
