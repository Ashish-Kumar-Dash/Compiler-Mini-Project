"""ADC-code lowering (Architecture §7, ADR-17): exact integer tests on sensor codes.

For a threshold T and a sensor's code map x(c), let r = x^-1(T) as a Fraction. Then
    x >  T  <=>  c >= floor(r) + 1        x >= T  <=>  c >= ceil(r)
    x <  T  <=>  c <= ceil(r) - 1         x <= T  <=>  c <= floor(r)
    x == T  <=>  c == r  if r is an integer, else never.
These hold because x is strictly increasing in c and c is an integer.
"""
from __future__ import annotations

import math
from fractions import Fraction

from .ir import AvgTest, CodeRange, Var

NEVER = (1, 0)   # canonical empty code range


def bounds(op: str, r: Fraction) -> tuple[int | None, int | None] | None:
    """Integer code bounds (lo, hi) for `x OP T` with r = x^-1(T); None = never holds."""
    r = Fraction(r)
    if op == ">":
        return math.floor(r) + 1, None
    if op == ">=":
        return math.ceil(r), None
    if op == "<":
        return None, math.ceil(r) - 1
    if op == "<=":
        return None, math.floor(r)
    if op == "==":
        if r.denominator != 1:
            return None
        return int(r), int(r)
    raise ValueError(op)


def merge(var: Var, parts: list[tuple[int | None, int | None] | None], adc_max: int) -> CodeRange:
    """Intersect several bounds on one variable, clamped to [0, adc_max]."""
    lo: int | None = None
    hi: int | None = None
    for b in parts:
        if b is None:
            return CodeRange(var, *NEVER)
        blo, bhi = b
        if blo is not None:
            lo = blo if lo is None else max(lo, blo)
        if bhi is not None:
            hi = bhi if hi is None else min(hi, bhi)
    if (lo is not None and lo > adc_max) or (hi is not None and hi < 0) or \
            (lo is not None and hi is not None and lo > hi):
        return CodeRange(var, *NEVER)
    if lo is not None and lo <= 0:
        lo = None
    if hi is not None and hi >= adc_max:
        hi = None
    return CodeRange(var, lo, hi)


def avg_test(var: Var, op: str, r: Fraction, adc_max: int) -> AvgTest:
    """`avg OP r` as `q*sum OP p*count`, with r clamped into [-1, A+1] (results unchanged for
    averages in [0, A]) and reduced to lowest terms."""
    r = min(max(Fraction(r), Fraction(-1)), Fraction(adc_max + 1))
    return AvgTest(var, op, r.numerator, r.denominator)


def code_range_holds(t: CodeRange, c: int) -> bool:
    if t.never:
        return False
    return (t.lo is None or c >= t.lo) and (t.hi is None or c <= t.hi)


def avg_holds(t: AvgTest, total: int, count: int) -> bool:
    if count == 0:
        return False
    lhs, rhs = t.q * total, t.p * count
    return {">": lhs > rhs, ">=": lhs >= rhs, "<": lhs < rhs, "<=": lhs <= rhs, "==": lhs == rhs}[t.op]


def describe(t: CodeRange) -> str:
    """'code >= 466', 'code <= 433', '434 <= code <= 465', 'never', 'always'."""
    if t.never:
        return "never (no ADC code)"
    if t.lo is None and t.hi is None:
        return "always"
    if t.lo is not None and t.hi is not None:
        return f"code == {t.lo}" if t.lo == t.hi else f"{t.lo} <= code <= {t.hi}"
    return f"code >= {t.lo}" if t.lo is not None else f"code <= {t.hi}"
