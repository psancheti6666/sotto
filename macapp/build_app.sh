#!/bin/bash
# Created by Pratik Sancheti / https://github.com/psancheti6666
# Build the unsigned Sotto app for the machine's architecture:
# arm64 → MLX ASR backend, x86_64 → ONNX (setup_app.py swaps the stacks).
#
# Two variants (separate apps: different bundle ids, names, icons — they
# coexist and hold permissions independently):
#   ./macapp/build_app.sh                  → "dist/Sotto Dev.app" (DEV-badged
#                                            icon; the default for development
#                                            so it never fights an installed
#                                            release Sotto)
#   SOTTO_RELEASE=1 ./macapp/build_app.sh  → dist/Sotto.app (the real thing;
#                                            what CI ships in DMGs)
set -euo pipefail
cd "$(dirname "$0")/.."

# py2app names the bundle after CFBundleName, so the dev app is
# "dist/Sotto Dev.app" (space and all — keep paths quoted)
if [[ "${SOTTO_RELEASE:-0}" == "1" ]]; then
  APP=Sotto; ICNS_FLAGS=""
else
  APP="Sotto Dev"; ICNS_FLAGS="--dev"
fi

PY=.venv/bin/python
[[ -x $PY ]] || { echo "No .venv — run ./setup.sh first (the build reuses the project venv)."; exit 1; }
[[ "$(uname -s)" == "Darwin" ]] || { echo "macOS only."; exit 1; }
ARCH=$(uname -m)
[[ "$ARCH" == "arm64" || "$ARCH" == "x86_64" ]] || { echo "Unsupported architecture: $ARCH"; exit 1; }

# Build-only dependency, pinned. Never goes in requirements.txt (CI stays lean).
# Always `python -m pip`: the venv's pip script has a stale shebang from a
# pre-rename path and fails when invoked directly.
"$PY" -m pip install --quiet "py2app==0.28.8" "setuptools<81"  # py2app 0.28 needs pkg_resources (removed in setuptools 81)

# The mlx wheel ships no top-level __init__.py (namespace package), which
# py2app's `packages` collector can't locate. An empty regular-package marker
# is harmless at runtime and makes the verbatim copy work. Idempotent.
# arm64 only — the Intel build has no mlx installed (ONNX backend instead).
if [[ "$ARCH" == "arm64" ]]; then
  MLX_INIT="$("$PY" -c 'import mlx.core, os; print(os.path.join(os.path.dirname(os.path.dirname(mlx.core.__file__)), "mlx", "__init__.py"))')"
  [[ -f "$MLX_INIT" ]] || touch "$MLX_INIT"
fi

# Stale py2app build dirs produce subtly broken bundles — always start clean.
rm -rf build dist
mkdir -p build

# $ICNS_FLAGS deliberately unquoted: empty → no extra argument
"$PY" macapp/make_icns.py logo/sottoLogo.png build/Sotto.icns $ICNS_FLAGS

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

OLDIR="dist/$APP.app/Contents/Resources/ollama"
mkdir -p "$OLDIR"
tar -xzf "$TGZ" -C "$OLDIR"
# Prune the universal tarball to what serving a GGUF model on THIS arch uses
# (verified on arm64: Metal is embedded in libggml — all layers GPU-offload
# without these; the same thinning applies symmetrically on x86_64):
#  - mlx_metal_*: ollama's optional MLX engine, 348 MB, Apple-Silicon-only
#    and unused by the GGUF path — dropped on both arches
#  - anything without a slice for the build arch (the other arch's CPU
#    variants and dylibs; fat binaries are thinned to the matching half)
rm -rf "$OLDIR"/mlx_metal_*
find "$OLDIR" -type f | while read -r f; do
  archs=$(lipo -archs "$f" 2>/dev/null) || continue   # not Mach-O (LICENSE etc.)
  case "$archs" in
    "$ARCH")   ;;                                      # already thin
    *"$ARCH"*) lipo -thin "$ARCH" "$f" -output "$f.thin" && mv "$f.thin" "$f" ;;
    *)         rm "$f" ;;
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

# arm64 refuses to run unsigned binaries, so the bundle must be sealed.
# Preferred: a local self-signed "Sotto Dev" certificate (see docs/macos-app.md)
# — it gives the app a STABLE identity, so Accessibility/Input Monitoring
# grants survive rebuilds. Fallback: ad-hoc, where every rebuild changes the
# signature hash and macOS silently invalidates those grants. Neither is
# Developer ID signing/notarization — users still go through Privacy &
# Security → "Open Anyway" on first launch.
SIGN_ID="${SOTTO_SIGN_IDENTITY:-Sotto Dev}"
if security find-identity -p codesigning -v 2>/dev/null | grep -q "\"$SIGN_ID\""; then
  echo "signing with local identity: $SIGN_ID (permission grants survive rebuilds)"
  codesign --force --deep --sign "$SIGN_ID" "dist/$APP.app"
  # Belt and braces: assert the seal really carries the cert. The v0.3.0
  # release shipped ad-hoc because a silent fallback hid a detection failure
  # (issue #23) — never let a signing surprise pass quietly again.
  # Plain grep, not -q: -q exits on first match, which can SIGPIPE codesign
  # mid-output and pipefail then fails the pipeline despite a correct
  # signature (issue #26 — a race this check lost once locally).
  codesign -dvvv "dist/$APP.app" 2>&1 | grep "Authority=$SIGN_ID" >/dev/null || {
    echo "ERROR: dist/$APP.app is not sealed by '$SIGN_ID' after signing" >&2
    exit 1
  }
elif [[ "${SOTTO_RELEASE:-0}" == "1" ]]; then
  # Releases MUST carry the stable cert: an ad-hoc release has a per-build
  # designated requirement, so every update wipes users' Accessibility and
  # Input Monitoring grants (shipped that way once in v0.3.0 — issue #23).
  echo "ERROR: release build but no valid '$SIGN_ID' identity — refusing to" >&2
  echo "ad-hoc sign a release. Import + trust the cert (see release.yml) or" >&2
  echo "set SOTTO_SIGN_IDENTITY." >&2
  exit 1
else
  echo "no '$SIGN_ID' certificate — ad-hoc signing (permission grants will reset on every rebuild)"
  codesign --force --deep --sign - "dist/$APP.app"
fi

echo "OK: dist/$APP.app ($(du -sh "dist/$APP.app" | cut -f1))"
