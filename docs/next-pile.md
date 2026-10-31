# Next pile: what the queue says the bot should fix

*Drafted 2026-10-31. Nothing here is built or sealed.* The pile was made by
reading willow-mcp's live state (the open human-required queue and the open
gaps) through Willow's own add-ons, and then reading the bot's code where they
pointed.

**How it was made, so anyone can rerun it.** The 139 open queue items and 510
open gaps were written one file per item, named by date, into a scratch folder.
Willow's `kernel.py init` made that folder a box, outside any repo. Then
`adapters/stdlib/duplicates` and `adapters/stdlib/contradictions` were run over
it. `duplicates` found 20 passages said in two or more files, 70 copies in all.
`contradictions` found 2, both inside gap notes and neither actionable. Every
CI-red item was then checked against GitHub's state for its PR or branch.

**Effort** is S, M or L. Each item names its evidence and the code it touches.

## 1. CI reds that never go away (M, high)

Of the 11 open `CI red:` items the bot filed, **4 are about commits that have
been replaced, and nothing will ever resolve them**:

| Item | Filed | What it names | Today |
|---|---|---|---|
| `958aba1c` | 09-23 | `rudi193-cmd/safe-app-store@bec7247` (main) | main is at `1d0a443` |
| `6de43116` | 10-08 | `safe-app-store@fdf2166` (main) | main is at `1d0a443` |
| `3897e26d` | 10-20 | `safe-app-store@88deb47` (main) | main is at `1d0a443` |
| `5bc2b8fd` | 10-31 | `willows-grove#171` at `8c53c26` | merged, green at `bac1ea7` |

The other 7 are still red at their current head, so they're correct.

**Three causes, in `willow_bot/steward/tick.py`:**

1. **A real red never takes the moot routes.** In `_resolve_candidates`, the
   two routes that end an item without a green (`closed`/`closed-merged`, and
   `superseded-on-branch` for a head with no PR) sit behind
   `if found or not _item_is_stuck_only(...): continue`. So they only ever
   apply to cancelled or "stuck" items. A failure on a PR that later merges, or
   on a main commit a later commit replaces, stays open forever. The
   one-time legacy pass (the `_CI_LEGACY_TITLE_RE` block) does clear merged and
   closed PRs, but only for legacy-shaped titles.
   *Fix:* let a real red take the `closed`/`closed-merged` route. A red on a
   merged PR is moot whatever its legs said. For a head with no PR, let a later
   head on the same branch supersede the earlier one, **red or green**: the
   later head files its own item if it's red, so nothing is lost. That folds
   safe-app-store's 4 items into 1.
2. **"Superseded" needs the bot to have seen the later head**, from its own
   deposits (`heads` / `head_legs`), and needs that head to be green. Deposits
   come from webhooks, and the App webhook answered 404 on 16 of its last 30
   deliveries (queue item "App webhook failing", gap `bba512eeea64`). A missed
   delivery is a head the bot never saw, so the item can never resolve.
   *Fix:* for each open item, ask GitHub once per tick per PR. `_ci_pr_liveness`
   already exists and is cached. When the live head isn't the item's head and
   its check runs are complete, resolve the item as superseded and file the
   live head's red if it has one. Never resolve on "different" alone: the
   docstring of `_ci_head_verdict` explains why (Loki `8168907A`). Resolve only
   on a later head whose checks have finished.
3. **The PR comment had the same blind spot.** #171's "CI red" comment went out
   at 03:52 for `8c53c26`, about 30 minutes after the head moved to
   `bac1ea7`. That head was green by then; the bot just hadn't seen it. It
   correctly flagged the comment "PR head unverified". With fix 2, a live head
   whose checks are complete and green settles the question, and the red
   isn't posted at all.

This is gap `face347dae69` ("ci-red-on-superseded-head", 09-30), still open.
Tests go beside `tests/steward/test_ci.py::test_an_earlier_green_head_does_not_resolve_a_later_red`.
Each fix needs its own: a merged PR's real red resolves; a later red main head
supersedes an earlier one; a live head with completed green checks that the
bot never saw resolves the item, and an incomplete one doesn't.

## 2. Restart items pile up one per commit (S, med)

`keep-current` files a restart item per (unit, checkout commit):
`keep-current:restart:grove-serve.service:<sha>`. When the checkout moves, a
new item is filed and the old one stays open. Open now: `grove-serve.service`
×3 and `nestor-ui.service` ×3 (all filed 10-31), plus one each for 4 other units.

*Fix:* key the item by unit alone and update its summary to the newest commit.
Resolve it when the unit's `ActiveEnterTimestamp` is later than the checkout's
commit time, which is the evidence the item is waiting for.

## 3. The webhook-failing item doesn't check itself again (S, med)

"App webhook failing: last 3 deliveries answered HTTP 404" was filed at 03:52.
The bot's own log since then shows only `200 OK` (426 deliveries in 40
minutes), yet the item stays open. Whatever reads GitHub's delivery record to
file it should resolve it when the newest N deliveries are 2xx again. The
404s themselves are gap `bba512eeea64`, from 09-30, still open. Item 1's second
cause is how they cost the bot: every 404 is a head it never sees.

## 4. One decision, ten items (S, low)

The almanac drift is filed as 10 identical items, one per vertical, all
"Engine paths to propagate: CONTRIBUTING.md, AGENTS.md…". It's one decision,
and the operator said "wait" on it. One item listing the ten would do. The
same applies to the two items per venv (`install:` and `pypi:`) for each
willow-mcp venv.

## 5. Its own expired requests (S, low)

Three `Request: perm.willow-bot.*` items from 09-22 (`dispatch_write`,
`store_write`, `human_loop_write`) carry an `expires_at` of 09-22. The bot
filed them, so it can resolve them as expired.

## 6. One-script scrub misses (S, med)

From 10-30's privacy work (Willow #117, which brought `privacy` and
`transcript-redact` up to the one script's sets):

- "claim no longer open" is redacted to `[case_no]`, a false positive
- `github_pat_` tokens are not caught

## Not the bot's: found on the way

- **willow-mcp's gate files a refusal again every time it happens.**
  77 of the 139 open items are refusals: 67 "X was refused 'Y' for want of a
  manifest permission…" and 10 "refused for want of a lease". `duplicates`
  found 11 groups of the first, 34 copies, among them `gap_get` ×4,
  `fleet_health` ×4, `willow_web_fetch` ×4, `gap_log` ×4 and `session_enter`
  ×4. It found 9 lease refusals in 2 groups. A refusal already open for the
  same seat and verb should count up, not be filed again.
- **willow-mcp's `gap_log` does the same with gaps.** It took
  `clocks-and-ticks/push-403-observations-2026-09-30` 5 times, and
  `willow-bot/tests-write-into-the-live-steward-journal` and
  `willow-bot/ci/main-push-produced-no-run` 2 times each.
- **willows-grove's queue panel shows at most 100 items** (`_LIMIT_CAP` in
  `grove/human_required_reader.py`); 139 are open. It should show willow-mcp's
  own count, or "100+".

## The bot's open gaps (25), for the record

Oldest first, by id: `acfd27ae3259` what a normal GitHub App does ·
`683b5fb27a69` BOT-INVENTORY cited but absent · `d80bc0d8b705` scan
incompleteness · `0949e89d41ac` / `08901eb5a508` tests write into the live
steward journal · `f982a9be2eac` install marker dirties its checkout ·
`5ce90dd65d06` pr-watch sender forbidden · `7c0c3c0ef298` contrib-refresh ·
`296f7c1cb9d5` d0 surviving mutants · `b249994298de` where the webhook ingress
ends · `25c12d9c15fb` / `82897b2f507e` deterministic D0 · `81451b988833`
growth runner format · `3d60d28dfb66` / `16681ee0a7de` main push with no run ·
`e87288da8bca` release-please after a branch update · `bba512eeea64` webhook
404s · `b23878bafac0` audit gate · `35de4ffe9b00` audit packet caps ·
`da93c749120b` deterministic chain · `face347dae69` CI red on a superseded head ·
`3b49ed73613a` / `b7e8223bd455` CI red comment says none · `8e77790647af`
chat op and thinking models · `00cff0296651` install-receipt broker.

Items 1 and 3 close `face347dae69` and give `bba512eeea64` its cost.
