"""
tests/test_runes.py — rune casting is deterministic and markdown-shaped.
"""
import re

import runes

_SHA = "deadbeefcafe1234567890abcdef1234567890ab"


def test_deterministic():
    assert runes.cast(_SHA) == runes.cast(_SHA)


def test_varies_across_shas():
    shas = [
        "0000000000000000000000000000000000000000",
        "1111111111111111111111111111111111111111",
        "abc1230000000000000000000000000000000000",
        "ffffffff0000000000000000000000000000000",
        "9deadbeef0000000000000000000000000000000",
    ]
    outputs = {runes.cast(sha) for sha in shas}
    assert len(outputs) > 1


def test_markdown_shape():
    reading = runes.cast(_SHA)
    assert re.match(r"^> \S+ \*\*[A-Za-z]+\*\* — .+$", reading)


def test_empty_sha_does_not_crash():
    reading = runes.cast("")
    assert reading
    assert "**" in reading


def test_short_sha_does_not_crash():
    reading = runes.cast("ab")
    assert reading
    assert "**" in reading
