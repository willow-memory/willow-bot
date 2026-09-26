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

Console scripts: `willow-bot` (webhook), `willow-bot-steward` (`tick` | `loop` | `heartbeat` | `sweep` | `resolve` | `install-receipts` | `mirror` | `ci` | `catchup` | `upstream-desk` | `audit` | `ingress` | `status` | `inbox` | `scan`).

`status` prints one JSON receipt for the seat, with three-state fields (`populated` / `empty` / `unreachable`) — an unreadable journal is not "unit absent". See `willow_bot/status.py`.

`ingress` asks GitHub for the **App webhook**'s recent deliveries (`/app/hook/deliveries`, App JWT) on every tick. A repository or organization hook is not read. Verdicts: `ok` (newest delivery 2xx, within `WILLOW_BOT_INGRESS_QUIET_DAYS`, default 7), `quiet` (newest 2xx but older than that), `degraded` (newest not 2xx, fewer than 3 in a row), `failing` (newest 3 not 2xx, at least one HTTP answer), `unanswered` (newest 3 got no HTTP answer), `empty`, `malformed` or `unreachable`. Only 2xx counts, because GitHub does not follow redirects. `failing` and `unanswered` file one `human_required` item and resolve it when the newest delivery is 2xx again. The hook URL is reduced to `scheme://host[:port]/path`, with any path segment that is not a plain lowercase word (or that starts with a GitHub token prefix) shown as `…`, before it is written anywhere. `status` shows the last verdict under `ingress`, with `latest_age_s`.

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

The box is wherever willow-data-vault's `bootstrap/provision.sh <box>` created it; `WILLOW_HOME` (or `WILLOW_VAULT_BOX`) must name it. There is **no default box**, and the box must already exist: with neither set, or with one naming a directory that is not there, willow-bot refuses (`BoxNotConfigured`) instead of guessing a path or creating one. Everything (state, secrets, the webhook fan-out) reads `WILLOW_HOME` first, then `WILLOW_VAULT_BOX`. **Commands run by hand need the box exported**; the systemd units already set it.
If Nest has not filed the env yet, copy the example and fill it (mode 600):

```bash
BOX="${WILLOW_VAULT_BOX:?set WILLOW_VAULT_BOX to the data-vault box}"
cp secrets/willow-bot.env.example "$BOX/secrets/willow-bot.env"
chmod 600 "$BOX/secrets/willow-bot.env" "$BOX/secrets/willow-bot.pem"
# edit willow-bot.env — GITHUB_APP_ID, GITHUB_WEBHOOK_SECRET, WEBHOOK_PUBLIC_URL
```

Checkout `.env` is no longer the source of truth. Env vars still override for tests.

Run locally:

```bash
export WILLOW_VAULT_BOX="${WILLOW_VAULT_BOX:?set WILLOW_VAULT_BOX to the data-vault box}"
export WILLOW_HOME="$WILLOW_VAULT_BOX"
set -a && . "$WILLOW_VAULT_BOX/secrets/willow-bot.env" && set +a
.venv/bin/willow-bot
```

Steward one-shot / loop (state under `$WILLOW_HOME`, which must be set):

```bash
export WILLOW_HOME="${WILLOW_HOME:?set WILLOW_HOME to the data-vault box}"
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
| `WILLOW_BOT_UPSTREAM_DESK` | Poll GitHub notifications + refresh author PR ledger on the steward loop (`upstream-desk` step). Needs `gh` user auth. | `0` |
| `WILLOW_BOT_UPSTREAM_WATCH_REPOS` | Comma-separated Tier A upstream repos for triage. | See `willow_bot/steward/upstream_config.py` |
| `WILLOW_BOT_UPSTREAM_TRACKER_EVERY` | Steward ticks between GraphQL ledger refreshes. | `12` (~1 h at 300 s) |

`willow-bot-steward status` includes an `upstream` block: `ledger.open_prs`, pending depth by lane, and the last `steward_upstream_desk` receipt.

Planned: upstream fork rebase + `--force-with-lease` push — see [`docs/SPEC-contrib-refresh.md`](docs/SPEC-contrib-refresh.md).

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
export WILLOW_VAULT_BOX="${WILLOW_VAULT_BOX:?set WILLOW_VAULT_BOX to the data-vault box}"
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
export WILLOW_VAULT_BOX="${WILLOW_VAULT_BOX:?set WILLOW_VAULT_BOX to the data-vault box}"
.venv/bin/python scripts/preflight.py
```

`local_listening` is expected to be `false` until `uvicorn` is running.

## Pre-push CI-fresh check

Before pushing a branch that changes `pyproject.toml` or adds a new test
dependency, run `scripts/fresh-check.sh` — it builds a temp venv from scratch,
installs `-e ".[dev]"`, and runs the test suite. This reproduces the CI
shape (`pip install -e ".[dev]"` on a fresh matrix leg) so a dev-dep that
your local `.venv` picked up by hand does not slip past you and turn CI
red. The venv is a tempdir and cleaned on exit.

```bash
scripts/fresh-check.sh
```

## User Services

Two units, both rendered from `systemd/*.service.template` by
`scripts/install-service.sh` (never commit a rendered unit):

| Unit | Runs | What it does |
|------|------|--------------|
| `willow-bot.service` | `willow-bot` | the webhook receiver: inbox items, gitsync trigger flags, event log |
| `willow-bot-steward.service` | `willow-bot-steward loop` | the tick, every 300 s: PR watch → curated heartbeat → **gitsync sweep** (`WILLOW_BOT_MCP=1`) |
| `willow-bot-deterministic.service` | `willow-bot-deterministic serve` | loopback Ollama socket (`$WILLOW_HOME/willow-bot/deterministic.sock`) for Kart `client` / `ladder` |

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

## Deterministic runner (flowering T1 MVP)

Host owns loopback Ollama; Kart calls `willow-bot-deterministic client` over
`$WILLOW_HOME/willow-bot/deterministic.sock` (no `allow_localhost`).

**Horizon:** if this works, model weights and inference may move *into* the bot
boundary (`$WILLOW_HOME/willow-bot/`) instead of ambient `~/.ollama` on disk —
church/state: git holds code, vault holds secrets, bot stewards what may run
locally.

Bootstrap one-liners (operator terminal, canonical checkout):

```bash
source ~/.willow/fleet.env
test -x "$WILLOW_HOME/venvs/willow-bot/bin/python" || python3 -m venv "$WILLOW_HOME/venvs/willow-bot"
"$WILLOW_HOME/venvs/willow-bot/bin/pip" install -e '/home/sean-campbell/github/willow-memory/willow-bot[dev]'
mkdir -p "$WILLOW_HOME/willow-bot/runs"
cp /home/sean-campbell/github/willow-memory/willow-bot/deploy/deterministic-policy.template.json "$WILLOW_HOME/willow-bot/deterministic-policy.json"
# Stop any nohup serve; run through systemd (same pattern as webhook + steward):
pkill -f 'willow-bot-deterministic serve' 2>/dev/null || true
cd /home/sean-campbell/github/willow-memory/willow-bot
scripts/install-service.sh willow-bot-deterministic
systemctl --user enable --now willow-bot-deterministic.service
systemctl --user status willow-bot-deterministic.service --no-pager
test -S "$WILLOW_HOME/willow-bot/deterministic.sock" && echo socket ok
"$WILLOW_HOME/venvs/willow-bot/bin/willow-bot-deterministic" client --fixtures /home/sean-campbell/github/willow-memory/willows-grove/seat/willow/experiments/flowering-2026-09 --model llama3.2:3b --limit 1
```

Kart smoke (desk `task_submit`, same client):

```bash
willow-bot-deterministic client --fixtures /home/sean-campbell/github/willow-memory/willows-grove/seat/willow/experiments/flowering-2026-09 --model llama3.2:3b --limit 1
```

(run from a Kart task with `WILLOW_HOME` set and `$WILLOW_HOME/venvs/willow-bot/bin` on `PATH`, or use the full path to `client` in the task string.)

### The chain: D0, then local, then flowering

`willow-bot-deterministic chain` runs each act through code first (D0,
`resolve`), sends only what D0 escalates to one local model under a JSON
schema where `ESCALATE` is always a valid answer, and writes whatever the
local tier escalates, answers off-schema, cites outside the pool, or cannot
reach as a `flowering` row addressed to `willow`. It never calls a cloud
model. A routing brief ("Route this item ...") closes in code to `willow`.

```bash
# on the host (needs loopback Ollama)
willow-bot-deterministic chain --fixtures <dir> --model willow-lane4-3b
# from Kart, through the serve socket (restart the serve unit after an upgrade)
willow-bot-deterministic chain-client --fixtures <dir> --model willow-lane4-3b
```

The summary reports `closed_code`, `closed_local`, `flowering`,
`grown_share`, `cloud_per_act_max` (an upper bound: one cloud call per
flowering act at most) and precision per tier. Exit 1 means D0 answered
something wrong; a wrong local answer is a measurement, not a failed run.
