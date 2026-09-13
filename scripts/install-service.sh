#!/usr/bin/env bash
# install-service.sh — render systemd/willow-bot.service.template and install
# the unit for the current user. Prints the resolved values before writing.
#
# Env inputs (with defaults):
#   WILLOW_BOT_VENV_BIN   — dir holding the willow-bot entry-point script
#                           (default: $VAULT_BOX/venvs/willow-bot/bin)
#   WILLOW_BOT_VAULT_BOX  — WILLOW_HOME / vault box root
#                           (default: $HOME/sean-data-vault/willow-operator-box)
#   WILLOW_BOT_WORKDIR    — repo checkout the service cd's into
#                           (default: $HOME/github/willow-memory/willow-bot)
#
# Usage:
#   scripts/install-service.sh          # render + install
#   scripts/install-service.sh --print  # print rendered template to stdout only
set -euo pipefail

VAULT_BOX="${WILLOW_BOT_VAULT_BOX:-$HOME/sean-data-vault/willow-operator-box}"
VENV_BIN="${WILLOW_BOT_VENV_BIN:-$VAULT_BOX/venvs/willow-bot/bin}"
WORKDIR="${WILLOW_BOT_WORKDIR:-$HOME/github/willow-memory/willow-bot}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$REPO_ROOT/systemd/willow-bot.service.template"
if [[ ! -f "$TEMPLATE" ]]; then
    echo "install-service.sh: template not found: $TEMPLATE" >&2
    exit 1
fi

RENDERED="$(sed \
    -e "s#@VENV_BIN@#$VENV_BIN#g" \
    -e "s#@VAULT_BOX@#$VAULT_BOX#g" \
    -e "s#@WORKDIR@#$WORKDIR#g" \
    "$TEMPLATE")"

if [[ "${1:-}" == "--print" ]]; then
    echo "$RENDERED"
    exit 0
fi

TARGET_DIR="$HOME/.config/systemd/user"
TARGET="$TARGET_DIR/willow-bot.service"
mkdir -p "$TARGET_DIR"

echo "install-service.sh: resolved values:"
echo "  VENV_BIN  = $VENV_BIN"
echo "  VAULT_BOX = $VAULT_BOX"
echo "  WORKDIR   = $WORKDIR"
echo "  target    = $TARGET"

echo "$RENDERED" > "$TARGET"
systemctl --user daemon-reload
echo "install-service.sh: wrote $TARGET and ran daemon-reload."
echo "install-service.sh: next step: systemctl --user enable --now willow-bot.service"
