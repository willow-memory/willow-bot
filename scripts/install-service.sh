#!/usr/bin/env bash
# install-service.sh — render systemd/<unit>.service.template and install the
# unit for the current user. Prints the resolved values before writing.
#
# Units (systemd/*.service.template):
#   willow-bot          — the GitHub App webhook receiver (default)
#   willow-bot-steward  — the tick loop: PR watch, curated heartbeat, gitsync
#                         sweep through willow-mcp (WILLOW_BOT_MCP=1)
#
# Env inputs (with defaults):
#   WILLOW_BOT_VENV_BIN   — dir holding the willow-bot entry-point scripts
#                           (default: $VAULT_BOX/venvs/willow-bot/bin)
#   WILLOW_BOT_VAULT_BOX  — WILLOW_HOME / vault box root
#                           (default: $HOME/sean-data-vault/willow-operator-box)
#   WILLOW_BOT_WORKDIR    — repo checkout the service cd's into
#                           (default: $HOME/github/willow-memory/willow-bot)
#
# Usage:
#   scripts/install-service.sh [unit]            # render + install (default: willow-bot)
#   scripts/install-service.sh --print [unit]    # print rendered template to stdout only
#   scripts/install-service.sh --all             # render + install every template
set -euo pipefail

VAULT_BOX="${WILLOW_BOT_VAULT_BOX:-$HOME/sean-data-vault/willow-operator-box}"
VENV_BIN="${WILLOW_BOT_VENV_BIN:-$VAULT_BOX/venvs/willow-bot/bin}"
WORKDIR="${WILLOW_BOT_WORKDIR:-$HOME/github/willow-memory/willow-bot}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PRINT=0
if [[ "${1:-}" == "--print" ]]; then
    PRINT=1
    shift
fi

if [[ "${1:-}" == "--all" ]]; then
    UNITS=()
    for t in "$REPO_ROOT"/systemd/*.service.template; do
        UNITS+=("$(basename "$t" .service.template)")
    done
else
    UNITS=("${1:-willow-bot}")
fi

render() {
    local template="$REPO_ROOT/systemd/$1.service.template"
    if [[ ! -f "$template" ]]; then
        echo "install-service.sh: template not found: $template" >&2
        exit 1
    fi
    sed \
        -e "s#@VENV_BIN@#$VENV_BIN#g" \
        -e "s#@VAULT_BOX@#$VAULT_BOX#g" \
        -e "s#@WORKDIR@#$WORKDIR#g" \
        "$template"
}

if [[ "$PRINT" == 1 ]]; then
    for unit in "${UNITS[@]}"; do
        render "$unit"
    done
    exit 0
fi

TARGET_DIR="$HOME/.config/systemd/user"
mkdir -p "$TARGET_DIR"

echo "install-service.sh: resolved values:"
echo "  VENV_BIN  = $VENV_BIN"
echo "  VAULT_BOX = $VAULT_BOX"
echo "  WORKDIR   = $WORKDIR"

for unit in "${UNITS[@]}"; do
    target="$TARGET_DIR/$unit.service"
    render "$unit" > "$target"
    echo "  wrote     = $target"
done

systemctl --user daemon-reload
echo "install-service.sh: ran daemon-reload."
for unit in "${UNITS[@]}"; do
    echo "install-service.sh: next step: systemctl --user enable --now $unit.service"
done
