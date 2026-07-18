#!/usr/bin/env bash
# Created by Pratik Sancheti / https://github.com/psancheti6666
# Package the PyInstaller onedir (dist/sotto/) into dist/Sotto-<ver>-amd64.deb
# (docs/linux-app.md, L6). Run after linuxapp/build_app.sh. Linux only.
#
# Layout: the onedir lands in /opt/sotto; /usr/bin/sotto is a launcher that
# sets SOTTO_BUNDLE=deb; the udev rule, polkit policy, root helper, and
# modules-load file (all from linuxapp/deb/, listed in manifest.txt) install
# to their FHS homes so the postinst's udev trigger grants keyboard access at
# install time. Ollama is NOT packaged — it downloads at first run.
set -euo pipefail
cd "$(dirname "$0")/.."
# newly created dirs/files (mkdir, install) get sane modes regardless of the
# build host's umask; cp -a PRESERVES source modes, so the onedir payload gets
# an explicit chmod below (--root-owner-group fixes ownership, not modes)
umask 022

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: Linux only" >&2
  exit 1
fi
[[ -d dist/sotto ]] || { echo "ERROR: dist/sotto missing — run linuxapp/build_app.sh first" >&2; exit 1; }

VERSION=$(sed -nE 's/^__version__ = "([^"]+)"$/\1/p' sotto/__init__.py)
[[ -n $VERSION ]] || { echo "Could not parse __version__" >&2; exit 1; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/pkg"

# the frozen app
mkdir -p "$PKG/opt"
cp -a dist/sotto "$PKG/opt/sotto"
# strip any stray setuid/setgid bits from the onedir: --root-owner-group would
# turn a setuid file into a setuid-ROOT file. Also strip group/world-write —
# cp -a preserved whatever modes the wheels shipped, and a writable root-owned
# file under /opt/sotto would let a local user swap code the seat user runs.
# PyInstaller doesn't emit either, but a dependency wheel theoretically could
# — belt and braces. CI asserts both on the installed tree. (Symlinks are
# exempt on both sides: chmod -R skips them and their own modes are
# meaningless — CI instead checks their TARGETS stay inside the tree.)
chmod -R u-s,g-s,go-w "$PKG/opt/sotto"

# discrete files + exact modes, from the manifest the unit test also reads
while read -r mode src dest; do
  case "$mode" in ''|\#*) continue ;; esac
  install -D -m "$mode" "$src" "$PKG/$dest"
done < linuxapp/deb/manifest.txt

# icons: the logo is a wide wordmark (1170x340), so a plain resize squashes it.
# Crop the waveform mark (same fractions macapp/make_icns.py uses for the mac
# tile) and center it on the logo's paper background → a square tile matching
# the macOS identity. Falls back to the raw PNG only if ImageMagick is absent.
ICON_SRC=logo/sottoLogo.png
PAPER='#F7F7F5'  # the logo's warm off-white (make_icns.py PAPER)
IM="$(command -v magick || command -v convert || true)"  # IM7 vs IM6
if [[ -n "$IM" ]] && command -v identify >/dev/null 2>&1; then
  # command substitution (not `read < <(identify …)` — identify prints no
  # trailing newline, so read returns non-zero and set -e would kill us)
  DIMS="$(identify -format '%w %h' "$ICON_SRC")"
  LW="${DIMS%% *}"; LH="${DIMS##* }"
  MX=$(( LW * 2 / 100 )); MY=$(( LH * 5 / 100 ))
  MW=$(( LW * 28 / 100 )); MH=$(( LH * 90 / 100 ))
  SQ=$(( MH * 130 / 100 ))  # square canvas with ~30% padding around the mark
  for sz in 48 64 128 256 512; do
    d="$PKG/usr/share/icons/hicolor/${sz}x${sz}/apps"
    mkdir -p "$d"
    "$IM" "$ICON_SRC" -crop "${MW}x${MH}+${MX}+${MY}" +repage \
      -background "$PAPER" -gravity center -extent "${SQ}x${SQ}" \
      -resize "${sz}x${sz}" "$d/sotto.png"
  done
else
  install -D -m 644 "$ICON_SRC" \
    "$PKG/usr/share/icons/hicolor/256x256/apps/sotto.png"
fi

# control + maintainer scripts
mkdir -p "$PKG/DEBIAN"
sed "s/@VERSION@/$VERSION/" linuxapp/deb/control.in > "$PKG/DEBIAN/control"
for s in postinst prerm postrm; do
  install -m 755 "linuxapp/deb/$s" "$PKG/DEBIAN/$s"
done

DEB="dist/Sotto-$VERSION-amd64.deb"
rm -f "$DEB"
# --root-owner-group: every file becomes root:root regardless of the build
# user; the helper's 0755 (from the manifest) + root ownership is what keeps
# the pinned polkit action safe.
dpkg-deb --root-owner-group --build "$PKG" "$DEB"

command -v lintian >/dev/null 2>&1 && lintian "$DEB" || true  # advisory only

echo "OK: $DEB ($(du -h "$DEB" | cut -f1))"
