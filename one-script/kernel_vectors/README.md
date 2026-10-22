# The kernel's vectors: one script, any language

`kernel.py` is the smallest script that runs the whole loop for anyone. This
folder lets it run in another language without becoming another script: a
port answers the same cases byte for byte, writes boxes `kernel.py` verifies,
and verifies boxes `kernel.py` wrote. The vectors are the converter, both
directions.

Three ports so far, every one passing everything:

| Port | File | Runtime | Made from |
|---|---|---|---|
| Java | `java/Kernel.java` | JDK 11+, standard library | `kernel.py` |
| TypeScript | `ts/kernel.ts` | Node 14+, standard library (TypeScript 4.0+ to build) | `kernel.py` and Java |
| C# | `csharp/Kernel.cs` | .NET 5+, base class library | Java's shape, TypeScript's scanners |

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
| `kernel_tables.json` | What Python decides that another language decides differently, read out of Python: word characters (`[^\W_]`), lowercasing and the final-sigma context, `str.strip`/`splitlines`/`isdigit`, the HTML5 entities and `html.unescape`'s tables, how `re.I` folds the letters of `script` and `style` and what its backreference calls equal, the kernel's constants. A port loads it, so its own Unicode version and regex engine never decide a word |
| `cases.json` | 4,595 cases in 17 parts: canon, words and bite keys, RFC 4231 and seal proofs, page text, `holds`, `echo`, chunks of fourteen folders, rank and look (some with a words list; look says what isn't there, what moved, and what date has gone by), `newest` (the dates a chunk carries), `written` (dates as people write them), `wordlist` (a words list read), `ids` (what names one thing, and its shape), `series` (numbers over time), the door, subjects |
| `compare.py` | Holds every port to `kernel.py`: every vector; one whole session through every command line on the same paths (exit codes, what the person saw, every row, every file); each box verified by every implementation and by the one script's `Record`. A port whose toolchain isn't here is skipped, and said so |

## Running it

```
python3 gen.py                 # after any change to kernel.py; commit both JSON files
python3 compare.py             # every port whose toolchain is here
python3 compare.py --port ts   # one port
```

It ends with a grid, every box under every implementation (0 = the chain
holds), and `unexplained differences: 0`.

`onescript/tests/test_kernel_vectors.py` runs both: it fails when `kernel.py`
changed and the vectors weren't remade, and runs `compare.py` when any port's
toolchain is on the machine.

The ports by themselves (`KERNEL_TABLES` names the tables for all three):

```
javac -d out java/Kernel.java && java -cp out Kernel bite --box BOX "your words"
(cd ts && npm install && npx tsc) && node ts/dist/kernel.js bite --box BOX "your words"
dotnet build csharp/Kernel.csproj -o csharp/out && dotnet csharp/out/Kernel.dll bite --box BOX "your words"
```

**Java needs a UTF-8 locale.** Without one the JVM can't even name a file with
a non-ASCII name (`InvalidPathException`), where Python carries it through.
Run it with `LC_ALL=C.UTF-8` (compare.py does).

## On the runtimes of 2020

`kernel.py` says Python 3.9+, so everything was also run on what was current
when 3.9.0 came out (2026-10-21, at the operator's ask):

| Runtime | Version | Where it came from | Result |
|---|---|---|---|
| Python | 3.9.0 (2020-10-05), Unicode 13.0 | built from tag `v3.9.0`, commit `9cf67522`, the one `foundation/` verified | the eight beats, the kernel's 17 tests, and `compare.py` with 3.9.0 as `kernel.py`: all pass; `gen.py` makes the same 3,088 cases as on 3.13 (only the Unicode tables differ) |
| Java | JDK 11.0.9 (2020-10-20), JDK 15 (2020-09-15) | AdoptOpenJDK builds, checksums verified | everything passes, unchanged |
| Node, TypeScript | Node 14.15.0 LTS and 15.0.0 (2020-10), TypeScript 4.0.5 | the npm registry | everything passes, after one change: `TextDecoder` imported from `util` (a global in Node 14, but typed only in `util` then) |
| .NET | 5.0.0 runtime (2020-11-10), C# 9 | NuGet's runtime packs, self-contained | everything passes, after the port stopped using .NET 6 and 7 conveniences (see below) |

.NET 5 does its hashing through OpenSSL 1.1, which a 2024 system doesn't
carry; it ran with Ubuntu 20.04's OpenSSL 1.1.1f (checked against focal's
package index) on its library path. What C# changed to reach .NET 5, with no
change on .NET 8: `record struct` and `>>>` (C# 10, 11) became a plain struct
and `/ 2`; `HMACSHA256.HashData`, `RandomNumberGenerator.GetBytes(n)`,
`ReadExactly`, `CreateTempSubdirectory` and `char.IsAsciiDigit` became their
long-standing equivalents; a path is resolved with libc's `realpath` (closer to
Python's `resolve()` than walking links); and the file mode (read, and 0600 at
creation) is the library's on .NET 7+ and libc's before it, the only `#if`.

`compare.py` takes a build made elsewhere: `KERNEL_TS_DIST` (a `dist/`
folder) and `KERNEL_CS_EXE` (a self-contained `Kernel`), with the old Node or
JDK first on `PATH`.

## Scanners, not regular expressions

The kernel has four patterns: a page's hidden `<script>`, `<style>` and
comment spans, its tags, its character references, and the name after
`def`/`function`/`fn`. All three ports match them with small hand-written
scanners, the same in each, written from Python's rules:

- .NET matches UTF-16 units and can't put an astral code point in a
  character class, which the word table needs.
- Go's RE2 and Rust's `regex` have no backreference and no lookbehind.
- Java's regex had both, and still disagreed: its backreference folds case by
  Java's rules, so it closed `<ſcript>…</SCRIPT>`, where Python doesn't (the
  vectors caught it on three pages). Python's fold and backreference are in
  the tables now, and every port reads them.

So the next ports (Go, Rust, C, C++) can follow any of the three line by line.

## Is it really testing anything?

Traps a port can fall into were planted in copies of the ports, and the
vectors had to catch them. Each time one got through, a case was added.

| Planted | Caught by |
|---|---|
| Keys sorted by UTF-16 unit, not code point | canon |
| The host's own lowercase or letter test, no final sigma | words |
| `String.lines()` for `splitlines`, a path sort by joined string | chunks |
| Line length in UTF-16 units at the 500 cap; no cut at the cap | chunks (lines of emoji) |
| The host's `isWhitespace` for `strip` | chunks (an NBSP line), door (an NBSP claim) |
| A UTF-8 decoder that drops a leading BOM (Node's default) | chunks, rank |
| Float scores as a plain `double` sum | rank (ties at the top three, found by search) |
| A tie broken by UTF-16 path order | rank (files named U+FF61 and U+1F600) |
| No longest-prefix entity match; no `;` taken after a reference | html |
| The closing tag matched by any fold, not the backreference's rule | html |
| No boundary after `script`/`style`; only ASCII space before `>` | html |
| A comment's `-->` allowed to overlap its `<!--` | html |
| `def` found inside a word; one space only after it; no digits in a name | look |
| A name counted where it's only the front of a longer word | look (added for this) |
| `contains("..")` for path parts | door |
| **Not caught:** float scores truncated to six decimals | Only moves an answer when two top scores are within a millionth; no case has that. The realistic float bug, a plain sum, is caught |
| **Can't be caught:** an entity name capped at 33, not 32 | The longest HTML5 name is 32 with its `;`, and the longest-prefix rule gives the same text either way |

## What building it found

In `kernel.py`, two fixes, each its own commit with a test that fails on the
old code:

1. **A file name could brick a box.** A served `where` is a file name, kept
   raw in the record. A name holding U+2028, U+2029 or U+0085 was cut by
   `str.splitlines()` on read-back, and every later verb refused the box.
   `onescript/record.py` already reads on `"\n"` only; the kernel now does too.
2. **The same bite could rank differently from run to run.** `rank` summed
   floats over a set, whose order is the process's hash seed. On Python 3.9
   to 3.11, 13 runs in 400 ranked one pile differently. Scores are now exact
   fractions, the same everywhere and exact for every port.

In the ports: Java's regex backreference (above), and two runtime traps
written into the files that dodge them: the JVM's locale, and Node's
`TextDecoder` dropping a BOM unless told not to.

## Locks

`kernel.py` takes an exclusive `flock` on the record to write, and reads
without one.

| Port | Its lock | Shares kernel.py's? |
|---|---|---|
| C# | libc's `flock`, blocking, called directly; .NET's own lock emulation is off (`Kernel.csproj`), since it takes a shared `flock` on every read and so trips over kernel.py's writer | Yes, both ways (tested: each waited for the other, then wrote) |
| Java | `FileChannel.lock`, a POSIX record lock (`fcntl`), which Linux keeps separate from `flock` | No: Java has no `flock` call |
| TypeScript | A directory made beside the record (Node has no lock call) | No |

Two writers of the same port never collide. Two different ones writing one
box at the same instant are kept apart only for C# and `kernel.py`.

## Open

- `kernel_tables.json` is about 90 KB. A C or C++ port could compile it in
  instead of reading it at start (`states.js` style).
- Nothing in the vectors pins `HOLD` (0.5) or `FUNCTION` (English) as right:
  they are pinned as what the kernel says today, and still the operator's
  numbers to set (IDEAS 113).
- The TypeScript port runs on Node. Past files, locks and the command line,
  its only Node call is hashing (`crypto`), which a browser has as
  SubtleCrypto. A browser build, the person's own folder through the File
  System Access API, is the natural next step for Grove's web and phone
  surfaces.
- Next ports: Go and Rust about a day each now, following the scanners; C or
  C++ two to three days (ICU-free, since the tables carry Unicode).
