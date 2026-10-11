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

_Pending: a research agent is filling this in from popular open-source bots._
