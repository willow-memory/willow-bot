# Ideas: what GitHub bots usually do that willow-bot doesn't (yet)

Drafted 2026-10-11, after #123 (sticky status comment, size/conflict/stale/title labels).
What the bot already has: webhook voice, CI-red filing + deposits, CI backfill,
webhook redelivery, owned `willow-bot/` labels, one status comment + one check run
per PR, first-timer welcome, upstream desk / contributions, audit dispatch, PR assign.

Every idea must stay operator-agnostic: off or empty by default, turned on in the
operator profile, bots matched by `type` never login, labels under `willow-bot/`.

## PR flow
1. **Slash commands** in PR/issue comments (`/retest`, `/label x`, `/assign @y`, `/rebase`),
   gated on `author_association` (OWNER/MEMBER/COLLABORATOR).
2. **Reviewer assignment** from CODEOWNERS or round-robin over a team, skipping the author.
3. **Review reminders**: ping requested reviewers after N days of silence, once per round.
4. **Auto-update branch** when the PR is behind base and green (`update-branch` API).
5. **Auto-merge** opt-in: enable GitHub's native auto-merge on a label, never merge itself.
6. **Conflict nudge**: one comment to the author when `willow-bot/conflict` first appears.
7. **WIP / draft guard**: a check that stays pending while the title says WIP.
8. **Linked-issue check**: warn when a PR names no `Fixes #n` (profile-gated).
9. **Path labels** (labeler-style): `willow-bot/area/<name>` from a glob map in the profile.
10. **Backport**: `/backport release-x` opens a cherry-pick PR.
11. **Branch cleanup**: delete the head branch after merge when it lives in the same repo.

## Issues
12. **Stale close**: after `willow-bot/stale`, warn, then close after a grace period (issues and PRs).
13. **Needs-info loop**: label when a maintainer asks, unlabel when the author replies.
14. **Template check**: flag issues that deleted the template's required sections.
15. **Lock old closed threads** after N days to stop necro-comments.
16. **Duplicate hints**: search open issues for near-identical titles and link them.

## Releases and dependencies
17. **Release drafter**: keep a draft release whose notes group merged PRs by label.
18. **Dependabot/Renovate shepherd**: group, auto-approve patch bumps that are green.
19. **Security relay**: surface Dependabot / secret-scanning alerts in `status --brief`.

## Health and reporting
20. **Weekly digest issue** per repo: opened/merged/stale/red counts (from the repo-state report).
21. **Flaky test detection**: a leg that went red then green on the same sha, counted per test.
22. **Conditional requests** (ETag / `If-None-Match`) so per-tick GETs don't spend rate limit.
23. **Large file guard**: a check that fails when a PR adds a file above a size limit.

## Expansion from the open GitHub world

Research pass 2026-10-11. Probot's app and bors-ng are both deprecated, so copy their designs, not their code. The Kodiak README fetch failed, so Kodiak is not cited. Rows with "none found" had no source in this pass.

### Seen in the wild

| # | Idea | Bot(s) | Design detail to copy |
|---|---|---|---|
| 1 | Slash commands | [Prow](https://www.k8s.dev/blog/2022/12/12/prow-and-tide-for-kubernetes-contributors/), [triagebot](https://forge.rust-lang.org/triagebot/labeling.html) | Role-gated commands. Triagebot's `allow-unauthenticated` list limits non-team users to named labels. Prow treats `/lgtm` as `/approve` for approvers, so write both out. [OWNERS guide](https://www.k8s.dev/docs/guide/owners/) |
| 2 | Reviewer assignment | [Prow OWNERS](https://www.k8s.dev/docs/guide/owners/) | Owners live in per-directory files in the repo, not in bot config |
| 5 | Auto-merge on label | [miss-islington](https://github.com/python/miss-islington), [bors](https://bors.tech/documentation/) | Merges only after a core approval, CI green and an explicit label. Bors `r+` requires approval and green CI. |
| 7 | WIP guard | [semantic-pull-request](https://github.com/amannn/action-semantic-pull-request) | `wip` option and `ignoreLabels`; reports a status, not a comment |
| 8 | Linked-issue check | [bedevere](https://github.com/python/bedevere) | Fails a status and links to the contributor guide when the title has no issue number |
| 9 | Path labels | [actions/labeler](https://github.com/actions/labeler) | `sync-labels` defaults to false; never removes labels it did not add; `max-files-changed` cap |
| 10 | Backport | [miss-islington](https://github.com/python/miss-islington) | Label must be set before merge; on failure it assigns the merger to backport by hand |
| 12 | Stale close | [probot/stale](https://github.com/probot/stale) | `exemptLabels`, `onlyLabels`, `limitPerRun` (1 to 30 per hour); warn before closing |
| 14 | Template check | [Danger](https://github.com/danger/danger-js) | Rules on PR body content, e.g. require a tracker link |
| 15 | Lock old threads | [lock-threads](https://github.com/dessant/lock-threads) | 365-day default, `exclude-any-*-labels`, 50 threads per run |
| 17 | Release drafter | [release-drafter configs](https://deku.posstree.com/share/github-actions/release-drafter/) | Category groups keyed on labels; version resolver from labels |
| 18 | Dependabot shepherd | [Dependabot groups](https://github.blog/2024-04-24-tame-dependabot-group-your-updates-slow-the-cadence-keep-security-fast/) | Group by patterns, dev/prod, or semver level. Security updates stay ungrouped. |
| 22 | Conditional requests | [GitHub REST best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api) | ETag and `If-None-Match`; a 304 does not count against the primary limit |
| 23 | Large file guard | [Danger](https://github.com/danger/danger-js) | Rule-as-code warning when specific files change |
| 3, 4, 6, 11, 13, 16, 19, 20, 21 | — | none found | — |

### New ideas

1. **Merge queue**: batch-test PRs against the latest base before merging. [Mergify](https://docs.mergify.com/merge-queue/setup), [bors](https://bors.tech/documentation/)
2. **Priority override**: a merge command takes a priority so urgent PRs jump the queue. [bors](https://bors.tech/documentation/)
3. **Label aliases**: one command word expands to a set of labels. [triagebot](https://forge.rust-lang.org/triagebot/labeling.html)
4. **Unauthenticated label allowlist**: non-team users may apply only listed label patterns. [triagebot](https://forge.rust-lang.org/triagebot/labeling.html)
5. **News/changelog entry check**: fails when a PR lacks a changelog file, unless it has a skip label. [bedevere](https://github.com/python/bedevere)
6. **Copy labels from issue to PR**: a feature label on the linked issue is copied to the PR. [bedevere](https://github.com/python/bedevere)
7. **Copy labels to backports**: a backport PR inherits its source PR's labels. [bedevere](https://github.com/python/bedevere)
8. **Dependency dashboard approval**: a checkbox list in one issue gates major updates. [Renovate](https://docs.renovatebot.com/key-concepts/dashboard/)
9. **Conventional title lint**: checks type, scope, and required scope, with a status only. [semantic-pull-request](https://github.com/amannn/action-semantic-pull-request)
10. **Rule file for PR conventions**: repo-owned rules (changelog, links, watched files) run in CI. [Danger](https://github.com/danger/danger-js)
11. **Contributor gate for commands**: only prior contributors can trigger bot actions, a GitHub setting one project used against bot spam. [AI Weekly on Archestra](https://aiweekly.co/alerts/archestra-blocks-ai-bot-spam-with-git-contributor-gate)
12. **Category release notes**: draft notes grouped by label, with a catch-all group. [release-drafter configs](https://deku.posstree.com/share/github-actions/release-drafter/)

### Pitfalls others hit

- **Stale bots read as dismissal.** Contributors said auto-closing signals "we don't care", and closes also hit well-documented reports. [lonami.dev](https://lonami.dev/blog/stalebots), [WP Tavern](https://wptavern.com/gutenberg-contributors-consider-implementing-a-bot-to-close-stale-issues)
- **Bot comments with no off switch.** Users asked for a default-off mode with a label to opt in, and Cursor's bot kept commenting after being disabled. [Eclipse Che](https://www.eclipse.org/lists/che-dev/msg03468.html), [Cursor forum](https://forum.cursor.com/t/cursor-is-sending-spam-pr-comments/164916), [SonarCloud](https://community.sonarsource.com/t/disable-github-merge-request-comments/18520)
- **Repeat comments reset others' timers.** A bot re-posting identical notices kept resetting stale-bot clocks, so it now checks for an existing comment first. [Bazel commit](https://bazel.googlesource.com/continuous-integration/+/438f023b193d850461a45b8cb172ef2516cd8133)
- **Rate limits.** Stale and lock bots cap work per run for this reason, and GitHub advises serial writes with about 1 s gaps and honoring `retry-after`. [probot/stale](https://github.com/probot/stale), [lock-threads](https://github.com/dessant/lock-threads), [GitHub best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)
- **Fork permissions.** `pull_request` gives read-only tokens on fork PRs, and `pull_request_target` carries security risk. [actions/labeler](https://github.com/actions/labeler)
- **Labels must exist before merge.** Backporting starts only after merge, so a label added later does nothing. [miss-islington](https://github.com/python/miss-islington)
- **Deprecated tools.** Probot's app and bors-ng are both deprecated, so depending on either is a maintenance risk. [probot/stale](https://github.com/probot/stale), [bors-ng listing](https://awesome.ecosyste.ms/projects/1003085)

### Suggested next batch

1. **Conditional requests (22)**: cuts poll cost for every other feature at low effort.
2. **Path labels with sync off by default (9)**: small, and it never removes labels users added.
3. **Linked-issue and title checks as statuses (8, 7)**: enforced without adding comment noise.
4. **Stale with exempt labels, warn-then-close, per-run cap (12)**: the common ask, but start warn-only given the backlash.
5. **Lock inactive closed threads at 365 days (15)**: low-risk cleanup with a long default.
6. **Auto-merge on label, gated on approval and green CI (5)**: matches the "never merge on its own" rule, since a human still sets the label.
