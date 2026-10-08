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
| `onescript/tests/` | Its tests |
| `prompt.py` | The served tables, put into a prompt as framed data, so the model never holds a path |
| `foundation/` | `prompt.py`'s tests run on a bare interpreter, without pytest |
| `scripts/scan/` | `script_match.py` (the gate's script index) and the table scans |
| `scripts/flow/` | The view drafts: session maps and their merge |
| `deep_thought.py` | The morning screen |

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
| `checkin(cfg)` | Boot: record, probes, the four gates. Makes this session's serve key |
| `scope(cfg, by, match, upto)` | Code proposes the stack. Returns the card (stack, joins), the `subject` to seal, and the `spec` that names the stack |
| `seal_scope(cfg, subject, proof / pair)` | The human's seal over the scope's exact set. A Nestor seal is checked against `cfg.keyring` and `cfg.nestor_db` |
| `serve(cfg, spec, max_chars)` | Writes `served.json`. Only the sealed set; over `max_chars`, or with no `max_chars`, `empty`, never truncated and never uncapped |
| `take_proposals(cfg, rows)` | The model's rows through a turn: door, served cites, recorded, graded, nothing written |
| `seal_proposal(cfg, subject, proof / pair)` | The human's seal over one proposal; a sealed `pass` is written, from the stored path and data, which must hash to the subject, inside the box (no symlink is followed out) |
| `escalate(cfg, task)` | willow-bot's deterministic chain on the sealed served scope (below) |
| `checkout(cfg)` | Reverse, then the morning screen |

The record is the only state between calls, except the serve key, which is
kept beside the keys (`serve.key`, 0600), fresh at every check-in, and is
never the seal key. A `spec` pins the stack to the record's rows up to `upto`,
so rows added later can't move a table id out from under a seal.

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

### The served-text cap

Serve no longer derives a cap from Claude Code's 10,000-character limit.
`max_chars` comes from the caller (Rat sizes it from the model's context) and
bounds the served document's text. Over it, `empty` and "narrow the stack".
`prompt.py` takes the same number as `ONESCRIPT_MAX_CHARS`, counted over the
whole injected text.

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

## Running it

```bash
cd one-script
python3 -m onescript checkin                       # boot: record, probes, the four gates
python3 -m onescript scope --by who --match who=run   # card, subject, spec (JSON)
python3 -m onescript seal serve:<hash> --pair p.json   # keyring/store: $WILLOW_KEYRING, $WILLOW_NESTOR_DB
python3 -m onescript serve --by who --match who=run --upto <n> --max-chars 20000
python3 -m onescript turn "the bite" --proposal f.jsonl
python3 -m onescript seal proposal:<hash>              # found in Nestor's store (--keyring/--nestor-db before the command)
python3 -m onescript escalate "the task"           # D0, then small pieces to the local rung
python3 -m onescript checkout                      # reverse, then the morning screen

python3 -m pytest -q onescript/tests            # its tests (willows-grove beside this repo)
python3 foundation/run_prompt_tests.py          # prompt.py's tests on any Python
```

The box defaults to `.flow/onescript/` in this repo, which git ignores.
`ruff.toml` here carries willows-grove's lint floor, so the move didn't
change what the code is held to.
