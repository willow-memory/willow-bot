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

## Pile 2: the same afternoon

*Drafted 2026-10-31, after the first pile was built.* Made the same way, with
more of the bot's own data. The inputs were:

- the 94 open queue items and the 510 open gaps
- the day's `willow-bot` and `willow-bot-steward` journals, turned into dated
  passages by Willow's `logs`
- the almanac catalogs, read by Willow's `almanac`
- the day's `ci_outcomes.jsonl`, replayed through `run_ci` on a scratch home

`box-health` checked the box, and `duplicates` and `contradictions` read it.
`almanac` and the bot's `current` agree: the same 132 entries in 11
verticals, and the same one live entry the link checker couldn't reach
(`noaa-billion-dollar-disasters`).

### 7. A burst of pushes filed false reds (M, high): built, #203

Thirteen "CI red" and "CI stuck" items were filed on almanac `main` heads,
about 80 minutes late. Each one was a run cancelled by the next push, plus its
aggregate `test`, which fails whenever a leg it needs is cancelled. The ci
step read 50 rows a tick, and 2,509 check runs left it ~740 KB behind. Its
"no successor after 10 min" then meant "not read yet". Replaying the day:
before the fix, 33 heads filed and 51 ticks to catch up; after it, 5 heads
filed, all real, and 6 ticks.

### 8. The sweep ignored the clone a flag names (S, med): built, willow-mcp #742

The bot finds `rudi193-cmd/safe-app-store` at `safe-app-store-public` by
scanning remotes and writes that clone into the flag. willow-mcp's
`sweep_triggers` read only the flag's file name, so it refused the flag
ENOCLONE on every tick since 10-20. It is live; the flag was consumed at 17:42Z.

### 9. keep-current can't see the steward's willow-mcp child (S, med)

The steward keeps one stdio willow-mcp child for its whole life. When
willow-mcp moves, keep-current lists no restart for `willow-bot-steward`,
because it checks that unit only against willow-bot's checkout and venv. #742
sat on disk unused until the steward was restarted by hand. *Fix:* count the
venv the child runs from (`WILLOW_BOT_MCP_COMMAND`'s, holding the editable
willow-mcp) among the steward's code, or have `mcp_client` start a new child
when that checkout's HEAD moves.

### 10. A red release on a tag never resolves (S, low)

willow-bot's v1.29.5 `publish` failed with a digest mismatch, and Jeles's
v0.17.0 `publish` failed too. A tag is a branch of its own, so
`superseded-on-branch-green` never fires. *Fix:* resolve a tag's red when a
later tag in the same repo passes the same leg.

### 11. The mirror can't keep up on a busy day (M, low; a decision)

It lands ~17 rows a tick within its 30 calls. Its lag grew from 103 KB at
06:15 to 1.2 MB at 17:04. No code in the fleet reads
`willow_bot_ci_deposits`. *Decide:* drop the mirror, batch it, or keep it and
accept the lag.

### 12. Vulnerability fixes aren't applied (L, high)

Ten "has N vulnerable or yanked package(s)" items are open, across the box,
dev and service venvs. The venv policy (1c, 2026-10-30) says a fix for a
vulnerability is applied at once, and the operator is told afterwards. The
bot only files the items. This is the per-venv lock and build-beside design.

### 13. Deliveries given up during the split route (S, low)

`redeliver` gave up on 90 deliveries after 5 tries each, while one route
answered 404. Those events never reached the bot, and nothing names them.

### 14. To check: the engine fold

The ten "is behind its engine" items fold into one at the next daily copies
check, about 00:02Z on 2026-11-01.

### Not the bot's: found on the way

- **Willow's `logs` wrote mirrors the kernel never reads** (built:
  Willow #126). The steward's day mirrored to 5.9 MB, and the kernel reads at
  most 1 MB from one file. `init` said nothing; `box-health` said "3 files
  can't be read".
- **Willow's `box-health` doesn't say why** a file can't be read: too large,
  not UTF-8, or binary.
- **`duplicates` finds nothing in logs:** every line carries its own time.
  That is not a bug; for logs, ask the kernel or `timeline`.

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
