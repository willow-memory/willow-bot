# Move / stay / borrow (frozen from codebase-memory graph + plan)

Indexed projects: `home-sean-campbell-github-workshop-willow-bot`,
`home-sean-campbell-github-willow-memory-willow-mcp`,
`home-sean-campbell-github-willow-memory-kartikeya`.

## Dew (Kart until proven)

| Symbol | Where | Decision |
|--------|--------|----------|
| `CommitmentProactiveHook` | `willow-mcp/.../commitments/proactive.py` | **Stay on Kart** |
| `chain_heartbeat` | same | **Stay** — compose only |
| Callers | `willow_mcp.worker.run_worker_daemon` CALLS both | Do not strip in first extract |
| Desk verbs | `commitment_surface` / `acknowledge` | Stay in willow-mcp |

Bot may *read* `commitment_surface` over MCP during prove; it must **not** own the dew clock until a later prove gate.

## MOVE into willow-bot (prove now; delete from mcp after extract)

| Artifact | Graph / path |
|----------|----------------|
| `loki_pr_watch*` / `loki_pass2_*` | willow-mcp `scripts/` (17 graph hits under `loki_pr`) → `willow_bot/steward/` |
| Box wrappers / pid / state | `$WILLOW_HOME` / operator-box |
| Seat `pr-watch` | willows-grove `willow-seat.sh` → package CLI |
| Desk tick `AGENT_LOOP_TICK_PR_AUDIT` | `loki_pr_watch_loop.sh` → `willow-bot-steward` |
| CI deposit writer (§12 middle) | missing; voice `ci_fail` already in `willow-bot.json` |

## STAY in willow-mcp

Gate, store_*, knowledge_*, dispatch_*, commitment_* (interactive), fleet_health, nest_*, tool oracle (repoint phrasings after extract).

## Already in willow-bot (keep / harden)

| Symbol | Path |
|--------|------|
| `bot.webhook` | HMAC ingress |
| `fleet_bridge.handle` | inbox + gitsync trigger flags; `_sender_type` is type not login |
| `github_app` | install tokens; no `GITHUB_BOT_LOGIN` |

## BORROW

Apache-2.0+ prior art (see `docs/PRIOR-ART.md` when freeze list lands). Nestor from Die-Namic-Systems. willow-mcp as MCP client for gated writes during prove.
