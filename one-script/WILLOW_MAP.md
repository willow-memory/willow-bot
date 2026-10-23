# The map for `willow-memory/willow`

The kernel's public home, mapped before anything moves. Settled with the
operator on 2026-10-23.

## Settled

- **willow is the real copy.** Once the kernel lands there, willow-bot keeps a
  copy pinned by its SHA-256, and a willow-bot test fails when the two differ.
  Corrections are made in willow, never here.
- **Fresh history.** The first commit in willow is the kernel as it stands; the
  record of how each rule got there stays in willow-bot.
- **Public.** The operator makes the repo public; that's an admin setting, not
  a commit.
- **Names stay.** `kernel.py` and `adapters/`.

## The tree

```
willow/
├── kernel.py           the whole thing: stdlib only, Python 3.9+
├── test_kernel.py      the kernel on its own
├── README.md           what it does, the verbs, exits 0/42/2, the one rule
├── LICENSE             Apache-2.0
├── vectors/            the contract any other implementation is held to
│   ├── cases.json, kernel_tables.json   generated from kernel.py
│   ├── gen.py          regenerates them
│   └── compare.py      every implementation against them: "unexplained differences: 0"
├── ports/              the kernel rewritten in another language
│   └── java/  ts/  csharp/
├── adapters/           things built on top of the kernel
│   ├── README.md       the one rule, what an adapter can rely on, the table
│   └── readings/
├── demo/
│   └── odile/  hollis/  teodora/   CHARACTER, PLANTED, QUESTIONS, STRIP, make_life.py
└── .github/workflows/
    ├── kernel.yml      kernel and adapter tests, Python 3.9 to 3.14
    ├── vectors.yml     JDK, Node, .NET 8: compare.py, and gen.py gives the same files
    └── demo.yml        make_life.py --check for each character
```

## Why it's shaped this way

- **Ports and adapters are two folders.** An adapter is built on the kernel and
  writes through its `append`. A port replaces the kernel and never imports it.
  One folder would need two rules. Both are contributions: a folder and a row
  in a table.
- **Demos ship their generators, not `life/`.** The generated files are about
  5 MB; `make_life.py` is seeded, gives the same bytes every run, and `--check`
  holds them to PLANTED.md, so CI rebuilds and checks them.
- **The blind STRIP files go as written.** Hollis found none of seven and the
  readings were fitted to Odile. That is the honest part of the demo.

## What stays in willow-bot

- The one script (`onescript/`), `foundation/`, `compiled/`, `merged/`,
  `deep_thought.py`, `IDEAS.md`.
- From `onescript/tests/test_kernel.py`, the checks against the one script's
  own code: `test_the_whole_loop_on_the_command_line_and_the_one_script_verifies_it`,
  `test_it_is_the_one_scripts_record_seal_and_bite_key`, and the line of
  `test_a_file_name_with_a_line_separator_doesnt_brick_the_box` that reads the
  box with `onescript.record`. The other tests go to willow.
- Never copied: `node_modules/`, `obj/`, `__pycache__/`.

## Dependencies

| For | Needs |
|---|---|
| `kernel.py` | Python 3.9+, nothing else. Imports `hashlib hmac html json os re secrets sys tempfile datetime fractions pathlib` |
| tests | pytest |
| `vectors/gen.py` | Python (adds `unicodedata random base64`) |
| `ports/java` | a JDK, `java.*` only |
| `ports/ts` | Node; TypeScript 5.9.3 and @types/node 22.18.0 to compile |
| `ports/csharp` | .NET 8 SDK, no packages |
| lint | ruff |

The kernel decides what a letter is by Python's own string methods, so the
Unicode version is a dependency too: `kernel_tables.json` records the one it
was built on, and the pin allows for it.

## Fixed in the move

- The case count: the READMEs say 3,989; `cases.json` holds 4,463.
- `kernel_vectors/` becomes `vectors/` and its ports move to `ports/`, so
  `gen.py` and `compare.py` paths change with them.
- The readings adapter finds the kernel at `parents[2]`; in willow that is
  still the root.
