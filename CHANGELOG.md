# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added

- **Bot voice on a PR: one status comment and one bot check per head SHA.**
  New `willow_bot/pr_voice.py` exposes two idempotent operations keyed on
  the head SHA. `upsert_status_comment(repo, pr_number, head_sha, body)`
  posts a comment carrying an invisible marker (`<!-- willow-bot:status
  head=<sha> -->`); a repeat call for the same head_sha PATCHes the same
  comment, a different head_sha (a force-push moved the world) writes a
  new row rather than rewriting the old one. `publish_check(repo,
  head_sha, name, status, conclusion, output, external_id)` creates or
  updates one App-owned check-run for that (head_sha, name); the GET
  filters `filter=app&check_name=NAME` server-side so two apps sharing a
  name on the same sha do not confuse the upsert. Both operations key on
  the marker / on `(check_run.id, filter=app)`, never on a login — the
  bot has been renamed twice and a login string was wrong on both sides
  of each rename. Validation before the network: a `completed` check
  without a valid `conclusion`, a conclusion on a non-terminal check, or
  a missing `head_sha` are refused with a receipt line, no POST. An HTTP
  or auth failure is likewise a `could-not-run` receipt line, not a
  raise. Gap `acfd27ae3259` (voice sub-part). Seventeen unit tests cover
  marker uniqueness, first-call create vs update-on-marker, distinct
  head_shas writing side by side, paginated comment walk, refuse-before-
  POST for every invalid check shape, auth-failure receipts, and HTTP
  failure receipts.

### Added

- **Resolve step lands `Idea-Id` trailers as `idea_landings` records and
  reads trailers the reconciler's way.** Each `Idea-Id: willow-ideas-NNN`
  (the reconciler's own id shape, `reconciler/ids.py`) found on a merged
  range is `store_put` under `<idea_id>:<sha12>` as `{idea_id, status, repo,
  sha, merged_at}`, with `Idea-Status: partial` honoured commit-wide; a
  re-run overwrites, a refused put is a line and the gap still resolves.
  Both `Gap-Id` and `Idea-Id` are now read from the commit's trailer block
  only (same rule as `reconciler/gitevidence.trailer_block`): a body that
  explains the convention no longer resolves a gap. The reconciler itself
  keeps reading git; this is the desk's timestamped view of the same landing.
- **Steward `ci` step: a red check reaches a seat.** `run_ci` reads
  `deposits/ci_outcomes.jsonl` from its own byte offset (`ci.offset`) and,
  for each new row whose conclusion is `failure` / `timed_out` / `cancelled`
  / `startup_failure`, files one `human_required_enqueue(kind="review")`
  naming the PR, the leg and the job URL — the bot's own deposits as the
  only source, no lease, no `gh`. Idempotent per (head_sha, check_run_id)
  through `ci_filed` in the state file; a refused filing holds the offset
  and the next tick retries only what was not filed; reds are reported in
  the receipt even with MCP off. Runs between mirror and audit and as
  `willow-bot-steward ci`. Gap 8d1bcb2b7c02: the bot recorded two reds on
  2026-09-14 and reported them to nobody.

### Fixed

- **`ci` step's first run starts at EOF.** Its first live tick walked the
  500+-row deposits file from byte 0 and filed three stale reds (one a test
  fixture) before the limiter stopped it, holding the offset at 0 to file
  the rest next tick. A missing `ci.offset` now means "start at the end,
  write the offset, report `first_run_skipped_bytes`" — the seal watcher's
  rule. A red that happened before the step existed is not the step's to
  raise.
- **Steward `resolve` step: a merged commit names the gap it closed.**
  After `gitsync_sweep` brings a merge home, `run_resolve` reads the merged
  commits' `Gap-Id: <12 hex>` trailers (willows-grove INVARIANTS §11) off the
  `before..after` range of each pulled checkout and calls `gap_resolve` with
  `merged <repo>@<sha>`; `Idea-Id:` trailers are collected into the receipt
  only. Idempotent per (gap, sha) through `gaps_resolved` in the state file;
  a refusal is a line with its reason, never a silent skip; the sweep
  receipt now carries `ranges`. Runs in the loop between sweep and mirror
  and as `willow-bot-steward resolve`. Closes the reader half of gap
  e278ec952b9c (the backlog could not record its own closures).

## [0.1.1] — 2026-09-12

### Fixed

- Drop git URL from optional deps so PyPI accepts the upload; document Nestor
  install for Grove Loki separately.

## [0.1.0] — 2026-09-12

### Added

- Packaged `willow-bot` / `willow-bot-steward` entrypoints (Apache-2.0).
- Steward PR watch (tick/loop/inbox) ported from willow-mcp `loki_pr_watch*`.
- Draft CI deposits (`ci_outcome`) to local JSONL and optional MCP `store_put`.
- Curated MCP heartbeat (`fleet_health`, `commitment_surface`,
  `human_required_list`, `diagnostic_summary`) — dew clock stays on Kart.
