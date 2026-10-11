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
23. **Conditional requests** (ETag / `If-None-Match`) for per-tick GETs. S-M, low-med. Source: [GitHub best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api) (a 304 costs no primary rate limit; not re-fetched). Note: `github_app.list_open_pulls` documents its own cost as under 1% of the 5000/hr budget, so measure before spending effort. Fit: cache beside the other tick state; a failed cache read falls back to a normal GET.

## Next batch (one PR: "PR hygiene checks and opt-in housekeeping")

All seven share the hygiene pass in `voice.py` and the profile settings block, and none needs a new webhook handler.

1. **WIP, title and linked-issue statuses (7, 8, 9)**: three checks through the existing `publish_check`, no comment noise.
2. **Path labels (10)**: small, and the owned prefix keeps it safe.
3. **Large file guard (11)**: reuses the file list fetched for 2.
4. **Conflict line in the status comment (6)**: tiny, reuses the existing label signal.
5. **Stale warn-then-close, warn-only default (15)**: the most common request; builds on the stale label.
6. **Needs-info loop (16)**: closes the issue-side gap with a single owned label.
7. **Auto-update branch (4)**: one API call on a state the hygiene pass already reads; per-PR opt-in label.

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
