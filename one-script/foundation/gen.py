"""Build the comparison cases once, so every interpreter loads the same objects.

    python3 gen.py [cases.pkl]

Run it once, under any one Python, and hand the same file to every probe: if
each interpreter generated its own cases, a change in `random` would look like
a change in `json`. Pickle protocol 4 loads on 3.9.
"""

import pickle
import random
import struct
import sys

R = random.Random(20201004)
POOLS = [
    (0x00, 0x20),  # controls
    (0x20, 0x7F),  # printable ASCII
    (0x7F, 0xA0),  # DEL and C1 controls
    (0xA0, 0x800),
    (0x2028, 0x202A),  # line and paragraph separators
    (0xD800, 0xE000),  # lone surrogates
    (0xE000, 0x10000),
    (0x10000, 0x110000),  # beyond the basic plane
]
SURROGATES = False  # on for about one object in ten; more drowns out the hash


def text(n=8):
    out = []
    pools = POOLS if SURROGATES else [p for p in POOLS if p[0] != 0xD800]
    for _ in range(R.randint(0, n)):
        lo, hi = R.choice(pools)
        out.append(chr(R.randrange(lo, hi)))
    return "".join(out)


def number():
    k = R.randint(0, 5)
    if k == 0:
        return struct.unpack("<d", R.getrandbits(64).to_bytes(8, "little"))[0]
    if k == 1:
        return R.choice([0.0, -0.0, float("inf"), float("-inf"), float("nan"), 5e-324])
    if k == 2:
        return R.randint(-(2**70), 2**70)
    if k == 3:
        return R.random() * 10 ** R.randint(-30, 30)
    if k == 4:
        return R.choice([True, False, None])
    return 10 ** R.randint(4290, 4310)  # around the int-to-text limit (3.11+)


def value(depth=0):
    k = R.randint(0, 4 if depth < 3 else 1)
    if k == 0:
        return text()
    if k == 1:
        return number()
    if k == 2:
        return [value(depth + 1) for _ in range(R.randint(0, 3))]
    return {text(): value(depth + 1) for _ in range(R.randint(0, 3))}


def obj():
    global SURROGATES
    SURROGATES = R.random() < 0.1
    return {text(): value() for _ in range(R.randint(1, 4))}


# Keys are strings only: keys out of json.loads always are.
objects = [obj() for _ in range(3000)]
lines = [
    '{"a":1}',
    '{"a":NaN}',
    '{"a":Infinity}',
    '{"a":-Infinity}',
    '{"a":1e400}',
    '{"a":-0.0}',
    '{"a":1,"a":2}',
    "﻿{}",
    '{"a":1}\x00',
    '{"a":1} ',
    '{"a":"\\ud800"}',
    '{"a":"\\u0000"}',
    '{"a":' + "9" * 5000 + "}",
    '{"a":' + "9" * 4300 + "}",
    "1",
    "[]",
    "null",
    '"x"',
    "{'a':1}",
    '{"a":1,}',
    '{"a":0x10}',
    '{"a":01}',
]

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "cases.pkl"
    with open(out, "wb") as f:
        pickle.dump({"objects": objects, "lines": lines}, f, protocol=4)
    print(len(objects), "objects,", len(lines), "lines ->", out)
