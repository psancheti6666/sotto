#!/usr/bin/env bash
# Created by Pratik Sancheti / https://github.com/psancheti6666
# Run EVERY check CI runs that can run on this machine, before pushing —
# so a red build is caught here in seconds, not on the PR. Mirrors the
# gates in .github/workflows/{tests,release}.yml.
#
# What this canNOT cover (no Linux hardware here): the PyInstaller Linux
# build + the bare-container smoke — those are Linux-only. Catch those with
# a trial `gh workflow run release.yml --ref <branch>` BEFORE opening the PR,
# not on the PR itself.
set -euo pipefail
cd "$(dirname "$0")/.."
fail=0
step() { printf '\n== %s ==\n' "$1"; }

step "unit tests (tests.yml)"
.venv/bin/python tests/test_pipeline.py || fail=1

step "shellcheck — root-executed + build shell (tests.yml gate)"
# GATING: exactly the files the CI gate checks — fail only where CI fails
shellcheck linuxapp/deb/sotto-perms linuxapp/build_app.sh || fail=1
# INFO ONLY: other tracked shell isn't a CI gate today; surfaced, not blocking
extra=$(git ls-files '*.sh' | grep -vE '^(linuxapp/deb/sotto-perms|linuxapp/build_app.sh)$' || true)
[ -n "$extra" ] && { echo "-- other shell (informational, not a CI gate):";
                     shellcheck $extra || echo "  (pre-existing warnings above — not blocking)"; }

step "python parse — PyInstaller spec + entry"
.venv/bin/python - <<'PY' || fail=1
import ast
for f in ("linuxapp/sotto.spec", "linuxapp/sotto_linux.py"):
    ast.parse(open(f).read())
print("spec + entry parse OK")
PY

step "well-formedness — polkit XML + workflow YAML"
.venv/bin/python - <<'PY' || fail=1
import glob, yaml
from xml.parsers.expat import ParserCreate
# expat checks well-formedness without fetching the external DTD or expanding
# entities — no XXE surface, and no defusedxml dependency needed
for f in glob.glob("linuxapp/deb/*.policy"):
    p = ParserCreate()
    p.Parse(open(f, "rb").read(), True)
for f in glob.glob(".github/workflows/*.yml"):
    yaml.safe_load(open(f))
print("XML + YAML OK")
PY

if [ "$fail" -ne 0 ]; then
  printf '\nPREFLIGHT FAILED — fix the above before pushing.\n' >&2
  exit 1
fi
printf '\nALL PREFLIGHT CHECKS PASSED (Linux build still verified by CI/dispatch)\n'
