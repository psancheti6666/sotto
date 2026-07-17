#!/usr/bin/env bash
# Created by Pratik Sancheti / https://github.com/psancheti6666
# Build the Linux onedir bundle → dist/sotto/ (docs/linux-app.md, L3).
# Linux-only; macOS builds live in macapp/. CI (ubuntu-22.04, for the
# glibc 2.35 baseline) is the primary runner; any Linux box with python3.11
# works — headless ones need xvfb-run for the smoke check.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: Linux only — the macOS app builds with macapp/build_app.sh" >&2
  exit 1
fi

PY="${PYTHON:-python3}"
if [[ ! -d .venv ]]; then
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
# build-only dependency; major pinned so hook behavior doesn't drift
.venv/bin/python -m pip install --quiet "pyinstaller>=6.11,<7"

rm -rf build dist/sotto
.venv/bin/pyinstaller --noconfirm --distpath dist --workpath build \
  linuxapp/sotto.spec

# Import every runtime-selected backend inside the frozen app — the safety
# net for lazy imports PyInstaller can't see (no Linux dev hardware exists;
# this MUST fail here, not on a user's first launch).
if [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && command -v xvfb-run >/dev/null; then
  xvfb-run -a dist/sotto/sotto --smoke
else
  dist/sotto/sotto --smoke
fi

echo "OK: dist/sotto/ ($(du -sh dist/sotto | cut -f1))"
