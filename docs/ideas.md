# willow-bot — idea pile

Scratch ideas for the steward and its **willow-mcp broker** seams. Same shape
`willow-reconciler` reads when commits carry `Idea-Id: willow-ideas-NNN` trailers
(see `willow_bot/steward/tick.py` resolve step).

Legend: ✅ shipped · 🟡 partial · (untagged) proposed

**Numbers are permanent join keys** — never renumber; retire and leave a gap.

Mirror copy for Nest handoff: `~/Desktop/Nest/upstream-seat/willow-bot-broker-notes.md`.

Snippets below are marked **excerpt** (copied from the file named, as read
2026-10-21) or **sketch** (proposed shape, not in any code yet).

## Index

| # | Idea | Snippet |
|---|---|---|
| [1](#idea-1) | `git_pull_execute` is a server-side lane | runs in willow-mcp with the App token; no `integration_call` gate |
| [2](#idea-2) | 🟡 `pip_sync_execute` broker verb | host-side venv sync, fixed paths and hosts, FRANK receipt |
| [3](#idea-3) | Policy fork for pip sync | receipt-only for known reinstalls vs an envelope when the allowlist widens |
| [4](#idea-4) | PyPI credential hygiene | read token in the broker only; publish token never on the install path |
| [5](#idea-5) | Steward install via the broker | `run_install_receipts` → `pip_sync_execute` when the checkout is clean |
| [6](#idea-6) | Orchestrator egress parity | honor the willow app lease on `# allow_net` hygiene tasks |
| [7](#idea-7) | Deterministic inbox serve, Willow only | pile repeats into `×N`, serve new/changed piles whole, never cut a field |
| [8](#idea-8) | Seed pool stays open | the pool grows every add; the peak is computed when looked at and at the stop |
| [9](#idea-9) | Code first, then one small piece | D0 tables what it can; a model gets one turn, never the whole file |
| [10](#idea-10) | `escalate` as a broker step, cut by structure | the chunker can't run headless, and its cut is by size, not meaning |
| [11](#idea-11) | The store key is the first gate on this box | the pile returns 0 rows and a 42: "no store key in the operator's keys dir" |
| [B](#b-specs-tracked-elsewhere) | Specs tracked elsewhere | contrib refresh, upstream desk |

---

## A. Broker lanes (willow-mcp holds credential; bot stays propose-only)

Evening box-sync (2026-09-22): fleet **git pull** worked via `git_pull_execute`;
Kart **pip** did not (sandbox has no PyPI). These items separate “bot App token
+ broker verb” from Kart `# allow_net` and from `integration_call`.

1. <a id="idea-1"></a>**Document `git_pull_execute` as server-side lane, not integration egress.**
   Runs in willow-mcp (not Kart); willows-bot installation token; ACL +
   ff-only/clean-tree guards; FRANK `git_pull` receipt. Does **not** use per-pull
   `integration_call` three-key gate or Nestor-sealed Kart tasks. (Note only —
   implementation lives in willow-mcp `pull_executor.py`.)

   excerpt — willow-mcp `pull_executor.py` (`execute_pull`):
   ```python
   fetched = _git(path, *cfg, "fetch", "--prune", remote, runner=runner)
   ...
   if facts["branch"] != branch:
       switched = _git(path, "checkout", "-q", branch, runner=runner)
   merged = _git(path, "merge", "--ff-only", target, runner=runner)
   ```

2. <a id="idea-2"></a>🟡 **partial**: Broker verb `pip_sync_execute` / `venv_sync_execute` (willow-mcp).
   Host-side sync for vault venv + allowlisted `-e` / `-r` paths — parallel to
   pull, **not** a `pypi` row in `integration_call`. Bounds: fixed venv paths,
   fixed index hosts, no free-form package names from agent prose. FRANK receipt
   (requirement hashes, optional freeze snippet) for `unit_reload_execute`
   anchor. Built: `pip_sync_executor.py` + tool + steward_sweep ACL; steward
   `install_receipt` falls back to broker when checkout `.venv` is absent.

   excerpt — willow-mcp `pip_sync_executor.py` (signatures only):
   ```python
   def _checkout_under_roots(checkout: Path, *, github_root: Path) -> bool:
   def _requirement_hash(checkout: Path, extras: list[str]) -> str:
   ```

3. <a id="idea-3"></a>**Policy fork for pip sync:** receipt-only for known editable reinstall +
   named requirement files (like pull), vs bounded envelope when allowlist
   widens (supply-chain risk higher than ff-only git).

   excerpt — the bar pull sets (`pull_executor.py`): it refuses rather than merge.
   ```python
   return _refuse("EDIVERGED", f"{branch} is {ahead} commit(s) ahead of {target}"
                  ... "; a pull that would need a merge or a reset is not performed")
   ```

4. <a id="idea-4"></a>**PyPI credential hygiene on the box:** wire **read** index token into broker
   only; never route **publish** / twine token into install path. Vault/env/
   `~/.pypirc` — receipts and logs must not echo secrets.

   excerpt — how pull already keeps its token out of config (`pull_executor.py`):
   ```python
   def _fetch_config_for_app_token(token: str) -> list[str]:
       """... the token is on argv for one subprocess and never in the
       remote URL or the config file."""
   ```

5. <a id="idea-5"></a>**Steward hook:** after broker verb exists, should `run_install_receipts` call
   `pip_sync_execute` instead of local `.venv/bin/pip` when checkout is clean?
   (Today: install often skipped when no venv — see steward_ticks `install skipped`.)

   sketch:
   ```python
   if checkout_clean and not (checkout / ".venv").is_dir():
       receipt = broker.pip_sync_execute(checkout=checkout, venv="willow-bot")
   ```

6. <a id="idea-6"></a>**Orchestrator egress parity (willow-mcp / Kart policy, not bot repo):**
   standing `net.egress` envelope (`c9ddd197`); honor active willow app lease on
   `# allow_net` box-hygiene tasks vs always `held_net_authorization`.

   excerpt — the Kart sandbox manifest every task reports today:
   ```json
   "allow_net": false,
   "network_mode": "isolated",
   "allow_localhost": false
   ```

---

## B. Specs tracked elsewhere

- **Upstream contrib refresh** — full spec in [SPEC-contrib-refresh.md](./SPEC-contrib-refresh.md); Nest deposit `upstream-seat/contrib-refresh-spec.md`.
- **Upstream desk** — prototype in workshop clone; Nest `upstream-desk-spec.md`.

---

## C. The one script's serve (desk session 2026-10-21)

Sealed ruling behind these items: Nestor pair `5e8e3683` — *"the seed pool is
multiplication, and 42 is the addition … The addition can come from anywhere …
it's only a human that can say this is right, or at least as right as I know."*

7. <a id="idea-7"></a>**Deterministic inbox serve, Willow seat only.** Today the prompt hook serves
   up to five raw Grove inbox lines by arrival order, cut mid-field
   (`rungs=openrouter/n…`), repeats stacked (`assignment_tampered` ×5 in one
   block), stale CI red for merged PRs, then "…and N more". Proposed:
   pile by `(sender, kind, subject, reason)` with a count and first/latest id;
   serve only since the cursor; a new pile or a changed state is **addition**
   (served whole, a 42 for the human), a pile that only grew is
   **multiplication** (one `×N` line); drop CI already settled; canonical order
   (additions, then kind, then subject); whole fields or none; three states.
   Served to the **willow** seat only — other seats get signed dispatch packets,
   never inbox chatter, and small models never see it. Open: should CI leave the
   inbox entirely and stay with `bot_status`'s open-PR reconcile?

   excerpt — as served today (five lines, one block):
   ```text
   [grove #willow loki] [ratatosk] wake trace=tr-3a2b8df1 entry refused: session_enter refused: assignment_tampered
   [grove #willow loki] [ratatosk] wake trace=tr-6b49e851 entry refused: session_enter refused: assignment_tampered
   ... (×5)
   … and 45 more — next prompt, or grove_inbox(since_id=135632)
   ```
   sketch — the same block, piled:
   ```text
   + loki wake  refused  assignment_tampered  ×5   (new since 135632)
   ```

8. <a id="idea-8"></a>**Seed pool stays open: live pool, on-demand peak.** Overturns "the first
   bite seeds once" (`api.seed`). Each seed word unions into an open pool held
   in plaintext while open (the record keeps word hashes, and the store ranks
   from plaintext); the pool grows every add (multiplication); the peak (rank,
   the next chunks) is computed when looked at (`scope`/`serve`) and at the stop,
   not every add; the stop is checkout or an interrupt, which closes and hashes
   the pool and runs the reverse pass. Both cadences spiked on
   `feat/seed-pool-live` / `feat/seed-pool-ondemand` (throwaway): pools grew
   identically (2→4→7), only the rank cadence differed. Not built.

   excerpt — the gate it overturns (`one-script/onescript/api.py`, `seed`):
   ```python
   if first is not None:
       why = f"this check-in is already seeded (row {first['n']}); the first bite seeds once"
   ```
   excerpt — the spike's accumulation (`feat/seed-pool-ondemand`):
   ```python
   ws = sorted(set(_prior) | set(_added))
   _pool_path.write_text(_json.dumps(ws))
   ```

9. <a id="idea-9"></a>**Code first, then one small piece — never the whole file to one model.**
   `rat_turn` hands the whole served file to one model in one call
   (`ratatosk --onescript --served served.json`). The box's own desk rows show
   how that goes: 2026-10-08 *"who-pile join proposed to qwen3:4b (returned
   silent)"*; gap `cc47032868c6` (qwen3:4b `done` in 74 s with 0 proposals;
   qwen3.5 hit Rat's wall clock); and a pooled pass that invented "port 8767 is
   Tomcat's" (it's Grove's serve port). Proposed: D0 code reads the served rows
   first and tables what it can count, no model —

   | What the desk rows show (sealed scope `serve:ab467c43…`, 11 rows) | Count |
   |---|---|
   | Model turns that came back silent | 1 |
   | Proposals the door caught (`link_fail`) | 1 |
   | Passed the door, still unsealed (since 10-11, 10-15) | 2 |
   | Sealed by a human | 0 |

   — and only then hands a model one small piece (one turn), so nothing waits
   on a timeout. The counting is multiplication; what it means is the human's.

   excerpt — the whole-file call (willow-mcp `onescript_executor.py`, `rat_turn`):
   ```python
   return {"argv": [str(ctx.rat), "--onescript", "--served", str(ctx.served),
                    "--out", str(ctx.proposals), "--model", model, task],
   ```
   excerpt — one of the desk rows it would count:
   ```json
   {"what": "proposal", "verdict": "link_fail", "reason": "a cite is not in the served file", "where": "who-ran-it"}
   ```

10. <a id="idea-10"></a>**`escalate` as a broker step, cut by structure.** `escalate` is the one
    script's chunker (D0 first; one piece per table, a table over
    `PIECE_CHARS = 6000` cut into row groups; hash-reuse; leftovers to one human
    card), but it's CLI-only — `onescript_run_execute` has no `escalate` step, so
    the desk can't drive it headless. And its cut is by size: the 11-row desk
    table is one piece, the same single chunk. Proposed: an `escalate` broker
    step, and a cut by structure (one piece per turn: open → proposal → close),
    per the sealed *"give them smaller pieces"* (a9a14ce7).

    excerpt — the step table, no `escalate` (willow-mcp `onescript_executor.py`):
    ```python
    TIMEOUTS = {"keys_export": 120, "checkin": 300, "scope": 120, "seal": 120,
                "serve": 120, "rat_turn": 900, "turn": 120, "checkout": 120,
                "pooled": 120}
    ```
    excerpt — the size cut (`one-script/onescript/escalate.py`, `cut`):
    ```python
    if cur and len(canon(_piece_doc(doc, t, trial))) + len(question) > budget:
        groups.append(cur)
    ```

11. <a id="idea-11"></a>**The store key is the first gate on this box.** Asked "ideas to be added to
    the bot" through the prompt path (on a copy of the live box, its own
    `.flow/onescript-keys`), the one script returned **0 tables, `state:
    empty`, code 42** and said why: no store key. That one missing key explains
    the day's empties — the seed spikes' `unreachable` peak, the copied-box runs
    with no rank, and a prompt hook that, even if it runs, would serve this same
    empty block. Only 2 rows stand waiting to be added (`pooled`), and neither
    holds up. Smallest bite: put the store key in place, rerun the same prompt,
    read the next `why`. Behind it, unverified until the key exists: whether a
    local store is built at all (the 20-repo store was the cloud container's),
    whether the pile's `xref/index.json` matches its pointer, and whether
    `repos.json` names the repos. One gate at a time; same input, same check.

    excerpt — what the prompt path served (2026-10-21):
    ```json
    {"code": 42, "state": "empty", "tables": [],
     "why": "no store key in the operator's keys dir"}
    ```
