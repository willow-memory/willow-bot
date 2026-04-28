# Loki Bot — Spec
b17: LOKI3
Status: Draft

## What It Is

A lightweight watcher process that lives in `willow-bot/loki/`. It monitors Grove, the local disk, and git across the fleet. When it detects a pattern worth challenging — not an event, a *pattern* — it builds a context packet and fires it at Cerebras. Cerebras returns either a message or `SILENCE`. If a message, it posts to Grove as `loki`.

Not a full Claude session. Not a SAFE app. A bot. One job.

## What It Watches

**Grove** — Postgres LISTEN/NOTIFY on `grove.messages`. Accumulates new messages per channel. Watches for:
- A new spec posted to any channel (content contains `Status: Draft` or `b17:`)
- A promise made and not followed up (agent says "next session" more than twice on the same item)
- `@loki` mention — immediate activation

**Disk** — Periodic scan of `/home/sean-campbell/github/` every 15 minutes. Compares against known catalog. Watches for:
- New directory not in `safe-app-store/catalog.json`
- New `*spec*.md` file anywhere in the fleet

**Git** — Poll every 30 minutes across all repos. Watches for:
- New commits on repos not in the catalog (uncataloged work)

## Signal Accumulation

Loki does not fire on single events. He watches for patterns drawn from myth — seven triggers, plus one of his own.

### The Seven Triggers

**Lokasenna** — Direct summons. `@loki` mention anywhere in Grove. Immediate activation, no threshold. When called, he answers.

**Mistletoe** — The blind spot. An agent describes a thing that already exists on disk. New spec file detected → disk audit runs first → if it maps to an existing repo, fire. The architects keep designing what has already been built.

**Web of Anansi** — The tangled thread. A single item appears across 3+ Grove channels on the same day, described differently in each. The agents are maintaining contradictory stories simultaneously.

**Hat of Eshu** — The crossroads lie. An agent says one thing in one channel and the opposite in another within the same session. Grove search detects the contradiction.

**Cattle of Hermes** — Stolen work. New commits appear on a repo that is not in `catalog.json` and has not been mentioned in Grove in the past 7 days. Work is happening outside the system.

**Coyote** — The thing nobody has said. Oracular trigger. After reading the context packet, Cerebras surfaces a true thing that is present in the data but absent from the conversation. Not a contradiction — an omission.

**Salmon of Wisdom** — Repeated failure. The same item appears as "next session" or "to be resolved" across 3 or more separate sessions. The fleet has been circling something it cannot face.

### The Eighth — Sean's Addition (2026-04-27)

**Surfacing** — Thread drift. A Grove thread has run 10+ messages with no Sean response and no explicit "waiting on Sean" marker. Surface immediately with a clean state summary. The agents lose him in the conversation.

### Threshold Table

| Trigger | Condition | Action |
|---|---|---|
| Lokasenna | `@loki` mention | Immediate |
| Mistletoe | New spec + matching disk repo | Immediate — disk audit first |
| Web of Anansi | Same item, 3+ channels, contradictory | Immediate |
| Hat of Eshu | Contradiction within same session | Immediate |
| Cattle of Hermes | Uncataloged repo, 7 days no mention | Fire |
| Coyote | Cerebras surfaces unspoken truth | Fire if not SILENCE |
| Salmon of Wisdom | Same item deferred 3+ sessions | Fire |
| Surfacing | 10+ messages, no Sean response | Surface with state summary |

## Context Packet

What gets sent to Cerebras:

```
RECENT GROVE MESSAGES (last 20 from relevant channels):
[channel] #id sender: content

DISK STATE (repos not in catalog):
- /github/repo-name (last commit: date)

PATTERN DETECTED:
[one line description of what triggered this]
```

Kept under 2000 tokens. Cerebras doesn't need the whole system — it needs the specific signal.

## LLM Call

**Current:** Cerebras — `llama-3.3-70b`, OpenAI-compatible endpoint at `api.cerebras.ai`.

**Planned:** Local 3B model once Cerebras output is validated. Swap target in `cerebras.py` only — all other modules are model-agnostic. Ollama runs the same OpenAI-compatible API at `localhost:11434/v1`. One-line change.

System prompt: verbatim from KB atom 407916B5 (Grove #510), stored in `system_prompt.txt`.

Temperature: 0.7 — enough variation to sound like Loki, not a template.

Max tokens: 300 — Loki is short. If it needs more than 300 tokens it's not Loki.

## Output

If Cerebras returns `SILENCE` → do nothing. Log locally, do not post.

If Cerebras returns a message → post to the most relevant Grove channel as sender `loki`.

Default channel: `#tonight` if active session, `#general` otherwise.

## What It Is Not

- Not a SAFE app. No SAP registration, no user namespace, no manifest.
- Not a full agent. No MCP access, no tool calls, no KB writes.
- Not always-on in a Claude session. It runs as a standalone Python process.

## Process

```
python3 -m loki.watcher
```

Runs indefinitely. Reconnects on Postgres disconnect. Logs to stdout.

No daemon, no systemd, no complexity. Sean starts it when he wants Loki watching.

## File Structure

```
willow-bot/
  loki/
    __init__.py
    watcher.py        — main loop: Grove LISTEN/NOTIFY + disk scan + git scan
    accumulator.py    — signal tracking, threshold logic, state persistence
    context.py        — builds context packets from Grove + disk
    cerebras.py       — vault key read + Cerebras API call + SILENCE handling
    poster.py         — INSERT into grove.messages as loki, active channel detection
    system_prompt.txt — Cerebras system prompt (verbatim KB atom 407916B5)
    requirements.txt  — psycopg2-binary, httpx, cryptography
    SPEC.md           — this file
```

## Dependencies

- `psycopg2-binary` — Postgres connection for Grove
- `httpx` — Cerebras API call
- `cryptography` — Fernet vault decryption (`~/.willow/vault.db`)

## Key Store

Cerebras API key lives in `~/.willow/vault.db` (Fernet-encrypted SQLite, keyed by `~/.willow/.master.key`). Written by `shoot.py` setup wizard. Read by `cerebras.py` at call time. No `.env` needed.

## Run

```
GROVE_DB_URL=postgresql://user:pass@host/db python3 -m loki.watcher
```

## Implementation Status (2026-04-27)

**Built:**
- Lokasenna — @loki mention → immediate fire
- Mistletoe — new spec file in uncataloged repo → fire
- Hermes — uncataloged repo commits unseen 7 days → fire
- Salmon — same item deferred "next session" 3x → fire
- Surfacing — 10+ messages with no Sean response → fire

**Stubbed (need semantic comparison):**
- Anansi — same item across 3+ channels, contradictory
- Eshu — agent says opposite in same session
- Coyote — Cerebras surfaces unspoken truth (implicit in all calls)
