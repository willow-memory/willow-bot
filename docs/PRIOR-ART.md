# PRIOR-ART freeze list (willow-bot)

Researched 2026-09-12. Core assembly: Apache-2.0 / MIT / BSD only (no LGPL/GPL).

## Freeze (ship against these)

| pick | SPDX | use |
|------|------|-----|
| ~~[gidgethub](https://github.com/gidgethub/gidgethub)~~ | Apache-2.0 | ~~Webhook HMAC + App JWT/install tokens.~~ Considered, not adopted — see **Decisions** below. |
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

## Decisions

### gidgethub — not adopted (2026-09-15)

The 2026-09-12 freeze list picked `gidgethub` for webhook HMAC + App JWT
and install-token issuance. Three months in, `bot.py` and `github_app.py`
ship the same coverage in stdlib + `requests`:

- HMAC verification: five lines with `hmac.compare_digest`
  (`bot._verify_signature`).
- App JWT: eight lines with `pyjwt` (`github_app._make_jwt`).
- Install token: one POST with a cache (`github_app._get_installation_token`).
- Idempotent posts (comments, checks, labels, assignees): plain `requests`
  wrapped by `willow_bot/pr_voice.py`, `willow_bot/pr_labels.py`,
  `willow_bot/pr_assign.py`.

Adopting gidgethub would trade ~30 lines of transparent code for a
dependency with its own release cadence and a sansio/async surface the
rest of the fleet does not use. The webhook receiver is synchronous
FastAPI and the steward is a periodic tick — neither shape asks for
async GitHub calls.

Reconsider only when one of these holds:

- A new caller needs the App's GraphQL surface (gidgethub carries it;
  hand-rolling GraphQL over `requests` is where the line moves).
- A rate-limit backoff / retry contract becomes worth centralizing (the
  three primitives currently each do their own bounded retry).
- The fleet standardizes on async I/O elsewhere — the tick is stdlib
  today but if the sweep grows fanout, async pays for itself.

Closes the gidgethub half of gap `a6c0926d7e83`.
