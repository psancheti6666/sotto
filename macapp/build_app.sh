#!/bin/bash
# Created by Pratik Sancheti / https://github.com/psancheti6666
# Build the unsigned menu-bar Sotto.app (Apple Silicon).
# Usage: ./macapp/build_app.sh   →   dist/Sotto.app
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
[[ -x $PY ]] || { echo "No .venv — run ./setup.sh first (the build reuses the project venv)."; exit 1; }
[[ "$(uname -sm)" == "Darwin arm64" ]] || { echo "Apple Silicon macOS only for now (Intel build lands with the DMG milestone)."; exit 1; }

# Build-only dependency, pinned. Never goes in requirements.txt (CI stays lean).
# Always `python -m pip`: the venv's pip script has a stale shebang from a
# pre-rename path and fails when invoked directly.
"$PY" -m pip install --quiet "py2app==0.28.8" "setuptools<81"  # py2app 0.28 needs pkg_resources (removed in setuptools 81)

# The mlx wheel ships no top-level __init__.py (namespace package), which
# py2app's `packages` collector can't locate. An empty regular-package marker
# is harmless at runtime and makes the verbatim copy work. Idempotent.
MLX_INIT="$("$PY" -c 'import mlx.core, os; print(os.path.join(os.path.dirname(os.path.dirname(mlx.core.__file__)), "mlx", "__init__.py"))')"
[[ -f "$MLX_INIT" ]] || touch "$MLX_INIT"

# Stale py2app build dirs produce subtly broken bundles — always start clean.
rm -rf build dist
mkdir -p build

"$PY" macapp/make_icns.py logo/sottoLogo.png build/Sotto.icns

"$PY" macapp/setup_app.py py2app --dist-dir dist --bdist-base build

# ---- bundled LLM runtime -----------------------------------------------
# Pinned ollama release (MIT), verified against the sha256 it publishes.
# The tarball is cached in macapp/.cache/ so rebuilds don't redownload.
OLLAMA_VERSION="v0.32.1"
OLLAMA_SHA256="346d28fe70f3ef3776e42100f5721510aa35fc07f3733f6629dbb117b1cfede9"
CACHE=macapp/.cache
TGZ="$CACHE/ollama-darwin-$OLLAMA_VERSION.tgz"
mkdir -p "$CACHE"
if [[ ! -f $TGZ ]]; then
  curl -fL --progress-bar -o "$TGZ" \
    "https://github.com/ollama/ollama/releases/download/$OLLAMA_VERSION/ollama-darwin.tgz"
fi
echo "$OLLAMA_SHA256  $TGZ" | shasum -a 256 -c - >/dev/null

OLDIR=dist/Sotto.app/Contents/Resources/ollama
mkdir -p "$OLDIR"
tar -xzf "$TGZ" -C "$OLDIR"
# Prune to what serving a GGUF model on Apple Silicon actually uses (verified:
# Metal is embedded in libggml — all layers GPU-offload without these):
#  - mlx_metal_*: ollama's optional MLX engine, 348 MB
#  - anything without an arm64 slice (Intel CPU variants, x86-only dylibs;
#    fat binaries are thinned to their arm64 half)
rm -rf "$OLDIR"/mlx_metal_*
find "$OLDIR" -type f | while read -r f; do
  archs=$(lipo -archs "$f" 2>/dev/null) || continue   # not Mach-O (LICENSE etc.)
  case "$archs" in
    arm64)    ;;                                       # already thin arm64
    *arm64*)  lipo -thin arm64 "$f" -output "$f.thin" && mv "$f.thin" "$f" ;;
    *)        rm "$f" ;;
  esac
done
find "$OLDIR" -type l ! -exec test -e {} \; -delete    # now-dangling symlinks
if [[ ! -f "$CACHE/ollama-LICENSE-$OLLAMA_VERSION" ]]; then
  curl -fsL -o "$CACHE/ollama-LICENSE-$OLLAMA_VERSION" \
    "https://raw.githubusercontent.com/ollama/ollama/$OLLAMA_VERSION/LICENSE"
fi
cp "$CACHE/ollama-LICENSE-$OLLAMA_VERSION" "$OLDIR/LICENSE"
echo "bundled ollama $OLLAMA_VERSION ($(du -sh "$OLDIR" | cut -f1))"
# -------------------------------------------------------------------------

# arm64 refuses to run unsigned binaries, so re-seal the finished bundle with
# an ad-hoc signature. This is NOT Developer ID signing or notarization —
# users still go through Privacy & Security → "Open Anyway" on first launch.
codesign --force --deep --sign - dist/Sotto.app

echo "OK: dist/Sotto.app ($(du -sh dist/Sotto.app | cut -f1))"
