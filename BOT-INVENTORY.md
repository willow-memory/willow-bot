# BOT-INVENTORY.md

What this tree asserts about the one bot identity it acts as, and nothing
more. Written for gap `683b5fb27a69`: `pr_voice.py` and
`fleet_bridge._sender_type` both cited this file before it existed.

## Match by type, not login

The bot has been renamed twice (`github_app.py`). Both times, a hardcoded
login string was wrong on one side of the rename. Nowhere in this tree does
code compare `sender.login` / `user.login` to a bot name. Every match is on
the actor's `type` field instead:

> `sender.type` as GitHub asserts it ("Bot" / "User" / "Organization"), or
> "" when the payload carries none. Never the login.
> — `fleet_bridge._sender_type`

> Neither posts a login: matching is by the marker for comments and by
> `(check_run.id, filter=app)` for checks, so a rename of the App bot does
> not orphan history.
> — `pr_voice` module docstring

Why: a login survives a rename by being wrong. `type` is asserted by
GitHub about the credential itself and does not change when the App's
display name does.

## Identity

The App this tree acts as: **`willows-bot`** (id `4001890`, per `README.md`).
Propose-only — see "What it never does" below.

Credentials resolve through `credentials.py` (`credentials.resolve`), in
this order — env, then the vault env file, then Fernet vault keys, then a
default PEM path. Env/vault keys it reads, never their values:

| Field | Env | Vault (Fernet, `willow-bot/…`) | File default |
|---|---|---|---|
| App id | `GITHUB_APP_ID` | `willow-bot/app_id` | — |
| Webhook secret | `GITHUB_WEBHOOK_SECRET` | `willow-bot/webhook_secret` | — |
| Private key | `GITHUB_APP_PRIVATE_KEY_PATH` (path) | `willow-bot/private_key` | `$WILLOW_VAULT_BOX/secrets/willow-bot.pem` |

Non-PEM settings also load from `$WILLOW_VAULT_BOX/secrets/willow-bot.env`.

## Label namespace it owns

`willow-bot/*`, reconciled by `pr_labels.reconcile_labels`. Labels outside
this prefix are read (for the receipt) but never added or removed
(`pr_labels.py`). The vocabulary shipped today: `willow-bot/ci-red`,
`willow-bot/needs-ratification`, `willow-bot/audit-dispatched`,
`willow-bot/bot-opened`.

## Comment and check markers it owns

- Status comment: one per `(repo, pr, head_sha)`, carrying the HTML marker
  `<!-- willow-bot:status head=<sha> -->` (`pr_voice.comment_marker`). A
  repeat call for the same head_sha updates that comment; a new head_sha
  opens a fresh one.
- Check-run: one per `(repo, head_sha)` named `pr_voice.CHECK_NAME`
  (`willow-bot/steward`), looked up by `(head_sha, name, filter=app)`
  before deciding create vs. update (`pr_voice.publish_check`).

## What it never does

Merge, approve, or close a PR. No code path in this tree calls a merge,
review-approval, or close endpoint — the App's only writes are comments,
checks, and its own label namespace. This mirrors willows-grove
INVARIANTS.md §12 (no fleet persona commits, merges, or wires the fleet
without a recorded authorization); this tree does not implement any of
those verbs to begin with.

## Other bot identities

No other bot identities are declared in this tree.
