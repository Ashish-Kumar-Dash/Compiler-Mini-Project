"""Deterministic generators for property tests (stdlib only; hypothesis is optional)."""
from __future__ import annotations

import random
from fractions import Fraction

from iotc.ir import Interval

SEEDS = range(20)


def rng(seed: int) -> random.Random:
    return random.Random(seed * 7919 + 17)


def frac(r: random.Random, lo: int = -20, hi: int = 20) -> Fraction:
    """Small fractions, biased toward integers and halves so that boundaries collide often."""
    choice = r.random()
    if choice < 0.5:
        return Fraction(r.randint(lo, hi))
    if choice < 0.8:
        return Fraction(r.randint(2 * lo, 2 * hi), 2)
    return Fraction(r.randint(10 * lo, 10 * hi), r.choice([3, 7, 10]))


def interval(r: random.Random, integer: bool = False) -> Interval:
    a, b = frac(r), frac(r)
    if r.random() < 0.85 and a > b:
        a, b = b, a
    return Interval.make(a, r.random() < 0.5, b, r.random() < 0.5, integer)


def grid(ivs: list[Interval]) -> list[Fraction]:
    """Probe points: every endpoint, +-1/1000 around it, and midpoints."""
    pts: set[Fraction] = set()
    for iv in ivs:
        for v in (iv.lo.value, iv.hi.value):
            pts.update({v, v - Fraction(1, 1000), v + Fraction(1, 1000)})
    ordered = sorted(pts)
    pts.update((a + b) / 2 for a, b in zip(ordered, ordered[1:]))
    return sorted(pts)
