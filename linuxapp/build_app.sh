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
# a stale .venv from another interpreter would build a bundle that silently
# differs from CI's — refuse instead
if ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))'; then
  echo "ERROR: .venv is not Python 3.11 ($(.venv/bin/python -V 2>&1))" >&2
  echo "       delete .venv or rerun with PYTHON=python3.11" >&2
  exit 1
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
# build-only dependency; major pinned so hook behavior doesn't drift
.venv/bin/python -m pip install --quiet "pyinstaller>=6.11,<7"
# PyGObject feeds pystray's appindicator backend — the only tray protocol
# GNOME renders (docs/linux-app.md, L7). Best-effort: it compiles against
# system gir headers (libgirepository1.0-dev, libcairo2-dev; CI installs
# them), and a build without it still works — the tray just falls back to
# pystray's xorg backend or the tray-less log line. <3.51 pin: newer
# PyGObject needs girepository-2.0 (glib ≥ 2.80), beyond the ubuntu-22.04
# glibc-baseline builder.
if ! .venv/bin/python -m pip install --quiet "PyGObject<3.51"; then
  echo "WARNING: PyGObject install failed — tray will lack the appindicator" >&2
  echo "         backend (invisible on stock GNOME); xorg fallback only." >&2
fi

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

# informational: did the appindicator stack make it into the bundle?
# (tray is best-effort — this is for reading CI logs, not a gate)
if [[ -d dist/sotto/_internal/gi ]]; then
  echo "tray: gi bundled — appindicator backend available"
else
  echo "tray: gi NOT bundled — pystray xorg fallback only"
fi

echo "OK: dist/sotto/ ($(du -sh dist/sotto | cut -f1))"
