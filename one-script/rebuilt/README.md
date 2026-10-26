# The one script, rebuilt from willow's pieces

`one.py` is the one script's loop, the seven parts and their order, done by
[willow-memory/willow](https://github.com/willow-memory/Willow)'s kernel and
add-ons. It's one stdlib file of about 600 lines, against the 25,000 in
`../onescript/`. The aim is to see how much of the one script willow already
holds now that the kernel lives there. It isn't meant to replace
`onescript/`: that stays the real one, with its Nestor seals, its store, the
Forge and Rat.

    python one.py checkin  --box DIR --pile FOLDER --key FILE   # first time: the kernel's init
    python one.py seed     --box DIR "the first bite"
    python one.py turn     --box DIR --proposal rows.jsonl
    python one.py escalate --box DIR "the task"
    python one.py needs    --box DIR
    python one.py checkout --box DIR
    python one.py verify   --box DIR
    python one.py map

willow comes from `--willow`, else `$ONESCRIPT_WILLOW`, else a clone beside
willow-bot (`../Willow` or `../willow`). That's the same placement rule as
willows-grove for the original. willow's `CONSTITUTION.md` is the law here.
Without willow or its law, every verb refuses.

## The seven parts

| Part | Here | Done by |
|---|---|---|
| 1 boot | `checkin` | `kernel.read` first (a break refuses), then four gates: `pin` (willow's kernel is the one `../kernel.pin` names), `law` (`differently` when the constitution changed since the last check-in), `beats` (`kernel.test`, the eight beats), `seals` (no seal row that doesn't prove). A failing gate is a hard close with the original's four options. Each add-on the parts lean on is checked, and a missing one is named `unreachable` |
| 2 predict | `seed` | Before the bite, what the record says it will come to (`populated`, `empty`, `sealed`), counted from earlier bites, a tie going to `empty`. Then `kernel.bite`, and a `predict` row grading the guess. `again` says which rung of the ladder the bite is on. Once per check-in |
| 3 record | every verb, `verify` | Each row through `kernel.append`. `verify` is `kernel.verify` plus `session-seal check` |
| 4 gate | `turn` | `kernel.propose`: the door, and cites only from the last bite. A pass waits, and the seal command is printed for the person |
| 5 resolve | `escalate` | Rung by rung, stopping at the first that answers: a sealed answer, your files (`kernel.bite`), every match (`all`), where to look (`kernel.look`), a model (`unreachable`: none is wired). It ends `answered` or `escalated:to_the_person`, recorded as one `escalate` row of rung states, without the words |
| 6 view | `needs` | `digest`'s page (waiting, gone stale, coming up, asked again), and each session sealed, waiting or broken. Writes nothing |
| 7 reverse | `checkout` | `stale` (approvals whose cited passage changed or went) and how the predictions did, as a `checkout` row. Then the screen, then `session-seal`: the session from its first row to the checkout, proposed for the person's one seal |

Nothing here approves anything. The seal is always `kernel.py seal` with the
person's key.

## Both ways

- **willow → the one script.** A box `one.py` writes is this one script's
  record, row for row: `onescript.record.Record(box).verify_chain()` holds
  on it, and an edited row breaks both (`onescript/tests/test_rebuilt.py`).
- **The one script → willow.** Willow had no piece for `session.py`, the
  one seal over a whole session. It now has the `session-seal` add-on,
  ported from it, with the kernel's own propose and seal standing in for
  Nestor's draft pair. That add-on is on the willow branch of the same name
  as this one. Until it merges, `one.py` against willow's `main` says
  `session-seal` is `unreachable`, and the rest still runs.
- **On willow's blind demo.** `test_rebuilt.py` also runs `one.py` on
  Bernadette (`demo/bernadette` in willow), a person an agent built without
  seeing the code. It reads her raw files, with no readers, the way the one
  script reads a repo: her tax note answers "whats the deadline for the tax",
  "did i promise siobhan anything" serves the email she wrote the promise in,
  and a question nothing of hers answers goes to the person.
- **The kernel reads the one script's own box.** Willow's `kernel.read`
  verifies willow-bot's own `.flow/onescript/record.jsonl`, written by the
  prompt hook: every row has the kernel's keys and the same chain.

## What the map says

`one.py map` lists every module in `onescript/`, the willow piece that does
its job, and whether that piece is here:

    60 parts: 39 done by willow's pieces, 12 still ideas on willow's list, 9 the bot's own

That count is against willow with `session-seal`; against willow's `main` it
is 38 and 13. *Done by* means the job at the kernel's scale, not everything
the module does. What the rebuild drops:

| Part | What `onescript/` does that the piece doesn't |
|---|---|
| `pile.py` | the typo rung, the embedder rung, phrase grams, the keyed store; `kernel.rank` is the words rung only |
| `serve.py` | a scope, sealed or marked unsealed, served to one file by keyed ids; `scope` is still an idea in willow, so the bite's own served passages stand in |
| `nestor_seal.py` | seals made in Nestor's UI with no terminal; here the person runs `kernel.py seal` |
| `reverse.py` | wagers, calibration, the tip anchor; `stale` re-checks approvals only |
| `escalate.py` | the local model rung (Rat) and the review card routed to willow |
| `boot.py` | the tests, toolchain, freshness and reachability gates over the bot's repos; here the gates are willow's own |

The 12 ideas are already on willow's `adapters/planned.json`, and `scope` is
the one the loop misses most. The 9 the bot's own (the Forge seam, the code
graph, OAuth, the merge gate, reach) stay here; they aren't willow's to do.
