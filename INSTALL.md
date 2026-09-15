# willow-bot install notes

`willow-bot` is a local FastAPI webhook receiver for a GitHub App, plus a
deterministic **steward** (`willow-bot-steward`) that feeds the orchestrator
desk (PR watch / inbox / optional host sync). Commitment **dew** stays on the
Kart worker heartbeat until proven on the bot — do not wire dew into steward yet.

## Package install

```bash
cd ~/github/willow-memory/willow-bot
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
# Dogfood (preferred for systemd):
#   $WILLOW_HOME/venvs/willow-bot/bin/pip install -e '.[dev]'
# optional Grove Loki semantic path (Nestor — not on PyPI as a URL dep):
#   .venv/bin/pip install "nestor @ git+https://github.com/Die-Namic-Systems/Nestor@master"
```

Canonical checkout: `~/github/willow-memory/willow-bot`. The workshop clone is a
stale second home — do not point the unit or Kart bind at it.

Console scripts: `willow-bot` (webhook), `willow-bot-steward` (`tick` | `loop` | `heartbeat` | `sweep` | `resolve` | `mirror` | `ci` | `audit` | `status` | `inbox` | `scan`).

`status` prints one JSON receipt for the seat, with three-state fields (`populated` / `empty` / `unreachable`) — an unreadable journal is not "unit absent". See `willow_bot/status.py`.

Dogfood venv (preferred for systemd): `$WILLOW_HOME/venvs/willow-bot` — keep separate from `venvs/willow-mcp`.

## Local service / secrets

Secrets live in the **operator data vault**, not in the checkout. Drop new
credentials in Nest (`~/Desktop/Nest`); the Nest **secrets** track files them
to `$WILLOW_HOME/secrets/` (usually the vault box). Autointake never auto-files
PEMs — confirm with `nest_intake_file` after `nest_intake_scan`.

| Item | Path |
|------|------|
| App PEM | Nest `*.pem` / `willow-bot.pem` → `$WILLOW_VAULT_BOX/secrets/` |
| App id + webhook secret (+ optional `WEBHOOK_PUBLIC_URL`) | Nest `willow-bot.env` → same, or edit the vault file |
| Optional Fernet keys | `willow-bot/app_id`, `willow-bot/webhook_secret`, `willow-bot/private_key` in `vault.db` |

`WILLOW_VAULT_BOX` defaults to `WILLOW_HOME`, then `~/{user}-data-vault/willow-operator-box`.
If Nest has not filed the env yet, copy the example and fill it (mode 600):

```bash
BOX="${WILLOW_VAULT_BOX:-$HOME/sean-data-vault/willow-operator-box}"
cp secrets/willow-bot.env.example "$BOX/secrets/willow-bot.env"
chmod 600 "$BOX/secrets/willow-bot.env" "$BOX/secrets/willow-bot.pem"
# edit willow-bot.env — GITHUB_APP_ID, GITHUB_WEBHOOK_SECRET, WEBHOOK_PUBLIC_URL
```

Checkout `.env` is no longer the source of truth. Env vars still override for tests.

Run locally:

```bash
export WILLOW_VAULT_BOX="${WILLOW_VAULT_BOX:-$HOME/sean-data-vault/willow-operator-box}"
export WILLOW_HOME="$WILLOW_VAULT_BOX"
set -a && . "$WILLOW_VAULT_BOX/secrets/willow-bot.env" && set +a
.venv/bin/willow-bot
```

Steward one-shot / loop (state under `$WILLOW_HOME` by default):

```bash
export WILLOW_HOME="${WILLOW_HOME:-$HOME/sean-data-vault/willow-operator-box}"
.venv/bin/willow-bot-steward tick
.venv/bin/willow-bot-steward heartbeat   # curated mcp tools when WILLOW_BOT_MCP=1
.venv/bin/willow-bot-steward sweep       # gitsync_sweep via mcp when WILLOW_BOT_MCP=1
.venv/bin/willow-bot-steward loop        # tick + heartbeat + sweep + AGENT_LOOP_TICK_PR_AUDIT
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

Additional env vars (all optional):

| Env | Purpose | Default |
|-----|---------|---------|
| `WILLOW_OPERATOR_GITHUB_LOGIN` | Assignee / requested reviewer for bot-opened PRs (`willow_bot.pr_assign`). Unset is honest-absence, not error. | unset |
| `WILLOW_BOT_DELIVERY_STATE` | Path override for the webhook `X-GitHub-Delivery` LRU (`willow_bot.delivery_dedup`). | `$WILLOW_HOME/willow-bot/delivery-seen.json` |

## Pangolin route

Create a Pangolin resource for the public bot hostname and forward it to:

```text
http://127.0.0.1:9000
```

In GitHub App settings, set the webhook URL to:

```text
${WEBHOOK_PUBLIC_URL}/webhook
```

Use the same `GITHUB_WEBHOOK_SECRET` in the GitHub App and `$WILLOW_VAULT_BOX/secrets/willow-bot.env`.

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
export WILLOW_VAULT_BOX="${WILLOW_VAULT_BOX:-$HOME/sean-data-vault/willow-operator-box}"
set -a && . "$WILLOW_VAULT_BOX/secrets/willow-bot.env" && set +a
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
export WILLOW_VAULT_BOX="${WILLOW_VAULT_BOX:-$HOME/sean-data-vault/willow-operator-box}"
.venv/bin/python scripts/preflight.py
```

`local_listening` is expected to be `false` until `uvicorn` is running.

## User Services

Two units, both rendered from `systemd/*.service.template` by
`scripts/install-service.sh` (never commit a rendered unit):

| Unit | Runs | What it does |
|------|------|--------------|
| `willow-bot.service` | `willow-bot` | the webhook receiver: inbox items, gitsync trigger flags, event log |
| `willow-bot-steward.service` | `willow-bot-steward loop` | the tick, every 300 s: PR watch → curated heartbeat → **gitsync sweep** (`WILLOW_BOT_MCP=1`) |

After vault secrets pass preflight:

```bash
scripts/install-service.sh --all
systemctl --user enable --now willow-bot.service willow-bot-steward.service
systemctl --user status willow-bot.service willow-bot-steward.service --no-pager
```

The steward unit turns `WILLOW_BOT_MCP` on, so the tick's act half goes
through willow-mcp: the heartbeat's curated read-only tools, then
`gitsync_sweep`, which consumes the flags the webhook unit wrote and brings
each merged default branch home under the App's token with a FRANK receipt
(`git_pull_execute`, willow-mcp #524). With MCP on, `merge.py`'s host
`gh`/`git pull`/`pip -e` sync is off unless `WILLOW_BOT_STEWARD_HOST_SYNC=1`
says otherwise — one puller per checkout. Receipts:
`$WILLOW_HOME/willow-bot/steward_heartbeat.jsonl` and the unit's journal
(`steward_sweep` lines; `status: absent` means MCP was off, never "nothing
to do").

The webhook unit uses:
- `WorkingDirectory` / `EnvironmentFile` under `~/github/willow-memory/willow-bot`
- `ExecStart` from `$WILLOW_HOME/venvs/willow-bot/bin/willow-bot` (dogfood venv)
- `bot:app` resolved from the checkout root (not shipped in the PyPI wheel)

The unit sets `WILLOW_VAULT_BOX` and loads `secrets/willow-bot.env` from the vault.
PEM + webhook secret resolve via `credentials.py` — not a checkout `.env`.

See also [docs/MOVE-STAY-BORROW.md](docs/MOVE-STAY-BORROW.md).
