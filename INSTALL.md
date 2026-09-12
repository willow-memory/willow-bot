# willow-bot install notes

`willow-bot` is a local FastAPI webhook receiver for a GitHub App, plus a
deterministic **steward** (`willow-bot-steward`) that feeds the orchestrator
desk (PR watch / inbox / optional host sync). Commitment **dew** stays on the
Kart worker heartbeat until proven on the bot — do not wire dew into steward yet.

## Package install

```bash
cd ~/github/workshop/willow-bot   # or future willow-memory/willow-bot
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
# optional Grove Loki semantic path:
# .venv/bin/pip install -e '.[loki]'
```

Console scripts: `willow-bot` (webhook), `willow-bot-steward` (`tick` | `loop` | `inbox` | `scan`).

Dogfood venv (preferred for systemd): `$WILLOW_HOME/venvs/willow-bot` — keep separate from `venvs/willow-mcp`.

## Local service

```bash
cp .env.example .env
```

Fill `.env` with:

- `GITHUB_APP_ID` from the GitHub App settings
- `GITHUB_APP_PRIVATE_KEY_PATH`, usually `~/.willow/secrets/willow-bot.pem`
- `GITHUB_WEBHOOK_SECRET`, matching the GitHub App webhook secret
- `WEBHOOK_PUBLIC_URL`, the public Pangolin URL that reaches this bot

Run locally:

```bash
set -a
. ./.env
set +a
.venv/bin/uvicorn bot:app --host "${BOT_HOST:-127.0.0.1}" --port "${BOT_PORT:-9000}"
# or: .venv/bin/willow-bot
```

Steward one-shot / loop (state under `$WILLOW_HOME` by default):

```bash
export WILLOW_HOME="${WILLOW_HOME:-$HOME/sean-data-vault/willow-operator-box}"
.venv/bin/willow-bot-steward tick
.venv/bin/willow-bot-steward heartbeat   # curated mcp tools when WILLOW_BOT_MCP=1
.venv/bin/willow-bot-steward loop        # tick + heartbeat + AGENT_LOOP_TICK_PR_AUDIT
```

Prove-phase MCP (optional):

```bash
.venv/bin/pip install -e '.[mcp]'
export WILLOW_BOT_MCP=1
export WILLOW_BOT_MCP_APP_ID=willow   # narrow steward manifest later
export WILLOW_BOT_MCP_INHERIT_ENV=1
export WILLOW_BOT_MCP_COMMAND="$WILLOW_HOME/venvs/willow-mcp/bin/python -m willow_mcp"
.venv/bin/willow-bot-steward heartbeat
```

CI draft deposits land under `$WILLOW_HOME/willow-bot/deposits/ci_outcomes.jsonl`
(and `store_put` collection `willow_bot_ci_deposits` when MCP is on). Dew stays on Kart.

Legacy env names `LOKI_PR_WATCH_*` still work during prove; prefer `WILLOW_BOT_STEWARD_*`.

## Pangolin route

Create a Pangolin resource for the public bot hostname and forward it to:

```text
http://127.0.0.1:9000
```

In GitHub App settings, set the webhook URL to:

```text
${WEBHOOK_PUBLIC_URL}/webhook
```

Use the same `GITHUB_WEBHOOK_SECRET` in the GitHub App and local `.env`.

## GitHub App permissions

Minimum planned permissions:

- Metadata: read
- Issues: write
- Pull requests: write
- Checks: read
- Contents: read

Webhook events:

- `pull_request`
- `push`
- `check_run`
- `create`
- `issues`
- `issue_comment` (upstream desk inbox)
- `installation` / `installation_repositories` (catalog refresh when repos change)

## Fleet bridge (local integration)

Verified webhooks fan out into local queues under `$WILLOW_HOME`:

| Event | Local action |
|-------|----------------|
| `pull_request`, `issues`, `issue_comment`, `check_run` | `upstream_steward/webhook_inbox/*.json` |
| `push` to `main`/`master` (local clone exists) | `gitsync/trigger-<owner>-<repo>.flag` |
| all events | `willow-bot/event-log.jsonl` audit trail |

Map App installations to local clones:

```bash
set -a && . ./.env && set +a
python scripts/repo_map.py
python scripts/list_installations.py
```

Sync hook secret after rotation:

```bash
python scripts/sync_webhook_secret.py
systemctl --user restart willow-bot
```

## Preflight

```bash
set -a
. ./.env
set +a
.venv/bin/python scripts/preflight.py
```

`local_listening` is expected to be `false` until `uvicorn` is running.

## User Service

After `.env` passes preflight, install the user service:

```bash
systemctl --user link ~/github/workshop/willow-bot/systemd/willow-bot.service
systemctl --user enable --now willow-bot.service
systemctl --user status willow-bot.service --no-pager
```

The service intentionally reads secrets from the gitignored local `.env` file.
Update `WorkingDirectory` / `EnvironmentFile` / `ExecStart` paths if your checkout is not `~/github/willow-bot`.

See also [docs/MOVE-STAY-BORROW.md](docs/MOVE-STAY-BORROW.md).
