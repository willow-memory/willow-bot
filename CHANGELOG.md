# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added

- **Read-only status surface for the seat.** New `willow_bot/status.py`
  exposes `report()` returning one structured dict covering: package
  version, running commit (git HEAD of the checkout), last heartbeat
  and last tick receipt, a recent journal excerpt (bounded to 5 rows),
  webhook inbox depth by kind, cursor offsets (mirror, ci) and the
  chain tip, and the last successful sweep. Every field carries its
  own three-state status (`populated`, `empty`, `unreachable`) so the
  seat can tell a fresh install (empty) from a broken read
  (unreachable) — an unreadable journal is not "unit absent". One
  field's miss never hides another field's data. Wired into the CLI as
  `willow-bot-steward status`. Gap `158600e03598`. Twenty-two unit
  tests cover overall shape, ISO8601 timestamp, version populated when
  installed, running-commit unreachable without .git, running-commit
  populated reads the sha (subprocess stubbed), missing-receipt-file is
  empty (not unreachable), populated receipt returns last row, garbage
  file is unreachable, garbage-tail-past-valid-row still reads the
  valid row, journal returns the last N rows, journal absent is empty,
  journal marks unparseable row instead of dropping it, inbox absent
  is empty-zero, inbox counts by kind, inbox unreadable entry is
  counted not dropped, cursors absent is empty, cursors present, cursor
  garbled offset is None not raise, sync no-tick-file is empty, sync
  returns last-ok-sweep only, sync ignores non-sweep events, and one
  field's failure does not hide another's data.

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
