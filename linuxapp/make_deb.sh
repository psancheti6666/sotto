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

# discrete files + exact modes, from the manifest the unit test also reads
while read -r mode src dest; do
  case "$mode" in ''|\#*) continue ;; esac
  install -D -m "$mode" "$src" "$PKG/$dest"
done < linuxapp/deb/manifest.txt

# icons: render sizes from the logo when ImageMagick is present, else ship the
# source PNG at one size so the app still has a tile
ICON_SRC=logo/sottoLogo.png
if command -v convert >/dev/null 2>&1; then
  for sz in 48 64 128 256 512; do
    d="$PKG/usr/share/icons/hicolor/${sz}x${sz}/apps"
    mkdir -p "$d"
    convert "$ICON_SRC" -resize "${sz}x${sz}" "$d/sotto.png"
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
