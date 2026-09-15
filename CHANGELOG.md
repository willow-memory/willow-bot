# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Fixed

- **Steward inbox consumes `check_run` items.** `willow_bot/steward/inbox.py`
  returned early on any item whose `kind` was not `pull_request`, so every
  completed check `fleet_bridge.handle` deposited (keyed on the check id,
  not the PR number — a PR with four legs left four items) accumulated in
  `$WILLOW_HOME/upstream_steward/webhook_inbox/` unread. The tick's journal
  therefore never named a red leg, and the ci step's deposit-file reader
  was the only path a seat could learn a check went red at all. The inbox
  step now also emits one `webhook_check_run` line per row and adds the
  work_id to `inbox_consumed`, so redelivery is a no-op. Every terminal
  conclusion GitHub asserts (`success`, `failure`, `timed_out`, `cancelled`,
  `skipped`, `stale`, `neutral`, `action_required`, plus a rare `null` from
  a completed run without a verdict) is preserved verbatim; the ci step's
  `(head_sha, check_run_id)` dedup key is unchanged, so a red is not filed
  twice. Rows of any other kind (`installation`, `issue_comment`) stay in
  the inbox for a later step, not silently dropped. Closes gap 1d737ffa2595.

### Added

- **Hash chain on `ci_outcomes.jsonl`.** Every row appended by
  `willow_bot.deposits.append_local` carries `prev_hash` (the previous
  chained row's `row_hash`, or `"0"*64` when this row starts the chain)
  and `row_hash` (sha256 over the row's other fields in canonical form,
  plus prev_hash). `verify_chain(path)` walks the file and confirms
  each chained row's prev_hash equals the previous chained row's
  row_hash and each row's row_hash equals a recomputed value; a tampered
  historical row breaks the chain at its successor, and the receipt
  names the 1-indexed line of the break. Legacy rows written before this
  step existed are tolerated at the head of the file: the verifier
  reports `legacy_head` and `chained_from` distinctly, so an upgrade to
  a live box does not read as a broken chain. A sidecar
  `ci_outcomes.chain.tip` keeps append O(1); a missing tip file is
  rebuilt on the next append by scanning the deposits tail (the deposits
  file is the source of truth, tip is an index). Canonical form sorts
  keys and excludes the chain fields, so a rec re-hashed after storage
  computes the same value, and a set-arithmetic caller does not
  accidentally desynchronize the hash. Gap `a6c0926d7e83` (hash chain
  sub-part). Seventeen unit tests cover first-row-from-genesis,
  second-row-chains-first, tip file tracks head, tip recovery from
  missing tip, empty-file receipt, all-chained receipt, legacy-head-
  then-chain, tampered-body detection, broken-prev detection, legacy-
  row-inside-chain detection, non-JSON row detection, canonical-form
  key-order stability, chain-key exclusion, prev-changes-changes-hash,
  sha256 shape, and end-to-end coverage via `deposit_from_check_run_payload`.

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
