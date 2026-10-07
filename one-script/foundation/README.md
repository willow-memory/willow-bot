# The hook on two foundations: CPython 3.9.0 against 3.16.0a0

*2026-10-05, desk session 019xJcd52XquwTaeZYqKL8QH. The operator cloned 3.9.0
("tag v3.9.0, 2020-10-04") and asked for the hook to be run on it and compared
against the newest Python ("3.16.0 alpha 0"). Agent-reported; not ratified.*

**Result:** the hook's hashes and its vault check behave the same on a Python
from October 2020 and on CPython `main` from today. The one split (integers
over 4,300 digits) fails safe, toward ESCALATE.

**Which hook:** the record-lookup hook at `aea0c64`. The hook was later cut to
its bare form (Read allowed, Write asks, everything else denied) with no
hashing and no vault check, so the hash and `realpath` rows below now describe
the foundation, not the hook. The bare hook's tests pass on both foundations.

## The two foundations

| | 3.9.0 | 3.16.0a0 |
|---|---|---|
| Source | tag `v3.9.0` | `main`, untagged |
| Commit | `9cf6752276e6fcfd0c23fdb064ad27f448aaaf75` | `114de198c0a05d8169cdbbd8f1e0976dc1319284` |
| Date | 2020-10-04, Łukasz Langa | 2026-10-05, Pablo Galindo Salgado |
| Verified | **yes:** good signature by RSA key `E3FF2839C048B25C084DEBE9B26995E310250568`, whose key ID python.org lists for Łukasz Langa (3.8.x and 3.9.x) | **no:** `main` has no tag to sign; the commit hash is all that pins it |
| Built | offline (`unshare -n`), out of tree, GCC 13.3.0 | same |
| SHA-256 from | the box's OpenSSL 3.0.13 (2024) | same |

Both builds stamp themselves `-dirty`. That's the out-of-tree build: the
Makefile runs `git describe --dirty` from the build folder. `git status` in
each clone is clean.

## What was compared

Source first: about 3,400 changed lines across `json`, `hashlib`, `hmac`, `os`,
`posixpath`, `tempfile` and `Modules/_json.c`, and `pathlib` became a package.
The escape tables in `json/encoder.py` are identical; `realpath` gained a
`strict` mode the hook doesn't use. Reading can't settle what the C encoder
emits, so the comparison is of behavior: the same inputs through both, bytes
out.

| What | Cases | 3.9.0 vs 3.16.0a0 |
|---|---|---|
| Canonical JSON → SHA-256 (all of Unicode, random-bit floats, NaN, ±inf, −0.0, nesting) | 2,293 | **identical hashes** |
| Lone surrogates | 204 | both fail the same way (UnicodeEncodeError), so the hook escalates on both |
| Integers over 4,300 digits | 503 | **differ:** 3.9.0 hashes, 3.16 refuses (the int-to-text limit added in 3.11) |
| Record lines (NaN, Infinity, 1e400, BOM, duplicate keys, NUL, non-objects, single quotes, trailing comma, hex, leading zero) | 22 | 21 same; only the 5,000-digit integer differs |
| `realpath`: symlink into the vault, a chain, a loop, dangling, relative `..`, `.` and `//` | 9 | **all identical** |
| Hook tests (`onescript/tests/test_hook.py`, without pytest) | 16, then 6 for the bare hook | pass on both |

Every one of the 503 object differences holds an integer over 4,300 digits;
`compare.py` checks each and reports `unexplained differences: 0`. 3.11.15
agrees with 3.16.0a0 on every case.

**What the split means:** an event carrying such a number hashes on 3.9.0 and
escalates on 3.16. A decision recorded under one foundation for that event
never matches under the other, so the human is asked again. 3.9.0 has no
switch to lift the limit (`sys.set_int_max_str_digits` arrived in 3.11), so
the split stays and stays safe.

**A flaw in the first run, kept on record:** lone surrogates were in every
object's pool, so only 447 of 3,000 objects reached the hash. Surrogates are
now in about one object in ten.

## Running it

```bash
python3 gen.py cases.pkl                      # once, any Python
python3.9  probe.py cases.pkl > a.json        # each foundation
python3.16 probe.py cases.pkl > b.json
python3 compare.py a.json b.json cases.pkl    # exit 1 on any unexplained difference
python3.9  run_hook_tests.py                  # the hook's tests, no pytest
```

`cases.pkl` isn't kept: `gen.py` rebuilds it from a fixed seed. Generate it
once and give the same file to every probe. All four scripts are stdlib only
and run on 3.9.
