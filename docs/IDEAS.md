# Ideas: what GitHub bots usually do that willow-bot doesn't (yet)

Drafted 2026-10-11, after #123 (sticky status comment, size/conflict/stale/title labels). Refined the same day: checked against the code, sources re-fetched.
What the bot already has: webhook voice, CI-red filing + deposits, CI backfill,
webhook redelivery, owned `willow-bot/` labels, one status comment + one check run
per PR, first-timer welcome, upstream desk / contributions, audit dispatch, PR assign.

Every idea must stay operator-agnostic: off or empty by default, turned on in the
operator profile, bots matched by `type` never login, labels under `willow-bot/`.
Also: a fact the bot cannot read never strips a label; it never merges on its own; prefer statuses and labels over comment noise.

**How to read this.** Effort S/M/L, value low/med/high. "Builds on" names the code that already covers part of the idea. "Fit" is the adaptation the rules above force. Sources are primary repos/docs that were fetched; a link marked (docs, not re-fetched) is an official GitHub page that the sandbox could not reach this pass, cited from memory.

## Already done (dropped from the list)

- Conventional-title label: `willow-bot/title`, off by default (`conventional_titles`), `willow_bot/steward/voice.py`.
- Size, conflict and stale labels (`willow-bot/size/*`, `willow-bot/conflict`, `willow-bot/stale`), with the "unreadable fact keeps the label" rule already in `_hygiene`.
- Status check per PR: `pr_voice.publish_check` (check run `willow-bot`), plus the sticky status comment.
- Reviewer request API call exists in `willow_bot/pr_assign.py` (POST `/requested_reviewers`), so reviewer assignment is not from scratch (see 2).

Checked and absent: slash commands (`router._handle_comment` only logs outside comments), update-branch, auto-merge, reminders, backport, branch deletion, ETag / conditional requests, lock, needs-info, path labels, linked-issue and file-size checks.

## PR flow

1. **Slash commands** in PR/issue comments (`/retest`, `/label x`, `/assign @y`, `/rebase`). M, high. Builds on `_handle_comment` and `contributions.INSIDERS`. Fit: command list and allowed labels come from the profile and are empty by default; gate on `author_association` (OWNER/MEMBER/COLLABORATOR); ignore comments by `user.type == "Bot"`. Sub-ideas: *label aliases* (one word expands to a label set) and an *allowlist of labels non-members may apply*, both profile data. Reference: [Prow](https://github.com/kubernetes-sigs/prow) and [triagebot](https://github.com/rust-lang/triagebot) both run label commands from comments; their exact allowlist syntax could not be re-checked, so design it from scratch.
2. **Reviewer assignment** from CODEOWNERS or round-robin over a team, skipping the author. M, med. Builds on `pr_assign.py`. Fit: opt-in; read CODEOWNERS from the repo rather than copying it into config.
3. **Review reminders**: ping requested reviewers after N days of silence, once per round. M, med. Source: [probot/reminders](https://github.com/probot/reminders) (`/remind` command). Fit: one comment with a marker per round; prefer a line in the status comment.
4. **Auto-update branch** when the PR is behind base and green (`update-branch` API, [docs](https://docs.github.com/en/rest/pulls/pulls#update-a-pull-request-branch), not re-fetched). S, med. Source: [Kodiak](https://github.com/chdsbd/kodiak) "Auto Update". Fit: it pushes a commit to the contributor's branch, so profile opt-in plus a per-PR opt-in label; skip on conflict and on unknown `mergeable_state`.
5. **Auto-merge on label**: enable GitHub's native auto-merge when a human adds a label. S-M, med. Source: Kodiak `automerge` label, [GitHub docs](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/automatically-merging-a-pull-request) (not re-fetched). Fit: GitHub merges once branch protection is met, the bot never calls merge; do nothing if protection is missing.
6. **Conflict nudge** when `willow-bot/conflict` first appears. S, low. Builds on the conflict label. Fit: add a line to the sticky status comment, not a new comment.
7. **WIP / draft guard**: the check stays pending while the title says WIP or the PR is draft. S, low-med. Builds on `publish_check`. Source: [action-semantic-pull-request](https://github.com/amannn/action-semantic-pull-request) (`wip`, `ignoreLabels`).
8. **Conventional title as a status**, not just a label. S, med. Builds on `willow-bot/title`. Add `ignoreLabels`, WIP skip and an optional required scope (same source as 7). Fit: report through the check run.
9. **Linked-issue check**: fail a status when no issue number is found. S, med. Source: [bedevere](https://github.com/python/bedevere). Fit: profile-gated; pattern comes from the profile.
10. **Path labels** `willow-bot/area/<name>` from a glob map in the profile. M, med. Source: [actions/labeler](https://github.com/actions/labeler) (`sync-labels` default false, `max-files-changed` cap). Fit: the bot owns the `willow-bot/area/` prefix, so syncing inside it is safe; add the prefix to `HYGIENE_PREFIXES`; if the file list is truncated or unreadable, keep existing labels. Needs the PR files API (not in the PR payload).
11. **Large file guard**: fail a check when a PR adds a file above a size limit. S, low-med. Same file-list fetch as 10. Rule style from [Danger](https://github.com/danger/danger-js).
12. **Changelog entry check**: fail a status when no changelog file changed unless a skip label is set. S, low-med. Source: bedevere (`skip-news`). Fit: this repo uses release-please (`release-please-config.json`), so only worth it for repos that hand-write a changelog.
13. **Backport**: `/backport release-x` opens a cherry-pick PR. L, med. Source: [miss-islington](https://github.com/python/miss-islington) (label set before merge; on failure it comments and assigns the merger). Fit: opens a PR only, never merges it; depends on slash commands (1).
14. **Branch cleanup**: delete the head branch after merge for same-repo branches. S, low. Source: [delete-merged-branch](https://github.com/SvanBoxel/delete-merged-branch), whose README says GitHub now has a native setting for this. Fit: prefer reporting the repo setting over deleting anything.

## Issues

15. **Stale close** for issues and PRs: warn, then close after a grace period. M, med. Builds on `willow-bot/stale` (PR head-date only, label only today). Source: [actions/stale](https://github.com/actions/stale) (warn then close, defaults 60 + 7 days, exempt labels, `operations-per-run` cap 30). [probot/stale](https://github.com/probot/stale) is deprecated. Fit: closing is off by default (`close_days` unset), exempt labels in the profile, per-run cap, activity clears the label; ship warn-only first.
16. **Needs-info loop**: label when a maintainer asks, unlabel when the author replies. S-M, med. Source: [probot/no-response](https://github.com/probot/no-response) (archived 2021; it points to an Action). Fit: label `willow-bot/needs-info`; match the author by user id, ignore bots by `type`; leave closing to 15.
17. **Template check**: flag issues/PRs that deleted required template sections. S-M, low-med. Rule style from Danger (PR body checks). Fit: required headings from the profile; label or status only.
18. **Lock old closed threads** after N days. S, low. Source: [lock-threads](https://github.com/dessant/lock-threads) (365-day default, 50 threads per run, `exclude-any-*-labels`). Fit: opt-in, exclude labels, per-run cap.
19. **Duplicate hints**: search open issues for similar titles and link them. M, low. No primary source found. Fit: comment noise on issues, which have no status comment, so keep it off by default or skip.

## Releases and dependencies

20. **Security relay**: surface Dependabot / secret-scanning alerts in `status --brief`. M, med. Builds on `willow_bot/status.py`. No primary source found; needs extra App permission, so check the installed grant first. Fit: read-only; an alert API 403 means "unknown", not "clean".

## Health and reporting

21. **Weekly digest** per repo: opened/merged/stale/red counts. M, low-med. Builds on the repo-state report. Fit: write it to `status --brief` or the inbox rather than opening an issue in the repo.
22. **Flaky test detection**: a leg red then green on the same sha, counted per test. L, med. Builds on `ci_log.py` and `ci_backfill.py`. No source; per-test names need log parsing.
23. **Conditional requests** (ETag / `If-None-Match`) for per-tick GETs. S-M, med-high. Source: [GitHub best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api) (a 304 costs no primary rate limit; not re-fetched). Note: the "under 1%" figure in `github_app.list_open_pulls` predates #123; the voice step now reads every open PR every tick (`get_pull` plus the label GET, ~24/hr per PR), and items 7-11 add a files read, so the cost scales with open PRs. Fit: cache beside the other tick state; a failed cache read falls back to a normal GET.

## Beyond GitHub: the box, the fleet, the voice

Everything above is about what the bot does on github.com. These are about the rest of it: the steward on the box, the fleet bridge and Grove, the persona counters, Loki, the deterministic runner, and the person running the install. Same rules as above, plus three of its own: **the vault is Sean's key** (no idea here reads, writes, rotates or caches vault material beyond what `credentials.resolve` already does, and none adds a vault key); local files stay inside `$WILLOW_HOME` (`willow_bot/paths.py`); and every feature passes the [LOSC](../losc/MANIFESTO.md) rules: runs offline, no phone-home, deletes clean. External sources are added where a line says Source (fetched this pass); each "builds on" was read in this tree.

### Steward and the box

24. **`status --doctor`**: one command that runs the checks already scattered across the tree (`app-grant`, `ingress`, `tunnel.py` preflight, `scripts/preflight.py`, inbox depth, persona DB readable, operator profile parsed) and prints one fix line per failure. S-M, high. Builds on `status.report()`, whose sources already name their own state. Fit: read-only; "could not check" is its own row, never a pass. Source: [Homebrew/brew](https://github.com/Homebrew/brew) (its README tells users to run and read `brew doctor` first, a one-command health check).
25. **Inbox replay and quarantine**: `steward inbox --replay <work_id>` moves an item back from `consumed/<YYYY-MM>/`; an item that fails to parse twice goes to `webhook_inbox/quarantine/` with the error beside it instead of being retried every tick. S, med. Builds on `steward/inbox.py` (already never deletes). Fit: moves files only, inside the box.
26. **Retention for the local ledgers**: `event-log.jsonl`, `contributions.jsonl`, deposits and `consumed/` grow forever. Monthly roll into `<name>-YYYY-MM.jsonl.gz`, with a `status` line for total size. S, med. Fit: off by default (`retention_months` unset); the deposit hash chain must survive the roll, so a rolled file ends with its last `row_hash` and the next file starts from it. Source: [logrotate](https://github.com/logrotate/logrotate) (time- or size-triggered rotation, compression and removal of log files; it has no hash-chain handling, so the chain carry-over is ours to write).
27. **Deposit chain verifier**: `steward deposits --verify` walks `prev_hash` / `row_hash` and names the first broken row. S, med. Builds on the chain in `deposits.py`. Fit: read-only; report, never repair. Source: [google/trillian](https://github.com/google/trillian) (a Merkle-tree log that serves inclusion and consistency proofs; this is the verify-the-chain idea in its strongest form).
28. **Tick budget and backoff**: record each tick's wall time and GitHub calls in the status receipt; when the rate limit runs low, skip the optional passes (voice, backfill, upstream tracker) before the required ones (inbox, CI filing). S-M, med. Pairs with 23. Fit: the skip is logged in `status`, never silent.
29. **Dry-run tick**: `steward tick --dry-run` runs everything and prints the writes it would make (comments, labels, checks, deposits, Grove sends) without making them. M, high. Builds on the tick's JSON events. Fit: the easiest way for a new operator to trust an install before turning `voice.orgs` on. Source: [hashicorp/terraform](https://github.com/hashicorp/terraform) (its README says the plan "shows what Terraform will do when you call apply", a preview kept separate from applying).
30. **Profile linter**: `steward profile --check` flags unknown keys, typos (`stale_day`), orgs the App is not installed on, and `voice.orgs` that are not in `orgs`. S, med. Builds on `operator_profile.py` and `scripts/list_installations.py`. Fit: read-only.
48. **`doctor` exit codes for timers**: `status --doctor --quiet` exits 0 when every check passes, 1 on any failure and 2 when a check could not run, so a systemd timer or cron line can alert without parsing text. S, med. Builds on 24 and `willow_bot/status.py`. Source: [Homebrew/brew](https://github.com/Homebrew/brew) (`brew doctor` is the same run-and-read check). Fit: prints and exits; the alert is the operator's own timer unit, the bot sends nothing.
49. **Deposit inclusion proof**: `steward deposits --prove <row_hash>` prints the hash path from one row to the chain head, so one deposit can be checked by hand without reading the whole file. S-M, med. Builds on 27 and `willow_bot/deposits.py`. Source: [google/trillian](https://github.com/google/trillian) (returns inclusion and consistency proofs; a per-row proof is the small version of that). Fit: read-only, stdout only; the proof is hashes, no row contents.

### Fleet bridge and Grove

31. **One Grove digest instead of many pings**: batch CI-red and upstream-desk notices into one Grove message per N minutes per channel, with a count and links. S-M, med. Builds on `ci_comments` / `_grove_sender`. Fit: off by default; the per-event path stays for operators who want it.
32. **Gitsync flag receipts**: the push path drops `trigger-<owner>-<repo>.flag`, but nothing says whether the local sync ran. Read back the consumer's receipt (or the flag's age) into `status`: "flag older than 1h, nobody picked it up". S, med. Builds on `fleet_bridge` and `install_receipt.py`. Fit: read-only; an unreadable flag dir is "unknown".
33. **Local event bus**: alongside the inbox files, emit each routed event as one line on a Unix socket (or a named pipe) so other local tools subscribe instead of polling the directory. M, low-med. Builds on the deterministic runner's socket server. Fit: loopback only; the files stay the source of truth.
34. **Upstream desk triage in the terminal**: `willow-bot-steward desk` lists `upstream_steward/pending/*.json` by lane and lets the operator mark one done / snooze / ignore, writing to a local state file. M, med. Builds on `upstream_desk.py` / `upstream_triage.py`. Fit: never posts to GitHub on its own; "reply" opens the URL.
50. **Grove command rights**: if Grove takes commands, each one is denied unless listed for that channel in the operator profile, with admin-only commands kept apart from the rest. S-M, med. Builds on `tick._grove_sender` (`willow_bot/steward/tick.py`). Source: [errbotio/errbot](https://github.com/errbotio/errbot) (per-command admin and user rights, with bot admins managed from a one-to-one chat). Fit: an empty list means no commands are accepted; no new credential, and the vault is not touched.

### Voice and persona

35. **Voice preview**: `willow-bot voice --event pr_merged --login x --sha y` prints what troll / quips / runes / horoscope would say, from the same deterministic picks. S, high. Builds on `troll.py`, `quips.pick`, `runes.cast`, `horoscope.reading`. Fit: no network; the pools in `willow-bot.json` get edited with a preview instead of a live PR.
36. **Pool linter**: check `willow-bot.json` for lines that drop a fact placeholder, duplicates, pools too small to cycle before a repeat, and lines over a length cap. S, med. Builds on the troll's "never repeated in a repo until that pool is spent". Fit: CI-runnable, read-only.
37. **Persona counters export / import**: dump the three SQLite files in `persona/` (titles, sigh streaks, rebase shame) to one JSON and back, so an operator moving boxes keeps their contributors' titles. S, low-med. Builds on `persona_store.py`'s one-time carry-over. Fit: plain data, no credentials in it.
38. **Seasonal / per-repo voice packs**: a second pool file chosen by date range or repo from the profile (`voice.packs`), merged over the default. S-M, low. Fit: off by default; a missing pack falls back to the default pools, never to silence.
39. **Quiet hours**: the voice holds non-essential lines (quips, runes, horoscope) during the operator's quiet hours or on a `willow-bot/quiet` repo topic; status comments and checks still post. S, low-med. Fit: profile key, empty by default.
51. **Voice diff for pool edits**: `willow-bot voice --diff old.json new.json` replays one fixed event list through both pools and prints which picks change, so a pool edit is reviewed before it goes live. S, med. Builds on 35, `troll.py` and `quips.py`. Source: [hashicorp/terraform](https://github.com/hashicorp/terraform) (the plan step is a review kept separate from applying). Fit: reads two local files, no network; the live `willow-bot.json` is never written.

### Loki

40. **Loki dry-run and replay**: run the seven triggers (plus Surfacing) over a saved Grove export and print which would fire, with the context packet, without calling the model. M, high. Builds on `loki/accumulator.py` and `context.py`. Fit: makes thresholds tunable without spamming Grove. Source: [promptfoo](https://github.com/promptfoo/promptfoo) (automated evals of prompts and models that run 100% locally, a replay-then-score loop).
41. **Local model first**: the spec already plans the Ollama swap in `cerebras.py`. Do it as a profile switch (`loki.model_url`), defaulting to loopback, with Cerebras as the opt-in. S, med. Fit: LOSC rule 2; the remote endpoint and its key stay outside the vault unless Sean says otherwise. Source: [ollama/ollama](https://github.com/ollama/ollama) (local REST API for running models, with the example call on localhost:11434; the loopback default here follows it).
42. **Loki silence log**: keep the `SILENCE` answers and the packet hash in a local JSONL, so "Loki said nothing" can be told apart from "Loki never ran". S, low-med. Fit: local only, rolled with 26.
52. **Silence cases in the Loki eval set**: a promptfoo-style file of saved Loki inputs, each marked ANSWER or SILENT, run against the loopback model; a SILENT case that speaks fails the run. M, med. Builds on 40, `loki/context.py` and `loki/accumulator.py`. Source: [promptfoo](https://github.com/promptfoo/promptfoo) (LLM evals that run 100% locally, with Ollama among the providers). Fit: loopback only; case files live in the repo and hold no channel transcripts unless the operator copies one in.

### Deterministic runner and one-script

43. **Fixture recorder**: capture a live chat-op exchange as a new `S-growth-*.json` fixture so regressions come from real use, not hand-written cases. M, med. Builds on `willow_bot/deterministic/chain.py` / `runner.py`. Fit: strip anything credential-shaped before writing; the operator reviews each fixture before it is kept.
44. **Model drift report**: run the fixture set against two local models (or two versions) and diff the outputs. M, low-med. Builds on `runner.py` and `host_probe.py`. Fit: loopback Ollama only. Source: [promptfoo](https://github.com/promptfoo/promptfoo) (side-by-side model comparison with local runs).

### People and onboarding

45. **`willow-bot init`**: an interactive first run that writes `operator.json` from `operator.example.json`, discovering login and orgs (the profile already can), then runs 24 and 29. M, high. Fit: never asks for or stores a secret; it points at where `credentials.resolve` looks and stops there. Source: [cookiecutter/cookiecutter](https://github.com/cookiecutter/cookiecutter) (`cookiecutter.json` declares each variable and its default; users answer prompts and the template does the rest).
46. **`willow-bot-contribute --watch`**: the contribute CLI, run on a timer, with a desktop notification (or a line appended to a local file) when something new waits on you. S-M, med. Builds on `contribute.py`. Fit: the user's own token, never the App's (already the rule there).
47. **Contributor cards**: `contributions --person LOGIN` prints one person's history across repos, their title from `quips`, and first-contribution date. S, low-med. Builds on `contributions.summary` and the persona titles. Fit: local read; nothing posted.
53. **`init --answers <file>`**: replay a saved answers file to set up a second box with the same choices, the way a cookiecutter template reuses its variables. S, med. Builds on 45 and `willow_bot/operator_profile.py`. Source: [cookiecutter/cookiecutter](https://github.com/cookiecutter/cookiecutter) (variables and defaults declared once, answered per run). Fit: the answers file holds choices only; any key that looks like a secret is refused at parse time.

### Pick-up order for this half

24 (`doctor`), 29 (dry-run tick), 35 (voice preview) and 45 (`init`) first: all four are mostly read-only, reuse code that exists, and make the install easier to trust. Then 25, 26, 27 together (the box's own hygiene), then Loki's 40 and 41.

## Next batch (one PR: "PR hygiene checks and opt-in housekeeping")

All eight share the hygiene pass in `voice.py` and the profile settings block, and none needs a new webhook handler.

1. **WIP, title and linked-issue statuses (7, 8, 9)**: three checks through the existing `publish_check`, no comment noise.
2. **Path labels (10)**: small, and the owned prefix keeps it safe.
3. **Large file guard (11)**: reuses the file list fetched for 2.
4. **Conflict line in the status comment (6)**: tiny, reuses the existing label signal.
5. **Stale warn-then-close, warn-only default (15)**: the most common request; builds on the stale label.
6. **Needs-info loop (16)**: closes the issue-side gap with a single owned label.
7. **Auto-update branch (4)**: one API call on a state the hygiene pass already reads; per-PR opt-in label.
8. **Conditional requests (23)**: ETag / `If-None-Match` on the per-tick reads (`get_pull`, the PR's labels, its files) so an unchanged PR answers 304 and costs no rate limit; lands first so 1-3 don't multiply the read cost.

Next PR after that: **slash commands (1)**, since 2, 13 and a rerun command all hang off it.

## Later / maybe not

- **Merge queue and priority override.** Clashes with "never merges on its own", and [bors-ng](https://github.com/bors-ng/bors-ng) (archived 2024) itself points to GitHub's built-in merge queue. Let GitHub run it.
- **Release drafter (grouped notes by label).** Overlaps release-please, which this repo already uses. Source if ever needed: [release-drafter](https://github.com/release-drafter/release-drafter).
- **Dependabot / Renovate shepherd (auto-approve green patch bumps).** An approval from the bot is a trust signal, which clashes with the rules; also heavy. A Renovate-style approval dashboard ([docs](https://github.com/renovatebot/renovate/blob/main/docs/usage/key-concepts/dashboard.md)) is Renovate-specific.
- **Copy labels from issue to PR or to backports** (bedevere). Would copy labels outside the `willow-bot/` namespace; only works with an explicit mapping into it.
- **Rule file for PR conventions** (Danger). Runs in the repo's CI, not in the bot.
- **Duplicate hints (19)**, **flaky detection (22)**: low value or large for now.
- Dropped: "contributor gate for commands". Its only source (a newsletter page) could not be fetched, and the `author_association` gate in 1 already covers it.

## Pitfalls others hit

- **Deprecated tools.** Probot's stale app is deprecated ("use the stale action") and bors-ng is archived, so copy their designs, not their code. [probot/stale](https://github.com/probot/stale), [bors-ng](https://github.com/bors-ng/bors-ng)
- **Cap work per run.** Stale and lock tooling limit actions per run or per hour (`operations-per-run` 30, lock-threads 50 threads, `limitPerRun` 1 to 30 on the old app), so a first run on a big backlog does not hit abuse limits. [actions/stale](https://github.com/actions/stale), [lock-threads](https://github.com/dessant/lock-threads), [probot/stale](https://github.com/probot/stale)
- **Fork permissions.** The labeler README warns that `pull_request_target` can hand a malicious fork PR write access or secrets; webhook-driven bots like this one avoid the problem by never running PR code. [actions/labeler](https://github.com/actions/labeler)
- **Label before merge.** Backporting starts only after merge, so a label added later does nothing. [miss-islington](https://github.com/python/miss-islington)
- **Native settings may already do it.** Branch deletion has a GitHub setting, as noted by [delete-merged-branch](https://github.com/SvanBoxel/delete-merged-branch). Check for one before building (see 14).
