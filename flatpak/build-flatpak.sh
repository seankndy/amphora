#!/usr/bin/env bash
#
# Build and install Amphora as a Flatpak (user install).
#
# Requirements: flatpak and flatpak-builder.
#   Fedora:  sudo dnf install flatpak flatpak-builder
#   Debian:  sudo apt install flatpak flatpak-builder
#
# Usage:
#   ./build-flatpak.sh            # build + install locally + show run command
#   ./build-flatpak.sh --bundle   # build a single-file net.kndy.Amphora.flatpak
#   ./build-flatpak.sh --regen-deps   # re-pin python3-deps.yaml for the runtime
#
set -euo pipefail

if [[ ${EUID} -eq 0 ]]; then
  echo "Don't run this with sudo — it does a per-user (--user) Flatpak install." >&2
  echo "Run it as your normal user:  ./build-flatpak.sh" >&2
  exit 1
fi

cd "$(dirname "$0")"

RUNTIME_VERSION='50'
PY_VERSION='313'   # Python shipped by org.gnome.Platform//50
FLATHUB='https://flathub.org/repo/flathub.flatpakrepo'
WORK=/tmp/amphora-flatpak

# Pick a builder: prefer a native flatpak-builder, else the Flatpak'd one
# (org.flatpak.Builder) — handy on immutable hosts (Silverblue/Kinoite) where
# you can't easily layer flatpak-builder.
if command -v flatpak-builder >/dev/null 2>&1; then
  FB=(flatpak-builder)
elif flatpak info org.flatpak.Builder >/dev/null 2>&1; then
  FB=(flatpak run org.flatpak.Builder)
else
  echo "Need a builder. Either install 'flatpak-builder', or run:" >&2
  echo "    flatpak install -y --user flathub org.flatpak.Builder" >&2
  exit 1
fi

# Inside a container (distrobox/toolbox), rofiles-fuse can't mount — it needs
# /dev/fuse and privileges containers usually lack. Disable it there.
BUILDER_FLAGS=()
if [[ -f /run/.containerenv || -f /.dockerenv || -n "${container:-}" ]]; then
  echo "Container detected — building with --disable-rofiles-fuse."
  BUILDER_FLAGS+=(--disable-rofiles-fuse)
fi

if [[ "${1:-}" == "--regen-deps" ]]; then
  echo "Re-pinning Python wheels for Python ${PY_VERSION}…"
  python3 -m pip install --dry-run --quiet --report /tmp/amphora-report.json \
    --target /tmp/amphora-deps --only-binary=:all: --python-version "${PY_VERSION}" \
    --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 --platform any \
    requests cryptography
  python3 - <<'PY'
import json
d = json.load(open('/tmp/amphora-report.json'))
lines = [
  "# Pinned Python dependencies not provided by org.gnome.Platform.",
  "name: python3-deps",
  "buildsystem: simple",
  "build-commands:",
  "  - pip3 install --prefix=${FLATPAK_DEST} --no-index --no-build-isolation",
  '    --find-links="file://${PWD}" requests cryptography',
  "sources:",
]
for it in d["install"]:
    di = it["download_info"]
    sha = di["archive_info"]["hashes"]["sha256"]
    lines += [f"  - type: file", f"    url: {di['url']}", f"    sha256: {sha}"]
open("python3-deps.yaml", "w").write("\n".join(lines) + "\n")
print("Wrote python3-deps.yaml")
PY
  exit 0
fi

# Ensure Flathub (for the runtime/SDK) is available to the user.
flatpak remote-add --if-not-exists --user flathub "${FLATHUB}"
flatpak install -y --user flathub \
  "org.gnome.Platform//${RUNTIME_VERSION}" \
  "org.gnome.Sdk//${RUNTIME_VERSION}"

# Keep build + cache dirs OUTSIDE the source tree: the app module uses
# `type: dir path: ..`, which copies the whole repo, so build output kept
# inside would get copied into itself on each run.
if [[ "${1:-}" == "--bundle" ]]; then
  # Export the build into a local OSTree repo, then pack a single .flatpak.
  "${FB[@]}" --user --force-clean "${BUILDER_FLAGS[@]}" \
    --repo="${WORK}/repo" --state-dir "${WORK}/state" \
    "${WORK}/build" net.kndy.Amphora.yaml

  # --runtime-repo embeds Flathub's location so installing the bundle on
  # another machine can fetch org.gnome.Platform//50 automatically.
  flatpak build-bundle "${WORK}/repo" net.kndy.Amphora.flatpak \
    net.kndy.Amphora --runtime-repo="${FLATHUB}"

  echo
  echo "Created: $(pwd)/net.kndy.Amphora.flatpak"
  echo "Copy it to the other machine and install with:"
  echo "    flatpak install --user net.kndy.Amphora.flatpak"
else
  "${FB[@]}" --user --install --force-clean "${BUILDER_FLAGS[@]}" \
    --state-dir "${WORK}/state" \
    "${WORK}/build" net.kndy.Amphora.yaml

  echo
  echo "Done. Launch with:  flatpak run net.kndy.Amphora"
fi
