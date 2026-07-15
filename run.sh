#!/bin/bash
# Start Sotto. Run ./setup.sh once first.
cd "$(dirname "$0")"
[[ -x .venv/bin/python ]] || { echo "Not set up yet — run ./setup.sh first."; exit 1; }
exec .venv/bin/python -m sotto
