"""words.py: the one word rule, pinned (flatten-2 F11, D-e)."""

import hashlib

from onescript import ladder, pile, seed, words

COMPOSED = "caf\u00e9 na\u00efve"
DECOMP = "café naïve"


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def test_one_home():
    assert seed.WORD is words.WORD and pile.WORD is words.WORD


def test_ascii_golden():
    assert words.words("Hello, World! 42 x_y") == ["hello", "world", "42", "x", "y"]
    assert words.bite_key("Hello, World!") == sha("hello world")
    assert words.bite_key("Hello, World!") == ladder.bite_key("hello   WORLD")
    assert words.bite_key("!!! ___") is None


def test_composed_golden():
    assert words.words(COMPOSED) == ["café", "naïve"]
    assert words.bite_key(COMPOSED) == sha("café naïve")


def test_decomposed_v1_is_unchanged_and_splits():
    # the recorded behaviour: a combining mark is not a letter, so it splits
    assert words.words_v1(DECOMP) == ["cafe", "nai", "ve"]
    assert words.bite_key(DECOMP) == sha("cafe nai ve")
    assert words.bite_key(DECOMP) != words.bite_key(COMPOSED)


def test_nfc_variant_joins_them_and_is_not_the_default():
    assert words.words_nfc(DECOMP) == words.words_nfc(COMPOSED) == ["café", "naïve"]
    assert words.bite_key_nfc(DECOMP) == words.bite_key_nfc(COMPOSED)
    assert words.words is not words.words_nfc
    assert words.bite_key(DECOMP) != words.bite_key_nfc(DECOMP)


def test_nfc_changes_no_ascii_or_composed_key():
    for t in ("plain ascii 123", "Hello, World!", COMPOSED, "日本語 テスト", "x_y z"):
        assert words.words_nfc(t) == words.words_v1(t)
        assert words.bite_key_nfc(t) == words.bite_key(t)


def test_hyphen_apostrophe_split():
    assert words.words("don't well-known o\u2019neil") == [
        "don",
        "t",
        "well",
        "known",
        "o",
        "neil",
    ]


def test_cjk():
    assert words.words("日本語 テスト、漢字") == ["日本語", "テスト", "漢字"]
    assert words.bite_key("日本語") == sha("日本語")


def test_seed_words_distinct_sorted():
    assert seed.words("b a b A") == ["a", "b"]
