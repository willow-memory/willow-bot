# Changelog

All notable changes to this project are documented here.

## [0.3.0](https://github.com/willow-memory/willow-bot/compare/v0.2.0...v0.3.0) (2026-09-20)


### Added

* **steward:** run_ci — cancelled is its own state, one item per head, resolve-on-green ([2d0709b](https://github.com/willow-memory/willow-bot/commit/2d0709bdc771ce15011f01f5eb387393f5f76105))
* **steward:** run_ci — cancelled is its own state, one item per head, resolve-on-green, legacy clear ([19da483](https://github.com/willow-memory/willow-bot/commit/19da483fe06440ce6a2240c46e9c0fb7e15dc9e1))


### Fixed

* **steward:** legacy clear reads only this build's ids, keeps real reds on open PRs, paces; bad-clock successor judged by first sighting ([abc9ab0](https://github.com/willow-memory/willow-bot/commit/abc9ab0d41e1187d4cdab6d433e639af7b49f4bb))
* **steward:** run_ci rework after audit — honest green, cancelled is never red, re-run resolves, bounded state, legacy clear ([2468670](https://github.com/willow-memory/willow-bot/commit/2468670fd88092de980d1396032d7b4b8fdc9f88))
* **steward:** scan runs with argv reset and receipts its open set; legacy clear trusts open only through it, names merged vs closed ([f693d46](https://github.com/willow-memory/willow-bot/commit/f693d4643928dd69f7514bfecab213aa59c467a1))

## [0.2.0](https://github.com/willow-memory/willow-bot/compare/v0.1.2...v0.2.0) (2026-09-19)


### Added

* **steward:** seal watch runs on the tick — seal_drain in the curated heartbeat ([d3c5aa9](https://github.com/willow-memory/willow-bot/commit/d3c5aa93fc31f554e243f4179c639214e12a86b7))
* **steward:** seal watch runs on the tick — seal_drain in the curated heartbeat ([7aa1d52](https://github.com/willow-memory/willow-bot/commit/7aa1d5224617f8390a4c121a2df72b681a7acd1c))

## [Unreleased]

### Changed

- **Steward `run_ci` stops drowning the review queue** (gap `25cb3c1a3489`).
  `cancelled` is no longer a red: a cancelled leg is held in
  `ci_cancelled_pending` and decided each tick — `superseded` when a later
  head for the same PR shows up in the bot's own deposits (dropped, never
  filed), `waiting` inside the grace window, `stuck` after it (filed), or
  `unreachable` when the deposit's timestamp cannot be read (neither).
  Grace defaults to 10 minutes (`WILLOW_BOT_CI_CANCELLED_GRACE_MIN`). Red
  legs collapse into ONE review item per (repo, pr, head_sha), keyed in
  `ci_items`, with the aggregate `test` job folded into its cause's item;
  a later leg on a filed head joins the item (`appended`) instead of filing
  again. When a later head for the same PR is fully green, the PR's older
  items are resolved through `human_required_resolve` with `superseded by
  <sha>, green at <ts>`. `ci_filed` keeps its `head_sha:check_run_id` key
  shape so the voice step's `ci-red` label mapping is unchanged.

  After Loki's audit (dispatch 82A7DB13): "green" means every leg the
  item's own head recorded has reported green on the later head — the
  earlier head's leg set is the only expected set the bot knows — never
  a single early green leg; a head re-run to green resolves its own item
  (`re-run green at <ts>`); a stuck cancelled leg is remembered in
  `ci_filed_cancelled`, not `ci_filed`, so voice never shows it as red;
  a pending cancelled leg whose check re-runs is `rerun` and dropped; an
  `unreachable` leg ages by first sighting (`pending_since`) and becomes
  `stuck`; the state maps are pruned each tick to one live head per PR
  plus the heads of unresolved items and pending cancels, with resolved
  items dropped (`ci_filed` remains the durable no-refile memory).
  **`ci-legacy-clear`** — a new tick step (also a subcommand, `force`)
  that runs once, lists open review items, and resolves the ones the
  leg-per-item `run_ci` filed before this build (old title shape, not one
  of this build's `ci_items` ids, GitHub `source_ref`) with `superseded by
  the run_ci collapse build (<sha>): cancelled run, not a failure`. A real
  red (`failure` / `timed_out` / `startup_failure`) on a PR still in the
  scan's `open` set is KEPT and reported — the operator asked for the
  noise cleared, not for a live failure to be called superseded; a red on
  a merged PR is cleared as `moot: PR merged`, on a closed one as `moot:
  PR closed without merge`. `open` is trusted only through an unfiltered
  scan record (see below); otherwise every real red is kept. Resolves are
  paced against the store limiter the way the mirror step is. Per item
  resolved / refused / kept, the whole step `unreachable` when the queue
  cannot be listed; recorded in `ci_legacy_cleared` only when the pass was
  clean and complete, so a partial or paced pass re-runs next tick.

### Fixed

- **Steward scan ran under the unit's own argv** (gap `1045a4056d11`).
  `run_once` called `scan.main()` with the process argv unreset; scan reads
  `sys.argv[1:]` as repo filters, the unit runs `willow-bot-steward loop`,
  so the filter was `['loop']`, every PR was rejected, and `state['open']`
  was written empty on every tick (catchup refilled three repos a tick
  behind it). The argv is now reset around the scan the way `merge`'s
  already was; filters are an explicit `run_once(scan_filters=...)`
  argument; and a `steward_scan` receipt (`open`, `filters`, `at`, and a
  `detail` line when nothing was found) makes an empty open set visible.
  The same record is written to `state['scan']`.

### Added

- **Steward tick reconciles owned-prefix labels on every open PR.**
  New `willow_bot/steward/voice.py` exposes `run_voice(state)` that
  reads the tick's state file, maps `audit_dispatched[repo#pr]` →
  `willow-bot/audit-dispatched`, and calls the label reconciler
  (`pr_labels.reconcile_labels`) for each open PR — including PRs with
  an empty desired set, so stale owned labels are cleaned up. Wired
  into the loop after `run_ci` and `run_audit` (so voice sees fresh
  state) and exposed as the CLI subcommand `willow-bot-steward voice`.
  Idempotent: an unchanged desired set is a network no-op inside the
  primitive (only a GET). Per-PR failures surface in `refused` while
  successful reconciles still land; `status` reflects the mix
  (`ok` / `partial` / `could-not-run`). Twelve unit tests cover
  parse_key edge cases, audit_dispatched mapping, per-PR reconcile
  outcomes, partial vs. all-fail status, garbled open keys, empty open
  set, jsonl append, and state-file fallback when no arg is passed.
  This closes the caller side of gap `acfd27ae3259`.

  `ci_filed` → `ci-red` label mapping is stubbed pending a shape update
  (the current `ci_filed` key is `head_sha:check_run_id`, without a PR
  index). `pr_voice` comment + `pr_voice.publish_check` wiring is left
  for a follow-on that carries a head SHA through the state.

### Fixed

- **Webhook boundary dedups on `X-GitHub-Delivery`.** `bot.py`'s webhook
  handler now consults `willow_bot.delivery_dedup.mark_seen(delivery_id)`
  before dispatching. A redelivery (operator-triggered replay, GitHub's
  own retry after a non-2xx, a proxy handing the same body to two
  instances) short-circuits with `{"ok": true, "dedup": "delivery_seen"}`
  and does not re-run `router.route` or `fleet_bridge.handle`. Without
  the guard `event-log.jsonl` and `ci_outcomes.jsonl` (pure-append
  journals) took a duplicate row on every retry, and `quips.record_merge`
  double-counted a merge. The upstream_steward inbox and the gitsync
  trigger flag were already semantic-idempotent — this closes the
  journal/counter gap at the boundary. A bounded LRU (5000 delivery ids,
  atomic-rename JSON under `$WILLOW_HOME/willow-bot/delivery-seen.json`)
  survives a restart, so a redelivery landing after a systemd roll still
  dedups. Fails open on a disk error — a broken cache does not drop real
  deliveries. Gap acfd27ae3259 (webhook idempotency sub-part).
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

- **Catch-up-in-tick: repair a missed webhook next tick.** New
  `willow_bot.steward.tick.run_catchup()` polls `/repos/{owner}/{repo}/pulls`
  under the App's install token for a bounded batch of repos each tick,
  reconciles the answer with `state["open"]`, and promotes any missing
  PR to `pending_audit` under the same shape `run_once` writes — the
  audit step in the same tick then dispatches it. A lost webhook
  (Pangolin restart mid-delivery, systemd roll while a POST was in
  flight, proxy dropping the body) is now closed on the next tick,
  bounded by the tick interval rather than by an operator noticing.
  Repos are discovered from every state key that has ever carried a
  `repo#pr` (`open`, `seen`, `merged_synced`, `pending_audit`,
  `audit_dispatched`) plus a `WILLOW_BOT_CATCHUP_EXTRA_REPOS`
  comma-separated env for repos the App is on but state has not seen
  yet. A cursor (`state["catchup_cursor"] = {next_index,
  last_polled_at, batch}`) rotates the polling — three repos per tick
  by default, 36 requests/hour under the App installation's 5000/hr
  budget. Idempotent per (repo, PR) through the same guards as
  `run_once`. Honest absence when MCP is off — this is an act-half
  read. New `github_app.list_open_pulls(repo, per_page, max_pages)`
  underlies the poll, using the same JWT + install-token machinery
  `post_comment` already uses. New CLI subcommand
  `willow-bot-steward catchup`. Thirteen unit tests cover the
  absent-when-off path, no-repos-is-honest-ok, discovery from every
  state key, extras-env, a missed PR promoting to pending_audit, no
  duplicate on race with a webhook, no re-pend of an already-dispatched
  PR, per-repo error line, cursor rotation across ticks, a partial-page
  PR shape not crashing, the receipt landing in `steward_ticks.jsonl`,
  CLI wiring, and `catchup → audit` loop ordering.
- **Tick step: refresh each pulled checkout's editable install.** New
  `willow_bot.steward.tick.run_install_receipts(sweep)` reads the ranges
  the sweep brought home and calls
  `willow_bot.install_receipt.refresh_editable(Path(checkout), default_branch)`
  for each. Wired into `run_loop` between `resolve` and `mirror` — a
  merge that arrives via `gitsync_sweep` now has its editable install
  refreshed in the same tick, without a human's credential on the box.
  The default branch is read from the checkout's `origin/HEAD` symref
  (set by `git clone`); a checkout with no symref (fresh `git init`,
  remote added later) is refused as `unknown_default_branch` rather
  than guessed as `main`, so a repo defaulting to `master` cannot be
  yanked off it. One range raising is a per-entry `state="error"` line,
  not a dead step. New CLI subcommand `willow-bot-steward install-receipts`
  runs a sweep and then the install step, mirroring `resolve`. Eleven
  unit tests cover the empty-sweep-is-honest-ok path, a real git clone
  landing on `install=skipped` (no `.venv`), `origin/HEAD` read, a
  supplied `default_branch` short-circuiting the read, a missing checkout,
  an `origin/HEAD`-absent clone, a raised exception per range, receipts
  landing in `steward_ticks.jsonl`, CLI wiring, and loop ordering
  (resolve → install → mirror). Deprecates `willow_bot.steward.merge.sync_checkout`,
  which stays on disk for one prove window behind `WILLOW_BOT_STEWARD_HOST_SYNC=1`
  and is scheduled for removal once the receipts land against a live
  merge. Gap `1f6b033ffca7` (bot half, wiring).
- **Merged-release install receipt.** New
  `willow_bot/install_receipt.py` exposes `refresh_editable(repo_dir,
  default_branch, *, remote="origin", do_install=True)` — brings a
  checkout to the current remote default and refreshes its editable
  install only when it is safe to do so. The receipt's `state` names
  distinctly what it found: `missing_checkout`, `dirty`,
  `on_feature_branch`, `fetch_failed`, `diverged`, `ahead`, `install_failed`,
  or `ok`. Never switches branches (an agent's active feature branch is
  never touched), never resolves a merge conflict (a diverged checkout
  is a line, not a merge commit), never creates a venv (a missing venv
  is `install=skipped`, state stays `ok`). Fast-forward uses `git merge
  --ff-only` rather than `git pull` so the default behaviour cannot
  turn a diverged checkout into a merge commit. A sidecar
  `.willow-bot-installed.commit` stamp records the commit the pip
  install ran on, so a future tick tells drift by comparing
  `checkout_commit` with `installed_commit` (the stamp is read into
  `installed_before` and, only on a successful install, overwritten
  with the new HEAD). Gap `1f6b033ffca7` (bot half). Thirteen
  integration tests use real `subprocess git` against tmp_path
  checkouts (a fake would hide the exact shape of `--porcelain`,
  `rev-list --left-right --count`, and `merge --ff-only`): missing
  checkout, dirty tree refuses refresh, feature branch is not
  touched, up-to-date-with-no-venv is `install=skipped`, behind ->
  fast-forward updates `checkout_commit`, ahead reports but does not
  push, diverged refuses refresh, install-ok writes stamp with HEAD,
  install-failed does NOT write stamp, `installed_before` reads
  prior stamp, `read_installed_commit` returns None when missing,
  `do_install=False` never calls pip, and a sanity check that `git` is
  on PATH.
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
- **Operator assignment for bot-opened PRs.** New
  `willow_bot/pr_assign.py` exposes `assign_to_operator(repo, pr_number,
  *, login=None)`. With no argument it reads
  `WILLOW_OPERATOR_GITHUB_LOGIN` from the env; an unset or blank env is
  `status="absent"` (honest absence, not error) so a fleet without an
  operator wired does not see refusal noise. On a configured operator
  the module hits both `POST /issues/{pr}/assignees` and `POST
  /pulls/{pr}/requested_reviewers` — assignee and requested-reviewer
  are independent APIs and either half succeeding on its own is worth
  reporting. GitHub silently drops an unreachable login on
  `/assignees` (200 with the ORIGINAL list back); the module inspects
  the response body and reports "not in returned list — unreachable
  login?" rather than a false success. A 422 on `/requested_reviewers`
  (already requested or refused) is not a raise. The receipt names
  exactly which half landed so a retry can hit only the miss:
  `status=ok` on full success, `partial` on one-of-two,
  `could-not-run` on both-failed. Gap `acfd27ae3259` (assignment
  sub-part). Twelve unit tests cover env lookup edge cases,
  absent-when-unconfigured, explicit login override, happy path,
  review-422 as already-or-refused, unreachable login as a specific
  line, assign failure not stopping review, auth failure, review 5xx,
  and both-failed as could-not-run.
- **Steward-state labels on a PR: reconcile the bot's owned namespace.**
  New `willow_bot/pr_labels.py` exposes `reconcile_labels(repo,
  pr_number, desired, owned_prefix="willow-bot/")` that brings the PR's
  labels under the owned prefix to exactly `desired` — additive within
  its own namespace, never touching labels a human or another bot
  applied (`bug`, `area/steward`, …). Vocabulary shipped: `ci-red`,
  `needs-ratification`, `audit-dispatched`, `bot-opened`, each under
  `willow-bot/`. A caller passing a label outside the namespace in
  `desired` is refused before any HTTP — otherwise the reconcile would
  add the label and then immediately delete it on the next pass. Empty
  strings in `desired` are silently dropped so a set-arithmetic caller
  never lands one in the POST body. Failure modes as receipts: an
  unavailable App, a list-labels error, and a partial reconcile
  (add-failed but remove-succeeded) each surface in `refused` with the
  op that failed. A DELETE 404 is treated as "already gone" — a
  concurrent reconcile that beat us to it left the label where we
  wanted it. Gap `acfd27ae3259` (labels sub-part). Thirteen unit tests
  cover vocabulary invariants, first-call add, repeat is a no-op on the
  network, moving between states adds new and removes stale in one pass,
  non-owned labels are reported but not touched, out-of-namespace desired
  is refused before HTTP, empty strings dropped, auth/list failures as
  lines, partial success is not silent, DELETE 404 is treated as absent,
  and pagination walks until a short page.
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
