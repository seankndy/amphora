# Amphora

A modern **libadwaita** client for [Pandora Internet Radio](https://www.pandora.com),
built in the spirit of [Pithos](https://github.com/pithos/pithos) — but written
fresh for **GTK 4 / libadwaita** with Python.

<p align="center"><em>Sign in → pick a station → listen.</em></p>

## Screenshots

<p align="center">
  <img src="data/screenshots/now-playing.png" alt="Now playing" width="320">
  &nbsp;
  <img src="data/screenshots/mini-player.png" alt="Mini player" width="220">
</p>

<!-- Drop PNGs into data/screenshots/ with these names and they'll appear here. -->

## Features

- 🎵 Adaptive GTK4 / libadwaita interface that feels at home on modern GNOME
- 📻 Station list with QuickMix, instant switching
- ⏯️ Play / pause, skip, and album-art "now playing" view
- 👍 👎 ⏰ Love, ban, and *tired of this song* feedback to shape your stations
- 🎚️ Selectable audio quality (high / medium / low)
- 🔐 Password stored in the system keyring (libsecret), never on disk
- 🧵 All network I/O runs off the UI thread — the interface never freezes

## Requirements

These come from your distribution, not pip (they are GObject-introspection
bindings, not pure-Python wheels):

| Component | Notes |
|-----------|-------|
| Python ≥ 3.10 | |
| GTK 4 + libadwaita ≥ 1.5 | `Adw.PreferencesDialog`, `Adw.Spinner` etc. |
| PyGObject | `python3-gobject` |
| GStreamer 1.x | with `playbin`, `souphttpsrc`, and **AAC + MP3** decoders |
| `cryptography` | Blowfish ECB for the Pandora protocol |
| `requests` | HTTP transport |

On Fedora:

```bash
sudo dnf install python3-gobject gtk4 libadwaita \
    gstreamer1-plugins-good gstreamer1-plugins-bad-free \
    gstreamer1-plugins-ugly gstreamer1-libav \
    python3-cryptography python3-requests libsecret
```

On Debian/Ubuntu:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly gstreamer1.0-libav \
    python3-cryptography python3-requests gir1.2-secret-1
```

## Running

From a checkout, without installing:

```bash
python3 run.py
```

Or install it:

```bash
pip install --user .
amphora
```

### Flatpak

A Flatpak manifest lives in [`flatpak/`](flatpak/). It builds against
`org.gnome.Platform//50` (so it carries its own GTK4/libadwaita/GStreamer/
PyGObject) and bundles the only two non-runtime Python deps — `requests` and
`cryptography` — as pinned wheels in
[`flatpak/python3-deps.yaml`](flatpak/python3-deps.yaml).

```bash
sudo dnf install flatpak flatpak-builder    # or: apt install …
cd flatpak
./build-flatpak.sh                           # installs runtime, builds, installs app
flatpak run net.kndy.Amphora
```

The sandbox is granted exactly what Amphora needs: `network` (Pandora + art),
`pulseaudio` (playback), `wayland`/`x11`/`dri` (UI), and D-Bus access for the
keyring (`org.freedesktop.secrets`), notifications
(`org.freedesktop.Notifications`) and MPRIS media-key control
(`org.mpris.MediaPlayer2.Amphora`). AAC/MP3 decoding is provided by the GNOME
50 runtime itself (gst-libav / mpg123), so no extra codec extension is needed.

To target a different runtime, change `runtime-version` in
`net.kndy.Amphora.yaml`, then re-pin the wheels for that runtime's Python with
`./build-flatpak.sh --regen-deps` (the bundled set targets Python 3.13).

#### Single-file bundle (move to another machine)

```bash
cd flatpak
./build-flatpak.sh --bundle        # produces net.kndy.Amphora.flatpak
```

Copy `net.kndy.Amphora.flatpak` to the other machine and install it:

```bash
flatpak install --user net.kndy.Amphora.flatpak
flatpak run net.kndy.Amphora
```

The bundle contains **only the app**, not the ~hundreds-of-MB GNOME runtime.
It embeds Flathub's address (`--runtime-repo`), so on first install Flatpak
pulls `org.gnome.Platform//50` from Flathub automatically — the target machine
needs `flatpak` and network access (or the runtime already installed). For a
fully offline target, also transfer the runtime once with
`flatpak create-usb` or by installing `org.gnome.Platform//50` there first.

## How it works

Amphora speaks Pandora's JSON API v5, the same protocol Pithos has used for
years:

1. **Partner login** with the built-in `android` partner credentials returns a
   partner token and an encrypted *sync time*; the client decrypts it (Blowfish)
   and keeps the clock offset.
2. **User login** sends your credentials in a Blowfish-ECB-encrypted,
   hex-encoded body and yields a user auth token.
3. Content calls (`user.getStationList`, `station.getPlaylist`,
   `station.addFeedback`, `user.sleepSong`) carry that token. Tracks are
   streamed with GStreamer's `playbin`.

### Project layout

```
amphora/
├── application.py        # Adw.Application, CSS + actions
├── config.py             # JSON settings + libsecret password storage
├── player.py             # GStreamer playbin wrapper (GObject signals)
├── tasks.py              # run blocking work off the GTK main loop
├── pandora/
│   ├── crypt.py          # Blowfish ECB encrypt/decrypt
│   ├── client.py         # JSON API client (login, stations, playlist…)
│   └── models.py         # Station / Song value objects
└── ui/
    ├── window.py         # main window + session/queue orchestration
    ├── login.py          # sign-in view
    ├── player_view.py    # now-playing view
    └── style.css
```

## Tests

The protocol-critical code (crypto, parsing) has no-network unit tests:

```bash
python3 -m pytest tests/        # or: python3 tests/run.py
```

## Notes & limitations

- Amphora is an **independent** client and is not affiliated with or endorsed
  by Pandora Media. A valid Pandora account is required, and availability is
  subject to Pandora's terms and geographic restrictions.
- Pandora streams are not seekable; the progress bar is informational only.
- Bundle a `net.kndy.Amphora.svg` icon under your icon theme to replace the
  placeholder headphones symbol used in the header and About dialog.

## License

GPL-3.0-or-later, matching Pithos.

Amphora is not affiliated or endorsed by Pandora Media, LLC.
