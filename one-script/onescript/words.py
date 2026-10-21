"""The pile's word rule: what a word is, and the key a bite hashes to (flatten-2 F11).

Cites: D-e (bytes already persisted never change), CONST-IV.

The operator: "Using the onescript at every path." and "merge only what must be
identical". The pile counts words and the ladder hashes a bite's words into a
key; both must agree to the byte, so the rule has one home here. `seed`, `pile`
and `ladder` import it.

A word is a run of letters or digits (`[^\\W_]+`), after `str.lower()`. The
KEY rule is `words_v1`, exactly what `seed.WORD.findall(bite.lower())` always
did, and it stays the default: `words` and `bite_key` are v1.

No Unicode normalization form is applied by v1, so it is not stable across the
two spellings of one letter. A decomposed `e` + U+0301 is not a letter (a
combining mark is not alphanumeric), so v1 splits it: `cafe\\u0301` -> `cafe`,
where the composed `caf\\u00e9` -> `café`. The two hash differently. Moving the
default to NFC would rekey every recorded bite that held a decomposed
character, shifting ladder counts and the store's word hashes (D-e). So NFC is
a named variant (`words_nfc`, `bite_key_nfc`), pinned by test, wired nowhere.
It is NFC rather than NFKC because NFKC also folds compatibility forms
(ligatures, full-width, superscripts) into other words, a wider change than
spelling. Flipping the default is the operator's call (and a rekey).

Search tokenizers with their own tuning are a different rule and stay where they
are: stopwords, minimum length, ASCII-only, apostrophes kept.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

WORD = re.compile(r"[^\W_]+")  # a word, for seed, the pile and the ladder alike


def words_v1(text: str) -> list[str]:
    """The key rule as first written: lowercase, then letter-or-digit runs, in
    order, repeats kept. No normalization form."""
    return WORD.findall(text.lower())


def words_nfc(text: str) -> list[str]:
    """`words_v1` after NFC. Differs from v1 only where the text holds a
    decomposed (or otherwise composable) sequence. Not the key rule."""
    return words_v1(unicodedata.normalize("NFC", text))


def words(text: str) -> list[str]:
    """The key rule (v1). Ordered, repeats kept."""
    return words_v1(text)


def _key(ws: list[str]) -> str | None:
    if not ws:
        return None
    return hashlib.sha256(" ".join(ws).encode()).hexdigest()


def bite_key(text: str) -> str | None:
    """sha256 of the words, one space between. None for no words."""
    return _key(words(text))


def bite_key_nfc(text: str) -> str | None:
    """`bite_key` over `words_nfc`. A different key for a decomposed bite."""
    return _key(words_nfc(text))
