#!/usr/bin/env python3
"""Asymmetric offsets: (b - k) b^i = (c - m) c^j, a puzzle from the operator's
Discord group, 2026-10-22.

    python3 compiled/offsets.py K M [--bmax B] [--digits D]

Each solution is one number written as a single digit and zeros in two bases:
3 * 6^9 = 16 * 18^5 = 30,233,088 is 3000000000 in base 6 and (16)00000 in base
18. The group's claim was "only one solution" for (3, 2) and for (8, 5). A
search can't prove that; it can say exactly how far it looked. So the answer
is the claim with its bound attached: every solution with b <= B and the value
below 10^D, b > k, c > m, b < c, i, j >= 1.

Why they are rare (`valuations`): for a prime p dividing c but not m, p does
not divide c - m, so p's exponent in the value is exactly j * v_p(c); the same
on b's side. b and c must share their primes, each offset must be made of
those primes, and every prime gives one linear equation in i and j, two
unknowns. The bound matters: offsets (9, 2) have a small solution (12, 3, 18,
2) and then a 40-digit one, (54, 22, 162, 17).

Stdlib only.
"""

from __future__ import annotations

import argparse
import sys


def c_for(n: int, m: int, j: int, lo: int) -> int | None:
    """The c >= lo with (c - m) c^j == n, or None; the left side grows with c."""
    hi = lo + 1
    while (hi - m) * hi**j < n:
        hi *= 2
    while lo < hi:
        mid = (lo + hi) // 2
        if (mid - m) * mid**j < n:
            lo = mid + 1
        else:
            hi = mid
    return lo if (lo - m) * lo**j == n else None


def solve(k: int, m: int, bmax: int, digits: int) -> list[tuple[int, int, int, int]]:
    """Every (b, i, c, j) with b <= bmax and the value below 10**digits."""
    limit, out = 10**digits, []
    for b in range(k + 1, bmax + 1):
        lo = max(b + 1, m + 1)
        n, i = (b - k) * b, 1
        while n < limit:
            j = 1
            while (lo - m) * lo**j <= n:
                if (c := c_for(n, m, j, lo)) is not None:
                    out.append((b, i, c, j))
                j += 1
            n, i = n * b, i + 1
    return out


def factor(n: int) -> dict[int, int]:
    out, p = {}, 2
    while p * p <= n:
        while n % p == 0:
            out[p] = out.get(p, 0) + 1
            n //= p
        p += 1
    if n > 1:
        out[n] = out.get(n, 0) + 1
    return out


def valuations(k: int, m: int, b: int, i: int, c: int, j: int) -> dict[int, tuple]:
    """Each prime of the value: its exponent from b's side and from c's side."""
    fb, fbk, fc, fcm = factor(b), factor(b - k), factor(c), factor(c - m)
    primes = sorted(set(fb) | set(fbk) | set(fc) | set(fcm))
    return {
        p: (fbk.get(p, 0) + i * fb.get(p, 0), fcm.get(p, 0) + j * fc.get(p, 0))
        for p in primes
    }


def digit_and_zeros(d: int, base: int, zeros: int) -> str:
    """One digit (in decimal, in brackets past 9) and its zeros, in `base`."""
    return (str(d) if d < 10 else f"({d})") + "0" * zeros + f" base {base}"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="offsets", description=__doc__.split("\n\n")[0])
    p.add_argument("k", type=int)
    p.add_argument("m", type=int)
    p.add_argument("--bmax", type=int, default=3000)
    p.add_argument("--digits", type=int, default=60)
    a = p.parse_args(argv)
    sols = solve(a.k, a.m, a.bmax, a.digits)
    print(
        f"{len(sols)} solution(s) of (b-{a.k}) b^i = (c-{a.m}) c^j"
        f" with b <= {a.bmax} and the value below 10^{a.digits}"
    )
    for b, i, c, j in sols:
        n = (b - a.k) * b**i
        print(f"  (b, i, c, j) = ({b}, {i}, {c}, {j})  value {n:,}")
        print(
            f"    {digit_and_zeros(b - a.k, b, i)} = {digit_and_zeros(c - a.m, c, j)}"
        )
        for q, (left, right) in valuations(a.k, a.m, b, i, c, j).items():
            print(f"    {q}^{left} on b's side, {q}^{right} on c's")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
