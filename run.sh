#!/bin/bash
# Created by Pratik Sancheti / https://github.com/psancheti6666
# Start Sotto. Run ./setup.sh once first.
cd "$(dirname "$0")"
[[ -x .venv/bin/python ]] || { echo "Not set up yet — run ./setup.sh first."; exit 1; }

# Auto-update: fast-forward to the latest Sotto before starting. Must never
# block or break startup — offline, diverged, or locally-modified checkouts
# (and ZIP downloads, which have no .git) are all silently fine.
# Skip with: SOTTO_NO_UPDATE=1 ./run.sh
if [[ -z "${SOTTO_NO_UPDATE:-}" ]] && command -v git >/dev/null \
   && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  before="$(git rev-parse HEAD 2>/dev/null || true)"
  GIT_TERMINAL_PROMPT=0 git pull --ff-only --quiet 2>/dev/null || true
  after="$(git rev-parse HEAD 2>/dev/null || true)"
  if [[ -n "$before" && -n "$after" && "$before" != "$after" ]]; then
    echo "Updated Sotto to the latest version ($(git rev-list --count "$before..$after") new change(s))."
    # New code may need new packages — keep the venv in sync with the pull.
    if ! git diff --quiet "$before" "$after" -- requirements.txt; then
      echo "Dependencies changed — updating (may take a minute)…"
      .venv/bin/python -m pip install --quiet -r requirements.txt
    fi
  fi
fi

exec .venv/bin/python -m sotto
