# willow-bot install notes

`willow-bot` is a local FastAPI webhook receiver for a GitHub App. It should
listen on localhost and receive public GitHub webhooks through Pangolin or
another operator-managed ingress.

## Local service

```bash
cd ~/github/willow-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
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
```

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
systemctl --user link ~/github/willow-bot/systemd/willow-bot.service
systemctl --user enable --now willow-bot.service
systemctl --user status willow-bot.service --no-pager
```

The service intentionally reads secrets from the gitignored local `.env` file.
