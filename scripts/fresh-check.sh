#!/usr/bin/env bash
# fresh-check.sh — build a CI-shaped venv from scratch and run the test suite.
#
# Guards against dev-dep drift the way CI would catch it, but locally, before
# a push. The httpx miss on PR #12 (starlette.testclient needs httpx, which
# was not in `[project.optional-dependencies].dev`) landed green in a
# developer venv where httpx had been installed by hand and red in CI where
# `pip install -e .[dev]` on a fresh matrix leg was the whole install. This
# script reproduces the CI shape:
#
#   1. tempdir venv (never touches ~/.venv or an existing .venv)
#   2. `pip install -e ".[dev]"` — nothing else, no manual pip installs
#   3. `python -m pytest tests/ -q`
#   4. clean up the venv on exit
#
# Run it before pushing a branch that changes pyproject.toml or adds a new
# testing dependency, and after any bump to fastapi / starlette / pytest.

set -euo pipefail

# Repo root (the script sits in ./scripts/).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$(mktemp -d -t willow-bot-fresh-XXXXXX)/venv"
trap 'rm -rf -- "${VENV%/venv}"' EXIT

echo "[fresh-check] repo:  $ROOT"
echo "[fresh-check] venv:  $VENV (tempdir; cleaned on exit)"

python3 -m venv "$VENV"

# Quiet pip, but keep errors visible.
"$VENV/bin/pip" install --disable-pip-version-check --quiet --upgrade pip

echo "[fresh-check] installing willow-bot editable + dev extras"
"$VENV/bin/pip" install --disable-pip-version-check --quiet -e "${ROOT}[dev]"

echo "[fresh-check] running pytest"
"$VENV/bin/python" -m pytest "${ROOT}/tests" -q

echo "[fresh-check] OK"
