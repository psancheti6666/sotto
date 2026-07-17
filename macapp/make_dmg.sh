#!/bin/bash
# Created by Pratik Sancheti / https://github.com/psancheti6666
# Package an already-built dist/Sotto.app into a distributable disk image:
# Sotto-<version>-<arch>.dmg containing the app, an /Applications symlink,
# and a README.txt that walks unsigned-app users through "Open Anyway".
# Usage: ./macapp/build_app.sh && ./macapp/make_dmg.sh   →   dist/Sotto-*.dmg
set -euo pipefail
cd "$(dirname "$0")/.."

APP=dist/Sotto.app
if [[ ! -d $APP ]]; then
  if [[ -d "dist/Sotto Dev.app" ]]; then
    echo "dist/ holds the Sotto Dev build — DMGs ship the release app:"
    echo "  SOTTO_RELEASE=1 ./macapp/build_app.sh && ./macapp/make_dmg.sh"
  else
    echo "No $APP — run: SOTTO_RELEASE=1 ./macapp/build_app.sh first."
  fi
  exit 1
fi

VERSION=$(sed -nE 's/^__version__ = "([^"]+)"$/\1/p' sotto/__init__.py)
[[ -n $VERSION ]] || { echo "Could not parse __version__ from sotto/__init__.py"; exit 1; }
case "$(uname -m)" in
  arm64)  ARCH=apple-silicon ;;
  x86_64) ARCH=intel ;;
  *)      echo "Unsupported architecture: $(uname -m)"; exit 1 ;;
esac
DMG="dist/Sotto-$VERSION-$ARCH.dmg"

# Stage the volume contents. ditto (not cp) preserves the bundle exactly —
# symlinks, extended attributes, and the code signature.
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/Sotto.app"
ln -s /Applications "$STAGE/Applications"
cp macapp/dmg-README.txt "$STAGE/README.txt"  # creator line doubles as attribution

rm -f "$DMG"
hdiutil create -volname "Sotto $VERSION" -srcfolder "$STAGE" \
  -fs HFS+ -format UDZO -quiet "$DMG"

echo "OK: $DMG ($(du -h "$DMG" | cut -f1))"
