# The kernel's vectors: one script, any language

`kernel.py` is the smallest script that runs the whole loop for anyone. This
folder lets it run in another language without becoming another script: a
port answers the same cases byte for byte, writes boxes `kernel.py` verifies,
and verifies boxes `kernel.py` wrote. The vectors are the converter, both
directions.

Made 2026-10-21, after asking the one script's pile what the fleet already
had. The pieces were already there; this puts them together for ports:

| Piece | Where it came from |
|---|---|
| Cases made once, under one Python, then every implementation compared, with "unexplained differences" as the number to read | `foundation/gen.py`, `foundation/compare.py` (CPython 3.9 against 3.16) |
| Never port an encoding from its description: read the pinned bytes | willow-mcp `canon_local.py`; the kernel's rows are family A |
| Two copies that can't share code pin the same golden bytes | willow-mcp and willow-gate `test_signing_encoding.py` (A6) |
| Python generating another language's half, parity-tested | Grove `scripts/generate_states_js.py` |
| Standard vectors for the primitive | `test_sign.py` uses RFC 8032; the seal is HMAC-SHA-256, so RFC 4231 |

## What's here

| File | What it is |
|---|---|
| `gen.py` | Makes `kernel_tables.json` and `cases.json` from `kernel.py`. Deterministic: the same bytes on every run and every hash seed |
| `kernel_tables.json` | What Python decides that another language decides differently, read out of Python: word characters (`[^\W_]`), lowercasing and the final-sigma context, `str.strip`/`splitlines`/`isdigit`, the HTML5 entities and `html.unescape`'s tables, the kernel's constants. A port loads it, so its own Unicode version and regex engine never decide a word |
| `cases.json` | 3,005 cases in 12 parts: canon, words and bite keys, RFC 4231 and seal proofs, page text, `holds`, `echo`, chunks of nine folders, rank, look, the door, subjects |
| `compare.py` | Holds a port to `kernel.py`: every vector; one whole session through both command lines on the same paths (exit codes, what the person saw, every row, every file); each box verified by the other implementation and by the one script's `Record` |
| `java/Kernel.java` | The first port. One file, JDK 17+, standard library only |

## Running it

```
python3 gen.py        # after any change to kernel.py; commit both JSON files
python3 compare.py    # needs javac and java; ends "unexplained differences: 0"
```

`onescript/tests/test_kernel_vectors.py` runs both: it fails when `kernel.py`
changed and the vectors weren't remade, and runs `compare.py` when a JDK is
on the machine.

The Java port by itself:

```
javac -d out java/Kernel.java
java -Dkernel.tables=kernel_tables.json -cp out Kernel init --box BOX --pile FOLDER --key KEY
```

**It needs a UTF-8 locale.** Without one the JVM can't even name a file with a
non-ASCII name (`InvalidPathException`), where Python carries it through.
Run it with `LC_ALL=C.UTF-8` (compare.py does).

## Is it really testing anything?

Each trap a port can fall into was planted in a copy of `Kernel.java`, and
the vectors had to catch it. The first set caught 8 of 13, so cases were added
for the rest. Now 14 of 15 are caught:

| Planted | Caught by |
|---|---|
| Keys sorted by UTF-16 unit, not code point | canon |
| Java's `toLowerCase`, Java's `isLetterOrDigit`, no final sigma | words |
| `String.lines()` for `splitlines`, a path sort by joined string | chunks |
| Line length in UTF-16 units at the 500 cap; no cut at the cap | chunks (lines of emoji) |
| Java's `isWhitespace` for `strip` | chunks (an NBSP line), door (an NBSP claim) |
| Float scores as a plain `double` sum | rank (ties at the top three, found by search) |
| A tie broken by UTF-16 path order | rank (files named U+FF61 and U+1F600) |
| ASCII-only `\s` in `</script >` | html |
| No longest-prefix entity match | html |
| `contains("..")` for path parts | door |
| **Not caught:** float scores truncated to six decimals | It only moves an answer when two top scores are within a millionth; no case has that yet. The realistic float bug, a plain sum, is caught |

## What building it found in kernel.py

Two fixes, each its own commit with a test that fails on the old code:

1. **A file name could brick a box.** A served `where` is a file name, kept
   raw in the record. A name holding U+2028, U+2029 or U+0085 was cut by
   `str.splitlines()` on read-back, and every later verb refused the box.
   `onescript/record.py` already reads on `"\n"` only; the kernel now does too.
2. **The same bite could rank differently from run to run.** `rank` summed
   floats over a set, whose order is the process's hash seed. On Python 3.9
   to 3.11, 13 runs in 400 ranked one pile differently. Scores are now exact
   fractions, the same everywhere and exact for every port.

## Open

- `kernel_tables.json` is about 90 KB. A C or C++ port could compile it into a
  header instead of reading it at start (`states.js` style).
- Nothing in the vectors pins `HOLD` (0.5) or `FUNCTION` (English) as right:
  they are pinned as what the kernel says today, and still the operator's
  numbers to set (IDEAS 113).
- Case-insensitive matching of `<script>` in a page agrees with Python on
  this JDK for the long s (U+017F) and the dotted capital I (U+0130), both in
  the cases. Java and Python fold case by different rules, so a new JDK is
  worth a rerun.
- Next ports, by the plan: Go, Rust, C#, TypeScript about a day each; C++ two
  to three days (ICU-free, since the tables carry Unicode).
