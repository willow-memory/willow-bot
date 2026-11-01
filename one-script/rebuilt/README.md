# The one script, rebuilt from willow's pieces

`one.py` is the one script's loop, the seven parts and their order, done by
[willow-memory/willow](https://github.com/willow-memory/Willow)'s kernel and
add-ons. It's one stdlib file of about 600 lines, against the 25,000 in
`../onescript/`. The aim is to see how much of the one script willow already
holds now that the kernel lives there. It isn't meant to replace
`onescript/`: that stays the real one, with its Nestor seals, its store, the
Forge and Rat.

    python one.py checkin  --box DIR --pile FOLDER --key FILE   # first time: the kernel's init
    python one.py checkin  --box DIR [--pin FILE]               # after: the tip checked too
    python one.py seed     --box DIR "the first bite"
    python one.py turn     --box DIR --proposal rows.jsonl
    python one.py serve    --box DIR [--to FILE]
    python one.py escalate --box DIR "the task"
    python one.py needs    --box DIR
    python one.py checkout --box DIR [--pin FILE]
    python one.py verify   --box DIR
    python one.py map

willow comes from `--willow`, else `$ONESCRIPT_WILLOW`, else a clone beside
willow-bot (`../Willow` or `../willow`). That's the same placement rule as
willows-grove for the original. willow's `CONSTITUTION.md` is the law here.
Without willow or its law, every verb refuses.

## The seven parts

| Part | Here | Done by |
|---|---|---|
| 1 boot | `checkin` | `kernel.read` first (a break refuses), then four gates: `pin` (willow's kernel is the one `../kernel.pin` names), `law` (`differently` when the constitution changed since the last check-in), `beats` (`kernel.test`, the eight beats), `seals` (no seal row that doesn't prove), and with `--pin`, a fifth, `tip` ([tip-pin](https://github.com/willow-memory/Willow/tree/main/adapters/stdlib/tip-pin): the record still ends where the last checkout pinned it, so rows cut off the end, which `verify` can't see, close the session hard). A failing gate is a hard close with the original's four options. Each add-on the parts lean on is checked, and a missing one is named `unreachable` |
| 2 predict | `seed` | willow's [predict](https://github.com/willow-memory/Willow/tree/main/adapters/stdlib/predict): what the bite will come to (`populated`, `empty`, `sealed`), counted from earlier bites, a tie going to `empty`, written as a `predict` row on the chain before the bite. Then `kernel.bite`, and a `graded` row. Without predict, `seed` says `unreachable` and sows nothing. `again` says which rung of the ladder the bite is on. Once per check-in |
| 3 record | every verb, `verify` | Each row through `kernel.append`. `verify` is `kernel.verify` plus `session-seal check` |
| 4 gate | `turn`, `serve` | `kernel.propose`: the door, and cites only from the last bite. A pass waits, and the seal command is printed for the person. `serve` is willow's [serve](https://github.com/willow-memory/Willow/tree/main/adapters/stdlib/serve): the last bite's passages proposed as a scope, peaks first; once the person seals it, `--to FILE` writes the one markdown file a model reads, opaque ids and no paths. Unsealed, it serves nothing |
| 5 resolve | `escalate` | Rung by rung, stopping at the first that answers: a sealed answer, your files (`kernel.bite`), every match (`all`), the folder's phrases ([pile-rungs](https://github.com/willow-memory/Willow/tree/main/adapters/stdlib/pile-rungs)' phrase rung, which points and never answers: only the kernel's words do), where to look (`kernel.look`), a model (`unreachable`: none is wired). It ends `answered` or `escalated:to_the_person`, recorded as one `escalate` row of rung states, without the words |
| 6 view | `needs` | `digest`'s page (waiting, gone stale, coming up, asked again), and each session sealed, waiting or broken. Writes nothing |
| 7 reverse | `checkout` | `stale` (approvals whose cited passage changed or went) and how the predictions did (`predict`'s own grades), as a `checkout` row. Then the screen, then `session-seal`: the session from its first row to the checkout, proposed for the person's one seal. With `--pin FILE`, last, `tip-pin` writes where the record now ends to FILE: a new file each time, never one that's there, kept where whoever writes the box can't reach |

Nothing here approves anything. The seal is always `kernel.py seal` with the
person's key.

## Both ways

- **willow → the one script.** A box `one.py` writes is this one script's
  record, row for row: `onescript.record.Record(box).verify_chain()` holds
  on it, and an edited row breaks both (`onescript/tests/test_rebuilt.py`).
- **The one script → willow.** Willow had no piece for `session.py`, the
  one seal over a whole session. It now has the `session-seal` add-on,
  ported from it, with the kernel's own propose and seal standing in for
  Nestor's draft pair, now on willow's `main`. A willow without one of
  the pieces the loop leans on (`session-seal`, `serve`, `pile-rungs`,
  `tip-pin`) has that part say `unreachable`, and the rest still runs.
- **And back again.** The one script's second part, `predict.py`, is
  willow's `predict` add-on on that same branch: the prediction written on
  the chain before the bite, then a `graded` row. `seed` here now calls it,
  so the count and the grade are willow's, not this file's; `predict.py
  score` still reads the single-row `predict` rows older boxes hold.
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

    60 parts: 41 done by willow's pieces, 10 still ideas on willow's list, 9 the bot's own

That count is against willow's branch of the same name as this one, which
adds `predict`; against willow's `main` at 5e04bc5 (with `session-seal`,
`serve` and `pile-rungs`, without `predict`) it is 40 and 11. A willow
without a piece counts it as an idea again. *Done by* means the job at the kernel's scale, not everything
the module does. What the rebuild drops:

| Part | What `onescript/` does that the piece doesn't |
|---|---|
| `pile.py` | the typo rung and the embedder rung; `pile-rungs` has words and phrases, and says its embedder rung is skipped (willow's [latent](https://github.com/willow-memory/Willow/tree/main/adapters/deps/latent) needs numpy). `pile-store`'s store is there, unused here |
| `serve.py` | the per-prompt document marked `unsealed: this session`; willow's `serve` writes only a sealed scope, and keys its ids fresh each run |
| `nestor_seal.py` | seals made in Nestor's UI with no terminal; here the person runs `kernel.py seal` |
| `reverse.py` | wagers and calibration (willow's `forecasts` idea); `stale` re-checks approvals, and `tip-pin` is the tip anchor |
| `escalate.py` | the local model rung (Rat) and the review card routed to willow |
| `boot.py` | the tests, toolchain, freshness and reachability gates over the bot's repos; here the gates are willow's own |

The 10 ideas are already on willow's `adapters/planned.json`; with `scope`
built as `serve`, `forecasts` (the wagers `reverse.py` grades) is the one
the loop misses most. The 9 the bot's own (the Forge seam, the code
graph, OAuth, the merge gate, reach) stay here; they aren't willow's to do.
