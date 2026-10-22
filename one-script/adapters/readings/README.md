# readings

Three hints beside a kernel bite, found by the Odile demo
(`demo/odile/STRIP.md`):

| Reading | Says | On her box |
|---|---|---|
| what isn't there | an id in what was served that a file naming most of its kind doesn't | `2023-040, 2025-063, 2026-095: not in money/teo-books.csv` |
| numbers over time | in a dated table, what moved in the last week, and the most in any week of the last four | Laszlo's weight, up 2.5 kg in one week |
| a date gone by | in a log, a later date now passed with nothing written since | who's due for a tuning |

    python adapters/readings/readings.py bite --box DIR [--now TS] "your words"

**Uses:** the kernel's `main` (its own bite, exit code passed through),
`chunks`, `config`, `read_words`, `newest`, `words`, `holds`, `held_by`.

**Never:** writes to the box (the kernel's bite row is the record), judges
whether a move matters, or changes what the kernel served.
