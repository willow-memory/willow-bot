"""The kernel's vectors: every case a port must answer byte for byte, made once.

    python3 gen.py [cases.json]

`foundation/` grown up for ports. That folder made its cases once, under one
Python, so every interpreter read the same objects (a change in `random`
must not look like a change in `json`). Same rule here, in JSON instead of
pickle, so a Java or C++ port can read it.

Two parts:

- `tables`: what Python decides that another language decides differently,
  read out of this Python rather than retyped. Which code points are word
  characters (`[^\\W_]`), what each lowercases to, what `str.strip`,
  `str.splitlines` and `str.isdigit` count, the HTML5 entities and
  `html.unescape`'s invalid-reference tables, and the kernel's own constants.
  A port loads these, so its host's Unicode version and its regex engine
  never decide a word. (Grove's `states.js` is generated from Python the same
  way.)
- `cases`, by part: canon, words, hmac, html, holds, echo, chunks, rank,
  look, door, subject. Each case holds its input and what `kernel.py`
  answered. Sessions (whole boxes, exit codes, rows) are run live by
  `compare.py`, both implementations on the same paths.

Every number here is deterministic: one seeded `random.Random`, the kernel's
own functions, no clock. Run it twice, get the same bytes.
"""

from __future__ import annotations

import base64
import hashlib
import html
import html.entities
import importlib.util
import json
import random
import re
import sys
import tempfile
import unicodedata
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
KERNEL = HERE.parent / "kernel.py"
_spec = importlib.util.spec_from_file_location("kernel", KERNEL)
k = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(k)

R = random.Random(20261021)
MAXCP = 0x110000
SURROGATES = range(0xD800, 0xE000)


def cps():
    return (c for c in range(MAXCP) if c not in SURROGATES)


def ranges(pred) -> list[list[int]]:
    """[first, last] runs of code points where `pred(chr(c))` holds."""
    out: list[list[int]] = []
    for c in cps():
        if pred(chr(c)):
            if out and out[-1][1] == c - 1:
                out[-1][1] = c
            else:
                out.append([c, c])
    return out


# ── tables ───────────────────────────────────────────────────────────────────
def tables() -> dict:
    word = k.WORD
    lower = {}
    for c in cps():
        ch = chr(c)
        if ch.lower() != ch and c != 0x03A3:  # capital sigma: by context, below
            lower[str(c)] = ch.lower()
    hold = Fraction(k.HOLD).limit_denominator(1000)
    return {
        "python": sys.version.split()[0],
        "unicode": unicodedata.unidata_version,
        "kernel_sha256": hashlib.sha256(KERNEL.read_bytes()).hexdigest(),
        "word": ranges(lambda ch: bool(word.fullmatch(ch))),
        # str.lower, code point by code point; U+03A3 lowers by its context
        # (CPython's final sigma: after a cased letter and not before one)
        "lower": lower,
        "sigma": {"cased": ranges(_cased), "case_ignorable": ranges(_case_ignorable)},
        "space": ranges(str.isspace),
        "digit": ranges(str.isdigit),
        "line_breaks": [ord(c) for c in _line_breaks()],
        "html5": html.entities.html5,
        # for a port without a regex engine that can say it (.NET can't put an
        # astral code point in a class; RE2 and Rust's regex have no
        # backreference): which code points re.I lets stand for each letter of
        # "script" and "style", and which pairs of those its backreference
        # (\1 under re.I, a simple lowercase per code point) calls equal
        "tag_fold": {t: _fold_set(t) for t in sorted(set("scriptstyle"))},
        "tag_backref": {
            t: [[a, b] for a in _fold_set(t) for b in _fold_set(t) if _backref_eq(a, b)]
            for t in sorted(set("scriptstyle"))
        },
        "invalid_charrefs": {
            str(n): v
            for n, v in vars(html)["_invalid_charrefs"].items()  # noqa: SLF001
        },
        "invalid_codepoints": sorted(vars(html)["_invalid_codepoints"]),
        "kernel": {
            "GENESIS": k.GENESIS,
            "TOP": k.TOP,
            "HOLD": [hold.numerator, hold.denominator],
            "SNIFF": k.SNIFF,
            "MAX_FILE": k.MAX_FILE,
            "MAX_CHUNK": k.MAX_CHUNK,
            "ECHO_RUN": k.ECHO_RUN,
            "FUNCTION": sorted(k.FUNCTION),
            "MAKE": sorted(k.MAKE),
            "NEXT": k.NEXT,
            "ROW_KEYS": sorted(k.ROW_KEYS),
        },
    }


def _near_tag_letters() -> dict[str, list[int]]:
    """Code points with a letter of "script"/"style" somewhere in their case
    mappings: the only ones re.I could fold to that letter. Asked once."""
    out: dict[str, list[int]] = {t: [] for t in set("scriptstyle")}
    for c in cps():
        ch = chr(c)
        mapped = (ch.lower() + ch.upper() + ch.casefold()).lower()
        for t in out:
            if t in mapped:
                out[t].append(c)
    return out


_NEAR: dict[str, list[int]] = {}


def _fold_set(letter: str) -> list[int]:
    # re itself decides; the case mappings only pick which code points to ask
    if not _NEAR:
        _NEAR.update(_near_tag_letters())
    return [c for c in _NEAR[letter] if re.fullmatch(letter, chr(c), re.I)]


def _backref_eq(a: int, b: int) -> bool:
    return bool(re.fullmatch(r"(.)\1", chr(a) + chr(b), re.I | re.S))


def _final(s: str) -> bool:
    return s.lower().endswith("\u03c2")


# CPython lowers U+03A3 to a final sigma when, skipping case-ignorable code
# points, a cased one comes before it and none comes after. Both properties
# are read from this Python's behaviour, not retyped (a code point that is
# case-ignorable is skipped before "cased" is ever asked of it):
#   final(ch + Σ)       holds iff ch is cased and not case-ignorable
#   final(A + ch + Σ)   holds iff ch is case-ignorable or cased
#   final(A + Σ + ch)   holds iff ch is case-ignorable or not cased
def _cased(ch: str) -> bool:
    return _final(ch + "\u03a3")


def _case_ignorable(ch: str) -> bool:
    return (
        _final("A" + ch + "\u03a3")
        and ("A\u03a3" + ch).lower()[1] == "\u03c2"  # the sigma, not the end
        and not _final(ch + "\u03a3")
    )


def _line_breaks() -> list[str]:
    return [chr(c) for c in cps() if len(("a" + chr(c) + "b").splitlines()) == 2]


# ── text pools: where languages disagree ─────────────────────────────────────
POOLS = [
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    " \t\n.,;:!?()[]{}\"'-_/\\@#$%^&*+=<>|~`",
    "éÉàÀçÇñÑüÜøØåÅßẞæÆœŒ",
    "e\u0301a\u0300o\u0308",  # combining marks: not word characters in Python
    "İıIi",  # Turkish dotted and dotless i
    "ΣσςΟΔΟΣΑΒΓ",  # sigma, final and capital
    "²³¹½⅓Ⅻ٠١٢٣߀",  # superscripts, fractions, roman, Arabic-Indic, NKo digits
    "中文日本語한국어",
    "😀🙂👍🏽👨\u200d👩\u200d👧",  # astral, a modifier, a ZWJ family
    "\u00a0\u2009\u3000\u200b\ufeff",  # NBSP, thin, ideographic space, ZWSP, BOM
    "ＡＢＣ１２３",  # fullwidth
    "\u2028\u2029\u0085\x0b\x0c\x1c",  # what splitlines cuts
    "ǅǈǋ",  # titlecase letters
]


def text(n: int) -> str:
    out = []
    for _ in range(n):
        pool = R.choice(POOLS)
        out.append(R.choice(pool))
    return "".join(out)


def key_text(n: int) -> str:
    # keys that sort differently by code point and by UTF-16 unit
    pools = ["a", "b", "z", "é", "｡", "\uffff", "\U0001f600", "\U00010000", "_", "A"]
    return "".join(R.choice(pools) for _ in range(n))


def value(depth: int = 0):
    roll = R.random()
    if depth > 3 or roll < 0.35:
        return text(R.randint(0, 12))
    if roll < 0.5:
        return R.choice(
            [
                0,
                1,
                -1,
                42,
                2**31,
                -(2**31) - 1,
                2**53 + 1,
                2**63 - 1,
                -(2**63),
                10**30,
                -(10**25),
            ]
        )
    if roll < 0.58:
        return R.choice([True, False, None])
    if roll < 0.78:
        return [value(depth + 1) for _ in range(R.randint(0, 4))]
    return {
        key_text(R.randint(1, 4)) if R.random() < 0.5 else text(R.randint(0, 5)): value(
            depth + 1
        )
        for _ in range(R.randint(0, 5))
    }


# ── cases ────────────────────────────────────────────────────────────────────
def canon_cases() -> list[dict]:
    objs = [value() for _ in range(600)]
    objs += [
        {
            "｡": 1,
            "\U0001f600": 2,
        },  # UTF-16 order puts the emoji first; code points don't
        {"": "", "a": [], "b": {}},
        {"ctl": "".join(chr(c) for c in range(0x20)) + '\x7f\\"/'},
        {"seps": "\u2028\u2029\u0085"},
        {"big": [2**64, -(2**64), 10**40]},
    ]
    out = []
    for o in objs:
        s = k.canon(o)
        out.append({"in": o, "canon": s, "sha256": k.h256(s)})
    return out


WORD_TEXTS = [
    "",
    "   ",
    "How much SUN do tomatoes want?!",
    "how_much_sun",
    "the one ending 672841",
    "Café crème brûlée",
    "Cafe\u0301 cre\u0300me",
    "İstanbul ISTANBUL ıstanbul",
    "ΟΔΟΣ οδός ΣΑΣ Σ ΑΣ. ΑΣ'Σ",
    "x² + y³ = ½ Ⅻ ٣ ߀",
    "中文日本語 한국어",
    "👍🏽 ok 👨\u200d👩\u200d👧 fam",
    "a\u00a0b\u2009c\u3000d\u200be\ufefff",
    "ＡＢＣ１２３",
    "ǅungla ǈubljana",
    "ẞTRASSE straße STRASSE",
    "line\u2028break\u2029para\u0085nel",
]


def words_cases() -> list[dict]:
    texts = WORD_TEXTS + [text(R.randint(0, 40)) for _ in range(800)]
    return [{"text": t, "words": k.words(t), "bite_key": k.bite_key(t)} for t in texts]


#: RFC 4231 HMAC-SHA-256 test cases 1-4, 6 and 7 (5 is truncated output)
RFC4231 = [
    (
        "0b" * 20,
        b"Hi There".hex(),
        "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7",
    ),
    (
        b"Jefe".hex(),
        b"what do ya want for nothing?".hex(),
        "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
    ),
    (
        "aa" * 20,
        "dd" * 50,
        "773ea91e36800e46854db8ebd09181a72959098b3ef8c122d9635514ced565fe",
    ),
    (
        "0102030405060708090a0b0c0d0e0f10111213141516171819",
        "cd" * 50,
        "82558a389a443c0ea4cc819899f2083a85f0faa3e578f8077a2e3ff46729665b",
    ),
    (
        "aa" * 131,
        b"Test Using Larger Than Block-Size Key - Hash Key First".hex(),
        "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54",
    ),
    (
        "aa" * 131,
        b"This is a test using a larger than block-size key and a larger than block-size data. The key needs to be hashed before being used by the HMAC algorithm.".hex(),
        "9b09ffa71b942fcb27635fbcd5b0e944bfdc63644f0713938a7f51535c3a35e2",
    ),
]


def hmac_cases() -> dict:
    import hmac as _hmac

    rfc = []
    for key, msg, want in RFC4231:
        got = _hmac.new(
            bytes.fromhex(key), bytes.fromhex(msg), hashlib.sha256
        ).hexdigest()
        assert got == want, "RFC 4231 vector disagrees with this Python"
        rfc.append({"key": key, "msg": msg, "mac": want})
    seals = []
    for _ in range(60):
        key = R.randbytes(32)
        subject = "proposal:" + k.h256(text(10))
        if R.random() < 0.2:
            subject = text(R.randint(0, 20))
        seals.append(
            {"key": key.hex(), "subject": subject, "proof": k.proof(key, subject)}
        )
    return {"rfc4231": rfc, "seal": seals}


HTML_TEXTS = [
    # the tag scanner's edges: folds, the backreference, the boundary
    "<\u017fcript>a</SCRIPT>b",
    "<SCRIPT>a</\u017fcript>b",
    "<script>a</SCRIPT>b",
    "<scr\u0131pt>dotless</scr\u0131pt>c",
    "<scr\u0131pt>dotless</scrIpt>c",
    "<scrIpt>a</scr\u0130pt>b",
    "<script_x>a</script>b",
    "<scripts>a</scripts>b",
    "<script1>a</script>b",
    "<script-x>a</script>b",
    "<script\u00e9>a</script>b",
    "<script>a</script\n\t\u3000>b",
    "<script>a</script b>c</script>d",
    "<script>one</script><style>two</style><!--three-->four",
    "<script><!-- x --></script>y",
    "<!-->a<!--->b<!---->c",
    "<!-- unclosed",
    "<script>a<script>b</script>c</script>d",
    "<>empty<a>",
    "&#x;&#;&#xg;&#12a;&#x1F600;&#128512;",
    "&" + "b" * 33 + ";",
    "&\U0001f600" * 3 + ";",
    "&notin&notit&not;&",
    "&amp;&amp&AMP&ampamp",
    "<script>x</script\u00a0>after",
    "<style>y</style\u3000\n>z",
    "<script>q</script\u2003\x1c>w",
    "<\u017fcript>long s</\u017fcript>tail",
    "<SCR\u0130PT>dotted</SCR\u0130PT>tail",
    "<p>The blue Civic,\n VIN 1HGEJ6672841, one owner.</p>",
    "<html><script>var lot = 1;\nvar x = 2;</script>\n<p>shown</p></html>",
    "<STYLE type=x>a{}\n</style >after<!-- a\ncomment -->end",
    "&amp; &AMP; &amp &ampx &notin; &notit; &noti &not",
    "&#62; &#x3e; &#X3E; &#0; &#x80; &#x9f; &#xD800; &#1114111; &#1114112; &#13; &#11;",
    "&#65 &#x41 &unknownentity; &; & ; &#; &#x;",
    "a<b>c</b>d<br/>e< f >g",
    "<!doctype html><button>x</button>",
    "&nbsp;&NewLine;&Tab;&lt;tag&gt;",
    "<a href='x\ny'>link</a>",
    "unterminated <tag",
    "<script>never closed",
    "&" + "a" * 40 + ";",
]


def html_cases() -> list[dict]:
    texts = HTML_TEXTS[:]
    names = sorted(html.entities.html5)
    for _ in range(300):
        parts = []
        for _ in range(R.randint(1, 6)):
            roll = R.random()
            if roll < 0.3:
                parts.append("&" + R.choice(names))
            elif roll < 0.45:
                parts.append(
                    f"&#{R.choice([R.randint(0, 200), R.randint(0, 0x110010)])}"
                    + R.choice([";", ""])
                )
            elif roll < 0.6:
                parts.append(f"&#x{R.randint(0, 0x11FFFF):x}" + R.choice([";", ""]))
            elif roll < 0.8:
                parts.append(
                    R.choice(
                        [
                            "<p>",
                            "</p>",
                            "<script>s\n</script>",
                            "<!--c-->",
                            "<b\n>",
                            "<style>x</style>",
                        ]
                    )
                )
            else:
                parts.append(text(R.randint(0, 8)))
        texts.append("".join(parts))
    return [{"in": t, "out": k.html_text(t)} for t in texts]


def holds_cases() -> list[dict]:
    out = []
    toks_pool = [
        "1hgej6672841",
        "672841",
        "abc",
        "a1b2",
        "x9",
        "２０２６",
        "²³",
        "order",
        "x1",
        "1234",
    ]
    for w in [
        "672841",
        "6728",
        "672",
        "abc",
        "a1b2",
        "1hge",
        "x9",
        "２０２６",
        "²³⁴⁵",
        "orders",
        "1234",
        "x1x1",
    ]:
        for _ in range(4):
            toks = R.sample(toks_pool, R.randint(0, 5))
            out.append({"w": w, "toks": sorted(toks), "out": k.holds(w, set(toks))})
    return out


def echo_cases() -> list[dict]:
    pairs = [
        ("has the shed been painted this year", "Has the shed been painted this year?"),
        (
            "so I was out back this morning and wondered: has the shed out back been painted this year",
            "Has the shed out back been painted this year or not, and by whom?",
        ),
        ("a b", "a b"),
        ("one two three", "x one two three y"),
        ("one two three", "one three two"),
    ]
    for _ in range(200):
        ws = [
            R.choice(
                [
                    "sun",
                    "tomato",
                    "water",
                    "root",
                    "shed",
                    "paint",
                    "year",
                    "the",
                    "Σ",
                    "ΟΔΟΣ",
                ]
            )
            for _ in range(R.randint(0, 12))
        ]
        bite = " ".join(ws)
        cut = R.randint(0, len(ws))
        text_ = " ".join(
            R.sample(ws, len(ws)) if R.random() < 0.5 else ws[cut:] + ["and"] + ws[:cut]
        )
        pairs.append((bite, text_))
    return [{"bite": b, "text": t, "out": k.echo(b, t)} for b, t in pairs]


# ── piles: folders written byte for byte ─────────────────────────────────────
def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def pile_cases() -> list[dict]:
    """Each pile: files as (path parts, bytes). `repeat` stands for a big file."""
    piles = []
    piles.append(
        [
            (["garden.md"], b"Tomatoes want six hours of sun.\n\nWater at the root.\n"),
            (
                ["lot.html"],
                b"<html><script>var lot = 1;</script>\n<p>The blue Civic, VIN 1HGEJ6672841, one owner.</p></html>\n",
            ),
            (["faq.md"], b"Has the shed been painted this year?\n"),
            (
                ["shed.md"],
                b"Has the shed out back been painted this year or not, and by whom?\n",
            ),
            (
                ["code", "util.py"],
                b"def frost_date(zone):\n    return zone\n\n\ndef unused():\n    pass\n",
            ),
            (
                ["code", "main.py"],
                b"from util import frost_date\nprint(frost_date(7))\n",
            ),
        ]
    )
    piles.append(
        [
            (["a-b"], b"dash file\n"),
            (["a", "b"], b"slash file\n"),
            (["a.b"], b"dot file\n"),
            (["｡.md".encode().decode()], "halfwidth name\n".encode()),
            (["\U0001f600.md"], "emoji name\n".encode()),
            (["Z.md"], b"capital\n"),
            (["_.md"], b"underscore\n"),
            ([".hidden", "x.md"], b"never read\n"),
            (["dir", ".dot.md"], b"never read either\n"),
            (["bin.dat"], b"text then \x00 nul\n"),
            (["latin1.txt"], "café\n".encode("latin-1")),
            (["bom.md"], "\ufeffBOM first\n".encode()),
            (["crlf.md"], b"one\r\ntwo\r\n\r\nthree\r\n"),
            (["seps.md"], "a\u2028b\u2029c\u0085d\x0be\x0cf\x1cg\n\nh\n".encode()),
            (["spaces.md"], "x\n\u3000\ny\n \t \nz\n".encode()),
            (["UPPER.HTML"], b"<b>bold</b>\n"),
            (["noeol.md"], b"no newline at the end"),
            (["long.md"], ("word " * 300 + "\n").encode()),
            (
                ["many.json"],
                "\n".join(f'"k{i}": "word{i} of {i}"' for i in range(400)).encode(),
            ),
            (
                ["mixed.md"],
                ("short\n" + "y" * 700 + "\n" + "z" * 499 + "\nend\n").encode(),
            ),
        ]
    )
    piles.append(
        [
            (
                ["big.md"],
                {"repeat": "a b c\n", "n": 200000},
            ),  # 1,200,000 bytes: over MAX_FILE
            (["edge.md"], {"repeat": "x", "n": 1_000_000}),  # exactly MAX_FILE: read
            (
                ["late-nul.md"],
                {"repeat": "y", "n": 9000, "then": "\x00"},
            ),  # NUL past SNIFF
            (["ok.md"], "Σ ΟΔΟΣ οδός\n".encode()),
        ]
    )
    rnd = []
    for i in range(25):
        name = f"f{i:02d}.md"
        paras = []
        for _ in range(R.randint(1, 4)):
            paras.append(
                " ".join(
                    R.choice(
                        [
                            "sun",
                            "tomato",
                            "water",
                            "root",
                            "frost",
                            "shed",
                            "paint",
                            "year",
                            "seven",
                            "bus",
                            "square",
                            "ferry",
                        ]
                    )
                    for _ in range(R.randint(1, 9))
                )
            )
        rnd.append(([name], ("\n\n".join(paras) + "\n").encode()))
    piles.append(rnd)
    # what the first vectors let through (planted in a copy of the Java port,
    # 2026-10-21): code points vs UTF-16 units at the chunk cap, NBSP and other
    # spaces Python strips and Java doesn't, and a tie broken by a path that
    # sorts one way by code point and the other by UTF-16 unit
    piles.append(
        [
            (["\uff61.md"], b"sun tomato\n"),
            (["\U0001f600.md"], b"tomato sun\n"),
            (["\U00010000.md"], b"tomato  sun\n"),
            (
                ["emoji.md"],
                ("\U0001f600" * 200 + "\n" + "\U0001f600" * 200 + "\nend\n").encode(),
            ),
            (["astral-long.md"], ("\U0001f642" * 600 + "\n").encode()),
            (["nbsp.md"], "one tomato\n\u00a0\ntwo tomato\n".encode()),
            (
                ["figure.md"],
                "three tomato\n\u2007\u202f\nfour tomato\n\x1c\nfive\n".encode(),
            ),
        ]
    )
    for pile, _ in FLOAT_TIES:
        piles.append(pile)
    # the definition scanner's edges (look): keywords, boundaries, spaces
    # Python counts as \s, names that are and aren't identifiers
    piles.append(
        [
            (
                ["code.txt"],
                (
                    "function foo() {}\nclass Bar:\nfn baz()\nsub qux\nfunc  quux\n"
                    "undef x\nxdef y\ndef\u3000spaced\ndef_x y\nfunctional thing\n"
                    "def a1_b2\nclass _Hidden\nfn\nnewline_name\ndef \u00e9x\n"
                    "def foo\ndef 9bad\nfunction\tfoo\n"
                ).encode(),
            ),
            (
                ["use.txt"],
                b"foo() and foo_bar and afoo and Bar() and quux\nspaced a1_b2\n",
            ),
            (["other.txt"], b"baz qux _Hidden newline_name\n"),
            # names only as the front or back of a longer word: never a use
            (["prefix.txt"], b"quuxify Barn _Hiddenness bazooka afoo spacedout\n"),
        ]
    )
    # dense ties: a small vocabulary, so many chunks score alike and the order
    # falls to exact arithmetic, then path, then line
    vocab = [f"w{i}" for i in range(7)]
    piles.append(
        [
            (
                [f"t{i:02d}.md"],
                (" ".join(R.sample(vocab, R.randint(2, 6))) + "\n").encode(),
            )
            for i in range(30)
        ]
    )
    piles.append(dated_pile())
    piles.append(ledger_pile())
    piles.append(series_pile())
    return piles


def series_pile() -> list:
    """A care log the kernel reads as numbers over time, and a ledger it
    doesn't (dates repeat), each long enough to be cut into chunks."""
    log = "date,weight_kg,pulse,notes\n" + "".join(
        f"2026-{9 + (d > 30):02d}-{d - 30 * (d > 30):02d},{71 + d / 10:.1f},{60 + d % 7},"
        + ('"ankles, puffy"' if d == 40 else "")
        + "\n"
        for d in range(1, 52)
    )
    books = "date,what,amount\n" + "".join(
        f"2026-10-{1 + d // 3:02d},weight {d},{d * 5}\n" for d in range(40)
    )
    return [(["dad", "care-log.csv"], log.encode()), (["books.csv"], books.encode())]


def ledger_pile() -> list:
    """What isn't there: orders the books name all but two of (and a calendar
    names together), a case number named once beside nothing, a piano model
    and a van that share a shape and never a page, phones the texts don't
    have, and a bare `-/-` run."""
    pos = [f"2025-{n:03d}" for n in range(10, 22)]
    paid = [p for p in pos if p not in ("2025-013", "2025-019")]
    return [
        (["books.csv"], ("".join(f"DEP PO {p}\n" for p in paid)).encode()),
        (["calendar.txt"], (" ".join(f"PO {p}" for p in pos) + "\n").encode()),
        *[
            (["mail", f"po-{p}.txt"], f"PURCHASE ORDER PO {p}\n".encode())
            for p in pos[:4]
        ],
        (["letter.txt"], b"RE: Case No. WR-2026-0117\n\nreply within 180 days\n"),
        (["reply.txt"], b"my reply, Case No. WR-2024-0390\n"),
        (["piano.txt"], b"Kawai K-300, purchase order pending\n"),
        (["van.txt"], b"Econoline E-150 rider\n"),
        (["texts.txt"], b"555-0120 555-0121 555-0122 order\n"),
        (["pharmacy.txt"], b"call 555-0190 about the order\n"),
        (["marks.txt"], b"-/- --2025-013-- a/b 1/300 2026-10-14 x-1-\n"),
        # a pair named together, and a file with one of them: half is enough
        (["both.txt"], b"auction: lot AB-11 and lot AB-12\n"),
        (["one.txt"], b"sold lot AB-11\n"),
    ]


#: newest's own draw, apart from R, so adding it moved no earlier case
RN = random.Random(20261022)


def dated_pile() -> list:
    """A log whose date headings fall in one chunk and cover the next, memos
    dated only by name, a calendar's compact dates, and undated files: equal
    scores fall to the newest, then the path, then a dated chunk's later line."""
    log = []
    for y in (2021, 2026):
        for m, d in ((3, 12), (9, 27), (10, 12)):
            log.append(f"== {y}-{m:02d}-{d:02d}")
            log += [
                f"  H{h}: bees frames {RN.randint(2, 9)}, eggs yes" for h in range(1, 9)
            ]
    memos = [
        (["memos", f"2026-{m:02d}-{d:02d}_2310.txt"], b"Okay. bees fine\n")
        for m, d in ((4, 16), (10, 9), (9, 19))
    ]
    return [
        (["hives.txt"], ("\n".join(log) + "\n").encode()),
        *memos,
        (["cal.ics"], b"DTSTART;VALUE=DATE:20250828\nSUMMARY:bees inspection\n"),
        (["undated.md"], b"bees okay, no date here\n\nbees okay again\n"),
        (["phone.txt"], b"bees 2026-10-149 and 12026-10-14 and 20261332\n"),
    ]


#: Bites run with a words list (their word -> their folder's words), by pile:
#: the garden's and the dated pile's, a word the list reads as one that's
#: there, one it reads as one that isn't, and one already there itself.
ALIASED = {
    0: [
        ("when does the cold snap come", {"cold": ["frost"], "snap": ["date"]}),
        ("how much sunlight", {"sunlight": ["sun", "light"]}),
        ("how much sunlight", {"sunlight": ["moonlight"]}),
        ("tomatoes want sun", {"tomatoes": ["sun"], "sun": ["water"]}),
        ("water the shed", {"shed": ["garden", "root"], "zzz": ["water"]}),
    ],
    10: [
        ("how are the hives", {"hives": ["bees"]}),
        ("is everything okay", {"everything": ["eggs", "frames"]}),
        ("bees calm", {"calm": ["okay", "fine"]}),
    ],
}


def _table(head: str, start: str, n: int, step: int, cols) -> str:
    """A dated table: `n` rows from `start` (YYYY-MM-DD), `step` days apart."""
    y, m, d = map(int, start.split("-"))
    first = k.day(f"{y:04d}{m:02d}{d:02d}")
    rows = [head]
    for i in range(n):
        x = first + i * step + 719468  # back to a civil date (Hinnant's)
        era = x // 146097
        doe = x - era * 146097
        yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
        doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
        mp = (5 * doy + 2) // 153
        dd = doy - (153 * mp + 2) // 5 + 1
        mm = mp + 3 if mp < 10 else mp - 9
        yy = yoe + era * 400 + (mm <= 2)
        rows.append(f"{yy:04d}-{mm:02d}-{dd:02d}," + ",".join(c(i) for c in cols))
    return "\n".join(rows) + "\n"


def series_cases() -> list[dict]:
    hand = [
        (
            "care.csv",
            "date,weight_kg,bp,pulse,notes\n2026-10-01,71.2,120/70,60,\n"
            '2026-10-08,71.3,118/72,62,\n2026-10-15,73.8,121/70,61,"ankles, puffy"\n'
            "2026-10-21,75.1,112/78,64,\n",
        ),
        (
            "ledger.csv",
            "date,what,in\n2026-10-01,a,5\n2026-10-01,b,6\n2026-10-09,c,7\n",
        ),
        ("back.csv", "date,v\n2026-10-09,1\n2026-10-01,2\n2026-10-20,3\n"),
        ("short.csv", "date,v\n2026-10-01,1\n"),
        ("nohead.csv", "2026-10-01,1\n2026-10-09,2\n2026-10-20,3\n"),
        ("onecol.csv", "date\n2026-10-01\n2026-10-09\n"),
        ("leap.csv", "day,v\n2024-02-29,1.5\n2024-03-07,1.25\n"),
        ("compact.csv", "d,v\n20261001,-3\n20261008,-3\n20261016,-0.5\n"),
        ("places.csv", "d,v\n2026-10-01,0.0000000000001\n2026-10-09,0.0000000000003\n"),
        (
            "ties.csv",
            "d,v\n2026-09-01,0\n2026-09-08,2\n2026-09-15,0\n2026-09-22,2\n"
            "2026-09-29,0\n2026-10-06,2\n2026-10-13,0\n2026-10-20,1\n",
        ),
        ("gaps.csv", "d,a,b\n2026-10-01,1,\n2026-10-09,,x\n2026-10-20,3,1.\n"),
        ("quoted.csv", 'd,"a, b","q""x"\n2026-10-01,"1","2"\n2026-10-09," 4 ",5\n'),
        ("spaced.csv", "d , v \n 2026-10-01 , 1\n\n 2026-10-09 , 2 \n"),
        ("odd.csv", "d,v\n2026-10-01x,1\n2026-10-09,1\n2026-10-17,1\n"),
        (
            "big.csv",
            "d,v\n2026-10-01,99999999999999999999\n2026-10-09,-99999999999999999999\n",
        ),
        ("old.csv", "d,v\n1899-12-25,1\n1999-12-25,2\n2000-01-01,3\n"),
        ("unicode.csv", "d,v\n2026-10-01,\u0661\n2026-10-09,\uff11\n2026-10-17,1\n"),
    ]
    tables = [
        _table("date,w,p", "2025-08-18", 430, 1,
               [lambda i: f"{71 + RN.randint(-9, 30) / 10:.1f}", lambda i: str(RN.randint(55, 80))]),
        _table("date,x", "2023-12-20", 40, 3, [lambda i: str(RN.randint(-5, 5))]),
    ]  # fmt: skip
    for n in range(40):
        cols = [
            lambda i: RN.choice(["", "1", "-2", "3.5", "0.25", "x", "10", "-0.75"])
            for _ in range(RN.randint(1, 3))
        ]
        tables.append(
            _table("d," + ",".join(f"c{j}" for j in range(len(cols))),
                   RN.choice(["2026-01-01", "2024-02-20", "1999-12-01"]),
                   RN.randint(2, 30), RN.randint(1, 5), cols)
        )  # fmt: skip
    hand += [(f"t{n}.csv", t) for n, t in enumerate(tables)]
    return [{"path": p, "text": t, "out": k.series(p, t)} for p, t in hand]


def ids_cases() -> list[dict]:
    texts = [
        "",
        "DCSD PO 2025-063 (575) 555-0161 2026-10-16 WR-2026-0117",
        "mites 1/300 -x- a-b 4-5 -2025-063- K-52 caf\u00e9-9",
        "--",
        "-/-",
        "a/b/c",
        "2026-10-14",
        "2026-10-14-5",
        "2026-1014",
        "x2026-10-14",
        "20261014-1",
        "\u0662-\u0663",
        "1-\u0662",
        "２-3",
        "A-1 A-1 a-1",
        "PO#2025-063.",
        "x_1-2",
    ]
    bits = [
        "2025",
        "-",
        "/",
        "063",
        "WR",
        "a",
        "1",
        " ",
        ".",
        "_",
        "\u00e9",
        "10",
        "14",
    ]
    texts += [
        "".join(RN.choice(bits) for _ in range(RN.randint(1, 12))) for _ in range(250)
    ]
    return [
        {"text": t, "ids": k.ids(t), "shapes": [k.shape(i) for i in k.ids(t)]}
        for t in texts
    ]


def wordlist_cases() -> list[dict]:
    texts = [
        "",
        "due = next",
        "due = next, next\nDue = overdue",
        "due =",
        "due = next,",
        "a b = c",
        "due next",
        "# x\n\n  due=next  ",
        "due = due, next",
        "due = 2026-10",
        "=next",
        "due = next = soon",
        "due = next\r\nlate = overdue",
        "DUE = Next, NEXT",
        "café = cafe",
        "due = next\u2028late = over",
        "  # only a comment",
        "due = next,,soon",
        "due\t=\tnext",
        "１ = one",
        # written out of order: the list is held sorted, by code point
        "due = zebra, apple",
        "late = overdue, behind\nlate = after",
        "x = é, e, z, É",
    ]
    bits = [
        "due",
        "next",
        " ",
        "=",
        ",",
        "#",
        "\n",
        "late",
        "x y",
        "\u00e9",
        "2026",
        "\t",
    ]
    texts += [
        "".join(RN.choice(bits) for _ in range(RN.randint(1, 14))) for _ in range(200)
    ]
    out = []
    for t in texts:
        alias, why = k.parse_words(t)
        out.append({"text": t, "alias": alias, "why": why})
    return out


def newest_cases() -> list[dict]:
    texts = [
        "",
        "2026-10-14",
        "20261014",
        "2026-10-14 and 2021-03-12",
        "DTSTART;VALUE=DATE:20250828",
        "memos/2026-05-10_2310.txt",
        "12026-10-14",
        "2026-10-149",
        "2026-13-01",
        "2026-00-10",
        "2026-01-00",
        "2026-01-32",
        "1899-12-31",
        "2100-01-01",
        "1900-01-01",
        "2099-12-31",
        "2026-1-01",
        "2026-10-1",
        "000123456789",
        "20261322",
        "2026-10-14-5",
        "x2099-12-31y",
        "\u0662\u0660\u0662\u0666-10-14",
        "２０２６-10-14",
        "2026\u201110\u201114",
        "2026-10-14\U0001f600 2027-01-01",
        "202610140",
    ]
    bits = ["2026", "1999", "-", "10", "13", "00", "31", "32", "20261014", "1", "x",
            " ", "\U0001F600", "\u0663", "19000101", "0"]  # fmt: skip
    texts += [
        "".join(RN.choice(bits) for _ in range(RN.randint(1, 12))) for _ in range(300)
    ]
    return [{"text": t, "newest": k.newest(t)} for t in texts]


def _fold(xs) -> float:
    total = 0.0
    for x in xs:
        total += x
    return total


def float_tie_piles(want: int = 3) -> list[tuple[list, str]]:
    """Piles where two chunks tie exactly but a plain float sum, walked in
    either order, tells them apart: a port that scores in floats ranks them
    by an accident of rounding, not by path. Found by a seeded search."""
    r = random.Random(7)
    found = []
    while len(found) < want:
        vocab = [f"v{i}" for i in range(r.randint(8, 10))]
        texts = [" ".join(r.sample(vocab, r.randint(2, 7))) for _ in range(40)]
        bite = " ".join(vocab)
        want_ws = set(vocab)
        held = [(t, set(k.words(t)) & want_ws) for t in texts]
        held = [(t, h) for t, h in held if len(h) * 2 >= len(want_ws)]
        df = {w: sum(w in h for _, h in held) for w in want_ws}
        by_score: dict = {}
        for _, h in held:
            exact = sum(Fraction(1, df[w]) for w in h)
            fl = {
                _fold(1 / df[w] for w in sorted(h)),
                _fold(1 / df[w] for w in sorted(h, reverse=True)),
            }
            by_score.setdefault(exact, []).append(fl)
        # the tie must be where rank looks: the top three, or the first one out
        cut = sorted((x for x, g in by_score.items() for _ in g), reverse=True)
        floor = cut[min(k.TOP, len(cut) - 1)]
        if any(
            score >= floor and len(group) > 1 and not set.intersection(*group)
            for score, group in by_score.items()
        ):
            pile = [
                ([f"t{i:02d}.md"], (t + "\n").encode()) for i, t in enumerate(texts)
            ]
            found.append((pile, bite))
    return found


FLOAT_TIES = float_tie_piles()


def extra_bites(i: int) -> list[str]:
    """More bites: the float-tie piles' own bite (piles 5-7), the code pile's
    (8), random handfuls of the dense-ties pile's vocabulary (9), the dated
    pile's (10), the ledger's (11) and the care log's (12)."""
    if 5 <= i <= 7:
        return [FLOAT_TIES[i - 5][1]]
    if i == 8:
        return [
            "function foo class bar fn baz sub qux func quux",
            "def spaced a1_b2 hidden newline_name",
            "foo bar baz qux quux",
            "def x y functional thing",
        ]
    if i == 10:
        return ["bees", "okay", "bees okay", "eggs yes", "frames", "inspection"]
    if i == 12:
        return ["weight", "ankles", "pulse", "what amount", "weight kg"]
    if i == 11:
        return ["purchase order", "order", "reply", "case", "rider", "Kawai", "call",
                "DEP", "lot", "auction"]  # fmt: skip
    if i != 9:
        return []
    vocab = [f"w{n}" for n in range(7)]
    return [" ".join(R.sample(vocab, R.randint(3, 7))) for _ in range(150)]


def materialize(pile, root: Path) -> None:
    for parts, data in pile:
        p = root.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, dict):
            body = data["repeat"] * data["n"] + data.get("then", "")
            p.write_bytes(body.encode())
        else:
            p.write_bytes(data)


def encode_pile(pile) -> list[dict]:
    out = []
    for parts, data in pile:
        if isinstance(data, dict):
            out.append(
                {
                    "parts": parts,
                    "repeat": data["repeat"],
                    "n": data["n"],
                    "then": data.get("then", ""),
                }
            )
        else:
            out.append({"parts": parts, "b64": b64(data)})
    return out


BITES = [
    "how much sun do tomatoes want",
    "when does the frost come",
    "the one ending 672841",
    "has the shed been painted this year",
    "what does this do",
    "frost_date unused",
    "make me a frost calendar",
    "bus square seven",
    "sun water root",
    "ΟΔΟΣ",
    "word0 word1 word2",
    "dash slash dot",
    "BOM first",
    "sun tomato",
    "tomato one two three four",
]


def pile_based_cases() -> dict:
    chunks_out, rank_out, look_out = [], [], []
    for i, pile in enumerate(pile_cases()):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pile"
            root.mkdir()
            materialize(pile, root)
            ch = k.chunks(root)
            chunks_out.append({"pile": encode_pile(pile), "chunks": ch})
            bites = BITES + [" ".join(R.sample(BITES, 2))[:60] for _ in range(10)]
            bites += extra_bites(i)
            runs = [(b, {}) for b in bites] + [(b, a) for b, a in ALIASED.get(i, [])]
            for bite, alias in runs:
                served = k.rank(ch, bite, alias)
                rank_out.append(
                    {"pile": i, "bite": bite, "alias": alias,
                     "ids": [c["id"] for c in served]}
                )  # fmt: skip
                answers = [c for c in served if not k.echo(bite, c["text"])]
                # served by place in the pile, not by id: two chunks with the
                # same text share an id, and look() tells them apart by identity
                at = [next(n for n, x in enumerate(ch) if x is c) for c in answers]
                look_out.append(
                    {
                        "pile": i,
                        "bite": bite,
                        "alias": alias,
                        "served": at,
                        "look": k.look(ch, bite, answers, alias),
                    }
                )
    return {"chunks": chunks_out, "rank": rank_out, "look": look_out}


def door_cases() -> list[dict]:
    good = {"path": "notes/a.md", "data": "x", "cites": ["c1"], "claim": "why"}
    lines = [
        json.dumps(good),
        json.dumps({**good, "cites": []}),
        json.dumps({**good, "cites": ["c2"]}),
        json.dumps({**good, "path": "/abs"}),
        json.dumps({**good, "path": "\\win"}),
        json.dumps({**good, "path": "a/../b"}),
        json.dumps({**good, "path": "a/./b"}),
        json.dumps({**good, "path": ".."}),
        json.dumps({**good, "path": "a/..b"}),
        json.dumps({**good, "path": ""}),
        json.dumps({**good, "path": 5}),
        json.dumps({**good, "path": True}),
        json.dumps({**good, "data": None}),
        json.dumps({**good, "cites": "c1"}),
        json.dumps({**good, "claim": ""}),
        json.dumps({**good, "claim": " \t"}),
        json.dumps({**good, "claim": "\u3000"}),
        json.dumps({**good, "claim": "\u200b"}),  # not whitespace to Python
        json.dumps({**good, "claim": "\u00a0"}),  # whitespace to Python, not to Java
        json.dumps({**good, "claim": "\u2007\u202f"}),
        json.dumps({**good, "claim": "\x1c\x1d\x1e\x1f"}),
        json.dumps({**good, "extra": 1}),
        json.dumps({k_: v for k_, v in good.items() if k_ != "claim"}),
        '{"path": "a.md", "path": "b.md", "data": "x", "cites": [], "claim": "dup keys: last wins"}',
        '{"path": "a.md", "data": "x", "cites": [NaN], "claim": "NaN parses"}',
        '{"path": "a.md", "data": "x", "cites": [1.5, 2], "claim": "numbers"}',
        '["not", "an", "object"]',
        "not json",
        "",
        "  " + json.dumps(good) + "  ",
        '{"path": "a.md", "data": "x", "cites": ["c1"], "claim": "\\ud83d\\ude00 escaped pair"}',
    ]
    out = []
    for line in lines:
        try:
            prop = json.loads(line)
        except ValueError:
            prop = None
        out.append(
            {"line": line, "served": ["c1", "c3"], "why": k.door(prop, {"c1", "c3"})}
        )
    return out


def subject_cases() -> list[dict]:
    out = []
    for _ in range(150):
        prop = {
            "path": text(R.randint(1, 10)),
            "data": text(R.randint(0, 30)),
            "cites": [k.h256(text(4)) for _ in range(R.randint(0, 3))],
            "claim": text(R.randint(1, 15)),
        }
        out.append({"prop": prop, "subject": k.subject_of(prop)})
    return out


def _write(path: Path, doc: dict) -> str:
    data = json.dumps(doc, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    path.write_text(data + "\n", encoding="ascii")
    return data


def main(argv: list[str]) -> int:
    """Writes kernel_tables.json (what a port loads at run time) and
    cases.json (what it must answer), beside this file or in `argv[0]`."""
    out = Path(argv[0]) if argv else HERE
    out.mkdir(parents=True, exist_ok=True)
    tab = tables()
    tdata = _write(out / "kernel_tables.json", tab)
    cases = {
        "canon": canon_cases(),
        "words": words_cases(),
        "hmac": hmac_cases(),
        "html": html_cases(),
        "holds": holds_cases(),
        "echo": echo_cases(),
        **pile_based_cases(),
        "newest": newest_cases(),
        "wordlist": wordlist_cases(),
        "ids": ids_cases(),
        "series": series_cases(),
        "door": door_cases(),
        "subject": subject_cases(),
    }
    doc = {
        "about": "kernel.py vectors; see gen.py. Regenerate, never edit.",
        "kernel_sha256": tab["kernel_sha256"],
        "tables_sha256": hashlib.sha256((tdata + "\n").encode()).hexdigest(),
        "cases": cases,
    }
    data = _write(out / "cases.json", doc)
    n = sum(
        len(v) if isinstance(v, list) else sum(len(x) for x in v.values())
        for v in cases.values()
    )
    print(
        f"cases.json: {n} cases, {len(data):,} bytes; kernel_tables.json:"
        f" {len(tdata):,} bytes; kernel {tab['kernel_sha256'][:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
