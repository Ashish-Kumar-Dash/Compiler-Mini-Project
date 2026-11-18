"""Tests for runtime.window.Window (Architecture §9.5, ADR-14; §7 avg tests; §13.2 property
tests against a brute-force model)."""
from __future__ import annotations

import operator
import random
from fractions import Fraction

import pytest

from iotc.runtime.window import Window
from tests.gen import SEEDS, rng

OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq}
SAMPLE_MS = (50, 100, 200, 250)
SEQUENCES_PER_SEED = 100


class Model:
    """Brute force: keep every push; aggregate samples whose bucket id is in (k* - B, k*]."""

    def __init__(self, window_ms: int, sample_ms: int) -> None:
        self.w = window_ms
        self.b = min(32, window_ms // sample_ms)
        self.samples: list[tuple[int, int]] = []

    def k(self, t: int) -> int:
        return t * self.b // self.w

    def push(self, t: int, code: int) -> None:
        self.samples.append((t, code))

    def codes(self) -> list[int]:
        if not self.samples:
            return []
        k_star = self.k(self.samples[-1][0])
        return [c for t, c in self.samples if k_star - self.b < self.k(t) <= k_star]


def random_window(r: random.Random) -> Window:
    s = r.choice(SAMPLE_MS)
    if r.random() < 0.4:
        w = s * r.randint(1, 40) + r.randint(0, s - 1)  # B < 32 and W not a multiple of S
    else:
        w = r.randint(s, 600_000)
    return Window(w, s)


def random_start(r: random.Random, s: int) -> int:
    choice = r.random()
    if choice < 0.3:
        return 0
    if choice < 0.5:
        return s * r.randint(0, 10_000)
    if choice < 0.8:
        return (2**32 // s - r.randint(0, 50)) * s  # crosses 2**32 during the sequence
    return s * r.randint(2**32 // s, 2**45 // s)


def random_gap(r: random.Random, w: Window) -> int:
    s, per_window = w.sample_ms, w.window_ms // w.sample_ms
    choice = r.random()
    if choice < 0.6:
        return s
    if choice < 0.65:
        return 0  # non-decreasing: a repeated tick time is allowed
    if choice < 0.8:
        return s * r.randint(2, max(2, per_window // 8))
    if choice < 0.95:
        return s * max(1, per_window + r.randint(-2, 2))  # about one window
    return s * r.randint(per_window, 10 * per_window + 10)  # longer than the window


def random_code(r: random.Random, palette: list[int]) -> int:
    return r.choice(palette) if r.random() < 0.7 else r.randint(0, 1023)


def check_against(w: Window, codes: list[int], r: random.Random) -> None:
    assert w.is_empty() == (not codes)
    assert w.count() == len(codes)
    assert w.total() == sum(codes)
    assert w.min_code() == (min(codes) if codes else None)
    assert w.max_code() == (max(codes) if codes else None)
    q = r.choice([1, 2, 3, 7, 10])
    if codes and r.random() < 0.6:
        p = (q * sum(codes)) // len(codes) + r.randint(-1, 1)  # near (or at) the average
    else:
        p = r.randint(-5 * q, 1100 * q)
    for op, fn in OPS.items():
        expected = bool(codes) and fn(Fraction(sum(codes), len(codes)), Fraction(p, q))
        assert w.avg_test(op, p, q) == expected, (op, p, q, codes)
    for agg in ("min", "max"):
        lo = None if r.random() < 0.25 else r.randint(-2, 1025)
        hi = None if r.random() < 0.25 else r.randint(-2, 1025)
        value = (min if agg == "min" else max)(codes) if codes else None
        expected = value is not None and (lo is None or lo <= value) and (hi is None or value <= hi)
        assert w.range_test(agg, lo, hi) == expected, (agg, lo, hi, codes)
        if value is not None:
            assert w.range_test(agg, value, value)


@pytest.mark.parametrize("seed", SEEDS)
def test_matches_brute_force_model(seed):
    r = rng(seed)
    for _ in range(SEQUENCES_PER_SEED):
        w = random_window(r)
        model = Model(w.window_ms, w.sample_ms)
        assert w.buckets == model.b
        palette = [r.randint(0, 1023) for _ in range(3)]
        t = random_start(r, w.sample_ms)
        check_against(w, [], r)
        for i in range(r.randint(1, 60)):
            if i:
                t += random_gap(r, w)
            code = random_code(r, palette)
            w.push(t, code)
            model.push(t, code)
            check_against(w, model.codes(), r)
            if r.random() < 0.01:
                w.reset()
                model = Model(w.window_ms, w.sample_ms)
                check_against(w, [], r)


def test_greenhouse_demo_buckets():
    w = Window(10_000, 100)
    assert (w.buckets, w.window_ms, w.sample_ms) == (32, 10_000, 100)
    assert 300 * 32 // 10_000 == 0 and 400 * 32 // 10_000 == 1
    w.push(300, 7)  # bucket 0
    w.push(400, 9)  # bucket 1
    w.push(9_900, 5)  # bucket 31: buckets 0..31 all count
    assert (w.count(), w.total(), w.min_code(), w.max_code()) == (3, 21, 5, 9)
    w.push(10_000, 6)  # bucket 32 reuses slot 0: bucket 0 (t = 300) drops out, bucket 1 stays
    assert (w.count(), w.total(), w.min_code(), w.max_code()) == (3, 20, 5, 9)
    w.push(10_400, 8)  # bucket 33 reuses slot 1: t = 400 drops out
    assert (w.count(), w.total(), w.min_code(), w.max_code()) == (3, 19, 5, 8)


def test_greenhouse_demo_bucket_holds_several_ticks():
    w = Window(10_000, 100)
    for t in (0, 100, 200, 300):  # k = 0 for t < 312.5
        w.push(t, t // 100)
    w.push(10_000, 50)  # k = 32 replaces slot 0, which held all four
    assert (w.count(), w.total()) == (1, 50)


def test_window_equal_to_sample_has_one_bucket():
    w = Window(100, 100)
    assert w.buckets == 1
    w.push(0, 10)
    assert (w.count(), w.total()) == (1, 10)
    w.push(100, 20)  # only the current bucket counts
    assert (w.count(), w.total(), w.min_code(), w.max_code()) == (1, 20, 20, 20)
    w.push(100, 30)  # same tick time: same bucket
    assert (w.count(), w.total(), w.min_code(), w.max_code()) == (2, 50, 20, 30)
    w.push(2**32 + 100 * 7, 4)
    assert (w.count(), w.total()) == (1, 4)


def test_window_not_multiple_of_sample():
    w = Window(150, 100)  # B = 1, k = t // 150
    assert w.buckets == 1
    w.push(0, 1)
    w.push(100, 2)  # k = 0 still
    assert w.count() == 2
    w.push(200, 3)  # k = 1
    assert (w.count(), w.total()) == (1, 3)


@pytest.mark.parametrize(
    "window_ms, sample_ms",
    [(50, 100), (99, 100), (0, 100), (100, 0), (-100, 100), (100, -100), (0, 0), (-1, -1)],
)
def test_invalid_parameters(window_ms, sample_ms):
    with pytest.raises(ValueError):
        Window(window_ms, sample_ms)


def test_buckets_capped_at_32():
    assert Window(3200, 100).buckets == 32
    assert Window(3300, 100).buckets == 32
    assert Window(3199, 100).buckets == 31
    assert Window(86_400_000, 50).buckets == 32


@pytest.mark.parametrize("window_ms", [100, 1000, 10_000])
def test_empty_window_tests_are_false(window_ms):
    w = Window(window_ms, 100)
    assert w.is_empty() and w.count() == 0 and w.total() == 0
    assert w.min_code() is None and w.max_code() is None
    for op in OPS:
        for p, q in ((0, 1), (-1, 1), (1023, 1), (5, 2), (-10**6, 1), (10**6, 3)):
            assert not w.avg_test(op, p, q)
    for agg in ("min", "max"):
        for lo, hi in ((None, None), (0, 1023), (None, 0), (0, None), (5, 5)):
            assert not w.range_test(agg, lo, hi)


def test_reset_empties_window():
    w = Window(1000, 100)
    for i in range(20):
        w.push(1000 + 100 * i, i)
    assert not w.is_empty()
    w.reset()
    assert w.is_empty() and w.count() == 0 and w.total() == 0
    assert w.min_code() is None and w.max_code() is None
    assert not w.avg_test(">=", -1, 1) and not w.range_test("min", None, None)
    w.push(5000, 42)  # works again after reset; nothing from before reappears
    assert (w.count(), w.total(), w.min_code(), w.max_code()) == (1, 42, 42, 42)


def test_reset_then_same_bucket_does_not_resurrect():
    w = Window(1000, 100)  # B = 10, k = t // 100
    w.push(500, 1)
    w.reset()
    w.push(500, 2)  # same bucket id and slot as before the reset
    assert (w.count(), w.total()) == (1, 2)


def test_stale_slot_with_matching_residue_excluded():
    w = Window(1000, 100)  # B = 10
    w.push(0, 9)  # k = 0, slot 0
    w.push(100 * 15, 1)  # k = 15, slot 5: slot 0 (id 0) is outside (5, 15]
    assert (w.count(), w.total(), w.max_code()) == (1, 1, 1)


@pytest.mark.parametrize(
    "codes",
    [[0], [1], [1023], [1, 2], [3, 4, 4], [0, 1023], [5, 5, 5], [1, 2, 2, 2, 3, 7], [10, 0, 0]],
)
def test_avg_test_exact(codes):
    w = Window(10_000, 100)
    for i, c in enumerate(codes):
        w.push(100 * i, c)
    avg = Fraction(sum(codes), len(codes))
    probes = {(p, q) for q in (1, 2, 3, 4, 6, 7) for p in range(-3 * q, 1026 * q, max(1, q * 37))}
    probes |= {(avg.numerator * m, avg.denominator * m) for m in (1, 2, 5)}  # exact equality
    probes |= {(avg.numerator * 3 + d, avg.denominator * 3) for d in (-1, 1)}  # just off it
    probes |= {(-1, 1), (-1023, 1), (-1, 1000)}  # negative p
    for p, q in sorted(probes):
        for op, fn in OPS.items():
            assert w.avg_test(op, p, q) == fn(avg, Fraction(p, q)), (op, p, q)
    assert w.avg_test("==", avg.numerator, avg.denominator)


def test_avg_test_large_values_stay_exact():
    w = Window(600_000, 50)  # 12000 ticks
    t = 2**40
    for i in range(12_000):
        w.push(t + 50 * i, 1023 if i % 3 else 1022)
    n, s = w.count(), w.total()
    assert n > 0
    avg = Fraction(s, n)
    assert w.avg_test("==", avg.numerator, avg.denominator)
    assert w.avg_test(">", avg.numerator * 10**9 - 1, avg.denominator * 10**9)
    assert w.avg_test("<", avg.numerator * 10**9 + 1, avg.denominator * 10**9)


def test_bad_arguments_rejected():
    w = Window(1000, 100)
    w.push(0, 1)
    with pytest.raises(ValueError):
        w.avg_test("!=", 1, 1)
    with pytest.raises(ValueError):
        w.avg_test(">", 1, 0)
    with pytest.raises(ValueError):
        w.range_test("avg", 0, 1)
