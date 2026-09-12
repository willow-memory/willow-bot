# PRIOR-ART freeze list (willow-bot)

Researched 2026-09-12. Core assembly: Apache-2.0 / MIT / BSD only (no LGPL/GPL).

## Freeze (ship against these)

| pick | SPDX | use |
|------|------|-----|
| [gidgethub](https://github.com/gidgethub/gidgethub) | Apache-2.0 | Webhook HMAC + App JWT/install tokens (`sansio` / `apps`). FastAPI stays the shell. |
| [fastapi-githubapp](https://github.com/primetheus/fastapi-githubapp) | MIT | Pattern reference only — do not hard-depend. |
| [ghapi](https://github.com/AnswerDotAI/ghapi) | Apache-2.0 | Checks/PR REST for deposits; persist pass **and** fail. Webhook-first, poll for catch-up. |
| [APScheduler](https://github.com/agronholm/apscheduler) | MIT | Optional named interval job for steward; else stdlib loop (already in `willow_bot.steward.tick`). |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | MIT | ClientSession from long-lived steward → willow-mcp (prove phase). |
| [llm-audit-trail](https://github.com/victorojewale/audit-trail-PoC) | Apache-2.0 | Append-only draft deposit / hash-chain pattern — wrap, never self-seal. |
| [admission-gate](https://github.com/ak-skwaa-mahawk/admission-gate) | MIT | Propose → human → audit JSONL shape (alt for E). |
| [arq](https://github.com/python-arq/arq) | MIT | Optional process-split pattern only if Redis already accepted. |

## Skipped

PyGithub (LGPL), Celery/RQ stacks, Probot/Octokit as Python core, FastMCP (server), immature github-app-kit.

## Notes

- HMAC over **raw** body before JSON parse (already in `bot.py`).
- Dew clock stays on Kart (`CommitmentProactiveHook` via `run_worker_daemon`) until proven — not in this freeze.
- Prefer official MCP SDK for prove-phase tool calls.
