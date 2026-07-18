#!/usr/bin/env bash
# Created by Pratik Sancheti / https://github.com/psancheti6666
# Package the PyInstaller onedir (dist/sotto/) into
# dist/Sotto-<ver>-x86_64.AppImage (docs/linux-app.md, L9). Run after
# linuxapp/build_app.sh. Linux only.
#
# Assembly is manual (mksquashfs + the VENDORED static runtime, hash-pinned
# — see linuxapp/appimage/PROVENANCE.md) rather than appimagetool: zero
# unpinned build-time downloads can reach a signed release artifact. The
# static runtime needs no libfuse2 (CI's bare-container smoke proves it).
# Payload: AppRun (exports SOTTO_BUNDLE=appimage), the onedir at opt/sotto,
# and setup/ carrying the BYTE-IDENTICAL deb payload files + bootstrap for
# the one-time generic-pkexec permission install (test-pinned against
# deb/manifest.txt so the two artifacts can't drift).
set -euo pipefail
cd "$(dirname "$0")/.."
umask 022

RUNTIME=linuxapp/appimage/runtime-x86_64
RUNTIME_SHA256=1cc49bcf1e2ccd593c379adb17c9f85a36d619088296504de95b1d06215aebbf

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: Linux only" >&2
  exit 1
fi
[[ -d dist/sotto ]] || { echo "ERROR: dist/sotto missing — run linuxapp/build_app.sh first" >&2; exit 1; }
command -v mksquashfs >/dev/null || { echo "ERROR: mksquashfs missing (apt install squashfs-tools)" >&2; exit 1; }
echo "$RUNTIME_SHA256  $RUNTIME" | sha256sum -c - >/dev/null \
  || { echo "ERROR: vendored runtime hash mismatch — see PROVENANCE.md" >&2; exit 1; }

VERSION=$(sed -nE 's/^__version__ = "([^"]+)"$/\1/p' sotto/__init__.py)
[[ -n $VERSION ]] || { echo "Could not parse __version__" >&2; exit 1; }
[[ $VERSION != *:* ]] || { echo "ERROR: epoch in version '$VERSION'" >&2; exit 1; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
APPDIR="$STAGE/AppDir"

mkdir -p "$APPDIR/opt"
cp -a dist/sotto "$APPDIR/opt/sotto"
chmod -R u-s,g-s,go-w "$APPDIR/opt/sotto"  # same strip as make_deb.sh

install -m 755 linuxapp/appimage/AppRun "$APPDIR/AppRun"
install -m 755 linuxapp/appimage/bootstrap "$APPDIR/bootstrap"

# byte-identical deb payload for the bootstrap (+ the updater pubkey for
# AppImage self-replace verification — read at APPDIR/setup/ by update_linux)
mkdir -p "$APPDIR/setup"
for f in 60-sotto-input.rules sotto-uinput.conf \
         io.github.psancheti6666.sotto.policy sotto-perms sotto-release.pub; do
  install -m 644 "linuxapp/deb/$f" "$APPDIR/setup/$f"
done

# desktop file + icon (AppImage spec: both at AppDir root). Icon: crop the
# waveform mark exactly as make_deb.sh does; raw-logo fallback without IM.
sed 's|^Exec=.*|Exec=sotto|' linuxapp/deb/sotto.desktop > "$APPDIR/sotto.desktop"
ICON_SRC=logo/sottoLogo.png
PAPER='#F7F7F5'
IM="$(command -v magick || command -v convert || true)"
if [[ -n "$IM" ]] && command -v identify >/dev/null 2>&1; then
  DIMS="$(identify -format '%w %h' "$ICON_SRC")"
  LW="${DIMS%% *}"; LH="${DIMS##* }"
  MX=$(( LW * 2 / 100 )); MY=$(( LH * 5 / 100 ))
  MW=$(( LW * 28 / 100 )); MH=$(( LH * 90 / 100 ))
  SQ=$(( MH * 130 / 100 ))
  "$IM" "$ICON_SRC" -crop "${MW}x${MH}+${MX}+${MY}" +repage \
    -background "$PAPER" -gravity center -extent "${SQ}x${SQ}" \
    -resize 256x256 "$APPDIR/sotto.png"
else
  install -m 644 "$ICON_SRC" "$APPDIR/sotto.png"
fi
ln -sf sotto.png "$APPDIR/.DirIcon"

# squashfs (root-owned entries, zstd) appended to the runtime = the AppImage
SQ="$STAGE/app.squashfs"
mksquashfs "$APPDIR" "$SQ" -root-owned -noappend -comp zstd -quiet
OUT="dist/Sotto-$VERSION-x86_64.AppImage"
rm -f "$OUT"
cat "$RUNTIME" "$SQ" > "$OUT"
chmod 755 "$OUT"

echo "OK: $OUT ($(du -h "$OUT" | cut -f1))"
