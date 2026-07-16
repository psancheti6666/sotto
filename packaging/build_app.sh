#!/bin/bash
# Created by Pratik Sancheti / https://github.com/psancheti6666
# Build the unsigned menu-bar Sotto.app (Apple Silicon).
# Usage: ./packaging/build_app.sh   →   dist/Sotto.app
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

"$PY" packaging/make_icns.py logo/sottoLogo.png build/Sotto.icns

"$PY" packaging/setup_app.py py2app --dist-dir dist --bdist-base build

# arm64 refuses to run unsigned binaries, so re-seal the finished bundle with
# an ad-hoc signature. This is NOT Developer ID signing or notarization —
# users still go through Privacy & Security → "Open Anyway" on first launch.
codesign --force --deep --sign - dist/Sotto.app

echo "OK: dist/Sotto.app ($(du -sh dist/Sotto.app | cut -f1))"
