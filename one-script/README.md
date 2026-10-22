# The one script

The code drives; the model reads what it served; the human seals. This is the
skeleton: the seven parts (boot, predict, record, gate, resolve, view,
reverse), `run`, serve, xref, the function API, and the scripts they lean on.

Moved here from willows-grove (`docs/design/one-script/` at `008c7f9`) under
D2, sealed 2026-10-02: "All is the things should live inside the bot." Its
history stays in willows-grove, and so do the design docs, which are the
reasoning behind every part: the one-script README, `next-pile.md`,
`workflow.md`, `incoming/`, and `../one-box/`.

It sits outside the `willow_bot` package on purpose: it's a proposal, not a
sealed build, so it isn't in the wheel and moving it cut no release.

## What's here

| Path | What it is |
|---|---|
| `onescript/api.py` | The function API the app calls: check-in, scope, seal, serve, proposals, check-out |
| `onescript/` | The parts, `run`, serve, xref, proposals, nestor_seal, escalate, and the CLI (`__main__.py`, a thin wrapper over `api`) |
| `onescript/jeles_store.py` | Jeles's verified corpus as the pile's escalation rung: asked at seed only when the pile doesn't hold the bite, then proposed, sealed and served like any chunk |
| `onescript/tests/` | Its tests |
| `prompt.py` | The served tables, put into a prompt as framed data, so the model never holds a path |
| `scripts/prompt-hook` | Claude Code UserPromptSubmit: every prompt a bite, served into the turn (`onescript prompt`) |
| `foundation/` | `prompt.py`'s tests run on a bare interpreter, without pytest |
| `scripts/scan/` | `script_match.py` (the gate's script index) and the table scans |
| `scripts/flow/` | The view drafts: session maps and their merge |
| `onescript/oauth_resource.py` | `bind()`: every OAuth code and token carries the resource it was issued for, so a server can turn on `validate_token_resource` without refusing its own tokens. Not wired into Grove or willow-mcp yet |
| `scripts/oauth/oauth_probe.py` | Deterministic OAuth: blind wagers forecast before they run, pins for answers already seen; the live flow against willows-grove and willow-mcp serve mode on loopback and Google's hosted MCP servers (read-only, no credentials), N runs each; disagreeing runs are `drift`, a pin that moves is `changed` |
| `deep_thought.py` | The morning screen |
| `kernel.py` | The smallest script for anyone: the whole loop on their own folder, stdlib only (IDEAS 113) |
| `kernel_vectors/` | `kernel.py` in any language: generated tables, 4,175 cases, `compare.py` (every box under every implementation), and three ports: Java, TypeScript, C# |

## OAuth: which server a token is for

Found 2026-10-21 by `oauth_probe`, run on this box: Grove accepts an access
token minted for another server (`g.audience.foreign_resource`: accepted).
The MCP SDK's `validate_token_resource` would refuse it, but neither Grove's
provider nor willow-mcp's records a token's resource, so turning the setting
on alone refuses every token, the honest ones too (tried on a throwaway Grove
copy: `g.mcp.with_token` went to 401).

`onescript/oauth_resource.py` is the fix as an addition here, not a patch
there. `bind(provider, resource=..., legacy_until=...)` stamps the resource on
the code, the access and refresh tokens, and every refresh. Wired into a
throwaway Grove copy with the check on, five runs: the foreign token is
refused, the honest one still works, every other pin and wager holds, no
drift. Its tests run on the real `GroveOAuthProvider`.

Open, for the operator (G1 to G4 in the module):

| Gap | What's left |
|---|---|
| G1 | Tokens minted before binding: refuse now (`legacy_until=None`) or honour for a window. No default, on purpose |
| G2 | A foreign resource is bound and refused at the bearer check; refusing it at authorize (`invalid_target`) isn't done |
| G3 | Grove advertises `/mcp` and serves `/`; willow-mcp advertises `/` and serves `/mcp` |
| G4 | Neither server has a revocation endpoint (Google's does) |
| Wiring | Two lines in each server, beside the provider: `bind(...)`, then `validate_token_resource=True` |

After wiring, `oauth_probe` should show exactly one pin moving,
`g.audience.foreign_resource` from `accepted` to `http_401`; re-pin it then.
Compared against Google's hosted Gmail, Drive and Calendar MCP servers the
same night: they serve `initialize` and `tools/list` with no token and
challenge per tool; both of ours refuse at the door. Google still allows
`plain` PKCE; ours don't.

## The law comes from willows-grove

The one script's law is willows-grove's constitution (its Trace IDs), and its
toolchain pin is willows-grove's CI. willows-grove is found at
`ONESCRIPT_GROVE`, else beside this repo (`../willows-grove`). Without it
there's no law, and every command refuses.

## The seat is Rat

The model runs in Rat (willow-memory/ratatosk, `--onescript`), not in a Docker
container. The container seat, its hook, its managed settings, its pinned
doors and the doors gate are gone (2026-10-07, the operator: "use Rat, drop
docker, make the Write a proposal"). What the seat enforced moves to Rat; what
this repo still owns is what the model is shown and what becomes of what it
returns.

`prompt.py` keeps the three-state rules and the tests that pin them, so Rat
and this repo agree on what the model is shown. Rat owns them from here:

| What `prompt.py` finds | What the model gets |
|---|---|
| A served document | The document, inside the `<served-data>` frame |
| Serve's own `empty` | That, with serve's reason |
| A scope whose text passes the caller's cap | `empty`: "narrow the stack". Never truncated |
| No served file, a missing or unreadable one, or one that isn't a served document | `unreachable`, with a reason that names no path |
| An error in `prompt.py` itself, or a cap that isn't a positive whole number | Exit 2: the caller blocks the prompt |

The model never sees a path, so it never learns that a box or a file exists.

## Every prompt is a bite

Back in a Claude Code seat (2026-10-19, the operator: "Build it, and in
leaning towards the bot"). On 2026-10-07 the container seat and its hook went
with Docker, and the per-prompt pass moved to Rat with the seat. A Claude Code
session outside Rat then had no per-prompt pass at all. It has one again:

- `.claude/settings.json` (committed; the rest of `.claude/` stays local) runs
  `scripts/prompt-hook` on UserPromptSubmit, with a 60 s timeout.
- The hook runs `python -m onescript prompt`: the prompt arrives on stdin and
  is looked up in the box, so the record gets one `lookup` row holding the
  bite's hash, never its words, and argv never carries them. Its top chunks
  are re-read at their indexed blobs and served untrusted, the document marked
  `unsealed: this session`.
- `prompt.py`'s frame puts it into the turn: the one frame Rat uses too, so
  the two seats can't drift in what the model is shown.
- Three states, never collapsed: no open check-in is `unreachable` (and a
  `refused` row), a bite that finds nothing is `empty`, over the cap is
  `empty` ("narrow the stack"). A crash exits 2, which blocks the prompt.
- The box is willow-bot's `.flow/onescript/` unless `ONESCRIPT_BOX` and
  `ONESCRIPT_KEYS` name another. The cap is `ONESCRIPT_MAX_CHARS`.
- The interpreter is `ONESCRIPT_PYTHON`, else the bot's venv found the way
  the one script's own `--venv` default finds it (`_bot_venv`, D2):
  `$WILLOW_HOME/willow-bot/venvs/willow-bot`, or the old
  `$WILLOW_HOME/venvs/willow-bot` while only that one exists (said on stderr),
  `$WILLOW_HOME` defaulting to `~`; else `python3`. A test holds the script
  and `_bot_venv` to the same answer.
- The embedder rung (the pile's rung 3) fuses in when `ONESCRIPT_EMBED` (or
  `--embed`) names WordLlama's local file dir: the words' top `EMBED_POOL`
  (100) candidates, re-read at their indexed blobs, ranked by the embedder and
  fused by RRF, the way `pile.score_grams` fuses. Install it with
  `pip install -e ".[embed]"`; the model is the one WordLlama bundles, never
  downloaded (its tokenizer config is copied into the cache dir, since 0.4
  ships it where its own lookup doesn't look). The served document's `rung`
  says `fused` or `words`, and why. The lookup row still keeps SEED_TOP.
- Cost: about 11–14 s a prompt on a warm store, about 1 s more with the
  embedder. The check-in stays once a session.

Said, not hidden: `prompt.py`'s frame opens with "data the human's sealed
scope allows you to read". A per-prompt document is not sealed; its own
`seal` field says so. The frame is Rat's contract and pinned by its tests, so
its wording is left for the operator to change.

## The Write is a proposal

The model has no Write. It returns rows, and Rat's build B writes them as a
JSONL file, one object per line, with exactly these keys:

```json
{"path": "notes/a.md", "data": "the file's text", "cites": ["<served table id>"], "claim": "why"}
```

`turn --proposal f.jsonl` (or `api.take_proposals`) reads each row as data and
sends it through the door and the served cites. Nothing is written.

| What a row is | Verdict, recorded as a `proposal` row |
|---|---|
| Not JSON, not an object, wrong or extra keys, wrong types, a path that is absolute, has `..`, is temp or is one the run keeps, text that can't be UTF-8 | `refused` (with the line and why; the other rows are still read) |
| A claim that says nothing (the door wants a stated why) or any other refusal at the door | `refused` |
| A cite that is not an id in the served file (or nothing was served) | `link_fail` |
| Anything else | `pass`: awaiting the human |

The human seals one proposal's hash (`proposal:<sha256>` over path, data, cites
and claim). Only a `pass` that is sealed is written, by `rec.write_file`; the
write reads the seal from the record, never from the caller. A changed byte is
a different proposal that needs its own seal.

## One seal per session, at its end

The operator, 2026-10-19: *"there is really nothing to seal yet because no
real decision has been made other than hey I want to build this thing that's
not a decision that's an idea"*, and *"It's still my seal, the bundle is what
I'm sealing. I don't need to seal tool calls, I need, and user's, to be able to
say, yes, these were my actions in this session. Agree, or disagree, if
disagree, then deny the pair. The record is still there, and can still be
looked at, it just can't be served."* And on what serves: *"It can still be
tagged as session, but it shouldn't be excluded."*

`Config.seal_at_end` (`onescript/session.py`; the CLI turns it on):

| When | What happens |
|---|---|
| During | Nothing waits on a seal. `serve` hands out an unsealed scope; the Forge's card answers are drafts |
| Check-out | The session, from its check-in to now, is one bundle: the person's actions, what came of each, every record row by number, kind and hash. Written to the box and proposed to Nestor (`nestor.session_seal`) as one draft pair, `session:<sha256>` over its bytes |
| The seal | The person's, in Nestor's UI, which shows the bundle's actions and refuses a seal whose bundle changed. Seal: yes, these were my actions. Deny: `reject_pair` |
| Next check-in | A session sealed in Nestor since is read back as a `seal` row, checked like any Nestor seal; the screen says how many are sealed and how many wait |
| Serving | A sealed session's rows serve like any piece; a denied or unanswered one's never do, and stay on the record to be read. Rows from before any check-in never serve unsealed |

Without `seal_at_end` nothing changes: a scope serves only once sealed. A
sealed scope still serves everything it covers, either way.

## Seals without a terminal

The operator, 2026-10-07: "I won't have access to a terminal when this moves to
an apk." So no step needs a tty. A seal is either:

- the human's HMAC proof over the subject (`gate.human`), which needs the
  human's key in the keys file; or
- a sealed Nestor pair whose conclusion (`target_text`) is the exact subject
  string (`nestor_seal`, `gate.human_nestor`). The human seals it in Nestor's
  own UI. This repo only reads it, and only from two places fixed in `Config`,
  never from the caller:
  - Nestor's store (`Config.nestor_db`, `$WILLOW_NESTOR_DB`), opened
    read-only. The pair must be `sealed` and unsuperseded there right now; a
    pair handed in as a dict only points at a stored row (a superseded,
    rejected or missing one seals nothing).
  - The operator's keyring (`Config.keyring`: `$WILLOW_KEYRING`, else
    `$WILLOW_HOME/config/verifiers.json`), outside the box. ed25519 public
    keys only; an HMAC entry or an entry carrying private material refuses the
    whole file, and a compromised, revoked or inactive key seals nothing. The
    signature is over Nestor's frozen `[source_norm, target_text, verifier]`
    encoding. The keyring's `legacy_key` is not accepted: it proves the
    deployment, not a person.

  ed25519 needs the `cryptography` package. Where it is absent the answer is
  `unreachable` with that reason, never a silent refusal of every real seal.

## The function API

`onescript/api.py`. Each function opens the box, writes its `invocation` row,
and returns plain data; a refusal is `{"refused": why, "code": n}`, never an
exception. Nothing moves before a check-in or after a check-out.

| Function | What it does |
|---|---|
| `checkin(cfg)` | Boot: record, probes, the four gates. Makes this session's serve key. The tests gate runs the suite in the bot's venv when it has one, and asks that interpreter for `pytest`, never this one. A pile with no xref index gets one built over `cfg.index_repos` (the CLI: its `--repo`s, else every clone beside willow-bot), through the record, before the store attaches; an index that is there but broken is never built over |
| `seed(cfg, bite, repos, exact=False)` | The first bite's words against the box. Counted from the session's store by default (`Cached.counts`: chunks and files per word, `counted_by: store`); `exact` scans for whole files and lines (`seed.py`: the pile's xref index, each file read at its indexed blob and counted only when it hashes to the index), and so does a seed with no store attached, saying why (`counted_why`). D6, the operator: "C". Keys rarest first, whole counts. `seeded`, `escalated:cold_start` or `unreachable`; once per check-in. A held write-back for this exact bite answers it first (`answered_by`), and unseen words are a `miss` row of hashes |
| `scope(cfg, by, match, upto, max_chars=None)` | Code proposes the stack. Returns the card (stack, joins), the `subject` to seal, and the `spec` that names the stack. Each chunk's size in characters, read at its blob (no text), the total, and the served document's length; with `max_chars`, whether it `fits` |
| `seal_scope(cfg, subject, proof / pair)` | The human's seal over the scope's exact set. A Nestor seal is checked against `cfg.keyring` and `cfg.nestor_db` |
| `seal_session(cfg, subject, proof / pair)` | The person's seal over one of this box's sessions (`session:<sha256>`), checked as any Nestor seal. The check-in does this on its own for every session sealed in Nestor since |
| `serve(cfg, spec, max_chars)` | Writes `served.json`. Only the sealed set, or, with `cfg.seal_at_end` (the CLI's), a scope nobody sealed, marked `seal: unsealed: this session`, serving only this session's record rows and sealed sessions' (below); over `max_chars`, or with no `max_chars`, `empty`, never truncated and never uncapped. A chunk found gone (not in the index, blob gone, hash mismatch, not at its line) gets one `tombstone` row per check-in, with `forward` where the same SHA-256 reads now |
| `decline_offer(cfg, key, proof=None)` | A No to a ladder offer (`ladder.py`: asked 1 does, 2 notices, 3 offers, again every 3rd ask); an `offer_declined` row stops it for that key. D5, the operator: "C". A No only narrows, so it takes effect unsigned, marked `said_by: caller`, `sealed: false`, and NEEDS YOU counts the unsealed ones; with the human's proof over `decline:<key>` it is `said_by: human`, sealed, and stands over them |
| `forge_bite(cfg, bite, project_id, builder_id, root)` | The Forge's `open_bite` with `PileBox` over this session's store as tier 2 (D9, the operator: "A"; live here only, the Forge never imports willow). `cfg.forge` (the CLI: `$ONESCRIPT_FORGE`, else `../forge` beside willow-bot) goes first on the import path when it holds `forge/entry.py`, so a clone is enough. The Forge not importable, or its own refusal (Nestor absent), is `unreachable`, said. A choice or confirm for the maker comes back `needs_you` with the card; the responder never answers, so nothing is sealed. With `cfg.seal_at_end` the Forge is asked to `defer_seal`: an answer becomes a Nestor draft, a piece of the session, never a seal of its own. Else `populated` or `empty`; one `forge_bite` row of hashes and state words |
| `take_proposals(cfg, rows)` | The model's rows through a turn: door, served cites, recorded, graded, nothing written |
| `seal_proposal(cfg, subject, proof / pair)` | The human's seal over one proposal; a sealed `pass` is written, from the stored path and data, which must hash to the subject, inside the box (no symlink is followed out) |
| `escalate(cfg, task)` | willow-bot's deterministic chain on the sealed served scope (below). Each answered piece is proposed as a write-back |
| `writeback_seal(cfg, subject, proof / pair)` | The human's seal over one write-back's exact subject (`writeback:<hash>`); held, the same bite stops at rung 1 (below) |
| `checkout(cfg)` | Reverse, then the morning screen. With `cfg.seal_at_end`, then the session's bundle: written to `sessions/<from>.json` and proposed to Nestor as one draft pair (below), one `session` row |

The record is the only state between calls, except two keys kept beside the
keys file, never in the box: the serve key (`serve.key`, 0600), fresh at every
check-in and never the seal key, and each box's store key
(`store-<hash of the box path>.key`, 0600), made once at the first check-in,
which keys the word and sentence hashes in that box's store (`pile.py`): the
store records only its fingerprint, and another box's store is read only when
its key is here and its record's chain verifies. A `spec` pins the stack to the record's rows up to `upto`,
so rows added later can't move a table id out from under a seal.

### The session's store

The operator: "So, all those keys need to live outside the sandbox. Like in
sean-data vault for me." (willows-grove next-pile.md). Here the store key sits
beside the keys file, outside the box but inside whatever sandbox the box runs
in; on the operator's machine `--keys` points that dir at the vault. That
placement is the operator's to make.

| Rule | How |
|---|---|
| Built in the background | The operator: "loaded everything up in the background, in anticipation of my next enter stroke." With the CLI (`Config.store_background`), a store that needs building is built by a detached process (`python -m onescript.pile --build-store`), which writes only the store file, never the record, and reads the key itself. Check-in writes a `building` row and returns; the first verb after the store is ready attaches it (an `attached` row with the file's SHA-256). Until then the store is `unreachable`: "still building". A lock stops a second build |
| The record holds hashes, not words | "A baseline holds hashes only, never content." The seed row holds the bite's words, keys, misses, pairs and repairs keyed with the store key; the caller still gets its words in the answer. No store key: the words are left out of the row, said |
| The cache and the direct read are one pair | "Every pair gets a named middle." `pile.rank` (reads the blobs) and `Cached.rank` (reads the store) both end in `score_grams`; `test_the_cache_ranks_exactly_as_the_direct_read` is the reconciler, byte for byte |
| Typos, rung 2 | `Cached.repair`: an unseen word tried one edit away, each spelling hashed with the key; the words never leave the bite |
| Every number is the operator's | `deep_thought.py`: "the numbers are yours … The script may propose each from the record; it never sets one." `MAX`, `MIN`, `TITLE`, `QUESTION`, `ATTEST`, `PAIR_MIN`, `PAIR_BASIN` (off), `REPAIR_MIN`, `MAX_STORE`, `MAX_POSTING` are placeholders |

### Escalate: the deterministic chain

`python3 -m onescript escalate "<task>"` (`api.escalate`) runs the chain over the
current check-in's sealed served scope (the served file, only while it hashes to
this check-in's `serve` row). D0 (`resolve_fixture`) answers what code can; an
empty or unreachable scope is escalated straight up and never handed to a model
("only along side"); what D0 escalates is cut into small pieces (one per table,
then row groups under `--piece-chars`), each handed to the local rung
(`ratatosk --onescript --served <piece.json> --out <rows.jsonl> --model M
"<question>"`). Every result is a `escalate_rung` record row keyed by the
piece's hash; a hash already on record is reused and no model runs. A model's
rows go through the proposal handling and are never written; a row with
`path == "ESCALATE"` is an escalation, its reason in `claim`. Pieces no rung
answered become one `review` card routed to `willow` (an `escalate_card` row,
printed; the willow-mcp verb files it).

One vocabulary: `answered`, or `escalated:<reason>`.

| Reason | Meaning |
|---|---|
| `d0_escalate` | D0 had no answer for the act and said so (or needs flowering) |
| `local_escalate` | the model returned `ESCALATE`, with its reason |
| `uncited` | an answer row with no cites, or a cite not in this piece |
| `silent` | the rung returned nothing |
| `timeout` | the rung did not return in time |
| `cap` | a piece, or a rung's output, was over its cap |
| `rung_error` | the rung failed: non-zero exit, unreadable output, or a row the contract or door refused |
| `empty_scope` | nothing populated is served in this check-in |

### Every escalation writes back

`onescript/writeback.py`. The loop (next-pile.md, "the loop", 6): "the cloud's
answer is recorded into the pile, tied to the words that missed"; the ladder
(the one-script README): "every answer is written back by the hash of the
phrasing, so the next time it stops at rung 1"; the forge's intake contract
(`the-forge-shape.md`): "a miss log — what was asked that nothing answered".
Everything here is a record row. Nothing is written into Nestor's store.

| Row | Written when | Holds |
|---|---|---|
| `miss` | a seed whose words the session's store has no posting for (in any form `rank` looks for), and no sentence of the bite was said before | the bite's hash, each missing word keyed with the box's store key (BLAKE2b-128), the count, the seed row and the check-in. Never a word, and never a plain hash of one |
| `writeback` (`proposed`) | an `escalate_rung` row settles `answered`, or a sealed proposal is written | subject `writeback:<sha256>` over `{bite_hash, answer_hash, cites}`; the answer (cites rewritten to content); where it came from |

- **The bite** is this check-in's seed (the words that missed); with no seed,
  the task as written (or the turn's own bite, for a proposal).
- **The cites** are content hashes: a chunk's own SHA-256, else the table's
  content id. The served ids are keyed with the session's serve key and mean
  nothing in another session. A cite that doesn't map to this check-in's
  served file, or no cite at all, and nothing is proposed (said, in the
  verb's `writeback` list).
- **Held** only while the human's seal over that exact subject verifies
  (`Run.held`): the HMAC proof, or a sealed Nestor pair whose conclusion is the
  subject, read from `Config.nestor_db` as any other seal is. A changed byte in
  the bite, the answer or a cite is another subject with no seal.
- **The read-back** runs at `seed`, before anything is ranked. A held
  write-back whose `bite_hash` is this bite's hash, exactly, answers it: the
  seed row says `answered_by: writeback:<subject>`, and its `writeback` field
  carries the cites and the row. Nothing fuzzy. A hit logs no miss.
- **Three states** in the seed's `writeback` field: `held`, `none` (with
  `unsealed: n` when candidates wait on a seal), and `unreachable` when a
  candidate's seal can't be checked here (the store or keyring is gone). An
  unchecked seal is never a hit.
- **A row is judged on its own bytes.** A `writeback` row whose answer doesn't
  hash to its `answer_hash`, whose subject isn't the hash of its fields, or
  whose hashes aren't SHA-256s is skipped, even when another row with that
  subject is sealed.
- A store that can't be read logs no miss: unknown is not a miss.
- `needs` adds `writeback: {misses, since, line, writebacks_proposed,
  writebacks_held}` when the record holds a miss or a write-back: "N misses
  since <ts>", counts only. The NEEDS YOU lines are unchanged, so the screen
  and `needs` still agree.

What the operator decides (not done here):

| Question | Now |
|---|---|
| Export held write-backs to Nestor as draft pairs (`nestor.answer.propose`), so they're sealed in Nestor's UI alongside everything else | Not done; the one script only reads Nestor |
| A missing word's hash: plain SHA-256 (comparable across sessions, but a short word is recoverable by trying a dictionary) or keyed | Plain SHA-256 |
| Should a write-back hit skip the ranking entirely ("nothing runs") | The seed still scans and ranks, so `scope --seed-top` works; `answered_by` is set first |
| Which bite an answer is tied to when the escalate task isn't the seed's bite | The check-in's seed, else the task as written |
| Whether D0's (code's) answers should be written back, since code answers them again anyway | Written back when its cites map to the served file, like any other |
| A newer held write-back for the same bite supersedes an older one | The newest held one answers; nothing is retired |
| The spot check ("a small sample of hits is sent up the chain anyway") | Not built; a wrong hit still doesn't escalate |

### The served-text cap

Serve no longer derives a cap from Claude Code's 10,000-character limit.
`max_chars` comes from the caller (Rat sizes it from the model's context) and
bounds the served document's text. Over it, `empty` and "narrow the stack".
`prompt.py` takes the same number as `ONESCRIPT_MAX_CHARS`, counted over the
whole injected text.

### The Forge's box: `PileBox`

`onescript/box_lookup.py` is the willow side of the Forge's `BoxLookup` seam
(`forge/entry.py`, tier 2: "the real lookup ... plugs in from the willow
side"). It duck-types the Protocol (`name`, `lookup(hits)`) and imports
nothing from the Forge, so willow-bot never requires forge-play.

`PileBox(stores, top=5)` takes a `pile.Cached`, a `{name: Cached}` dict, or a
list (several are merged by chunk hash, `pile.merge`), or `rank=` a callable
`bite -> rank result` in their place. `lookup(hits)` joins the hits' `source`
keywords into a bite, ranks it, and returns the top chunks as `Candidate`
(`name` = `repo:path`, `where` = `repo:path:line`, `why` = the keywords and
the score, or the stores it was seen in when merged).

| `last_state` | When | `name` (what `tiers["box"]` prints before `: nothing`) |
|---|---|---|
| `populated` | a chunk ranked | `willow pile` (`: N candidate(s)`) |
| `empty` | the store answered, nothing ranked; or no keyword | `willow pile (empty: why)` |
| `unreachable` | no store, a store with no chunks, or the read failed | `willow pile (unreachable: why)` |

`last_why` holds the reason. `api.forge_bite` is the live wiring (D9 A):
`open_bite(..., box=PileBox(<this session's store>))` with a responder that
never answers for the maker. `onescript/tests/test_box_lookup.py` and
`test_forge_bite.py` run it end to end when `forge.entry` and `nestor` are
importable, and skip otherwise.

## Gates on the phone

The gates that shell out (toolchain/ruff, tests/pytest, repos/git) report
`unreachable` when their tool or clone is absent, but only when the run says it
is on the phone: `Config.phone` (`--phone`), a flag the APK sets, never
inferred from a missing tool. The row is marked `deferred`: the box opens,
because nothing ran, and it isn't a pass either. The next check-in where the
gate CAN run (the box, when the record comes home) runs it and writes a
`deferred_result` row against the deferred one, once. On the box the same gap
is a `failing` gate and hard-closes. A gate that runs and fails still
hard-closes, as before.

`--no-tests`, and a check-in nested inside the tests gate's own suite, each
leave a `differently` row on the tests gate saying it was skipped and why.

## Planting: a person's first run

The seed's person-facing half (`onescript/plant.py`, IDEAS 121), in the one
script's terms. Every seed before it (willow-seed, SEED9, the charter's
SEED10) was the file a person runs first; the one script already had the
mechanics, and this is what a person meets.

- **`plant`**, once per box after its first check-in: the canon's six
  movements to read, from `$WILLOW_HOME/seed/canon` or the Grove's
  `governance/seed/canon` (the Grove's own order). The record keeps each
  movement's hash, never its words. No canon is `unreachable`, said, and
  nothing is planted.
  Then the coda, SEED10's "first honest handoff" (`seed/handoff/seed.py`, from
  `willow-config/seed` beside the bot or `$ONESCRIPT_SEED`), its hash on the
  row. `--who` is movement 3, the person saying who they are: kept in the
  box's `person.md` only with `--keep-who`, otherwise read once and not kept.
- **`consent`**, once per check-in: the repos this session may read, training
  consent off unless given, expiring at check-out. On a planted box serve and
  the prompt hook take no chunk from a repo the session didn't consent to;
  NEEDS YOU says when a session hasn't. An unplanted box serves as before.
- **`letter`**, the handoff letter for the next session, written into the
  box's `letters/` through the record; NEEDS YOU names the latest.

Not brought over, on purpose: SEED9's dashboard card and Nestor's seeded demo
store, which belong to their own surfaces.

## Running it

```bash
cd one-script
python3 -m onescript checkin                       # boot: record, probes, the four gates
python3 -m onescript plant --allow REPO            # once per box: the canon to read, this session's consent
python3 -m onescript consent --allow REPO          # each later session: what it may read
python3 -m onescript letter --file handoff.md      # the next session's letter (no --file: read the latest)
python3 -m onescript seed "the first bite"         # its words against the box, once per check-in (--exact: files and lines)
python3 -m onescript forge "the first bite"        # the Forge's entry, this session's pile as its box
python3 -m onescript decline <key> --proof <p>     # a No to an offer; --proof seals it as the human's
python3 -m onescript scope --by who --match who=run   # card, subject, spec (JSON)
python3 -m onescript seal serve:<hash> --pair p.json   # keyring/store: $WILLOW_KEYRING, $WILLOW_NESTOR_DB
python3 -m onescript serve --by who --match who=run --upto <n> --max-chars 20000
python3 -m onescript turn "the bite" --proposal f.jsonl
python3 -m onescript seal proposal:<hash>              # found in Nestor's store (--keyring/--nestor-db before the command)
python3 -m onescript escalate "the task"           # D0, then small pieces to the local rung
python3 -m onescript checkout                      # reverse, then the morning screen
python3 -m onescript life g1 --soup odile          # Conway's Life as verdict rows (--board F.rle)
python3 -m onescript life-check g1                 # played again from its open row

python3 -m pytest -q onescript/tests            # its tests (willows-grove beside this repo)
python3 foundation/run_prompt_tests.py          # prompt.py's tests on any Python
```

The box defaults to `.flow/onescript/` in this repo, which git ignores.
`ruff.toml` here carries willows-grove's lint floor, so the move didn't
change what the code is held to.
