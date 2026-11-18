"""Property tests for ir.Interval (Architecture §7, §13.2) and formatting helpers."""
from fractions import Fraction as F

import pytest

from iotc.ir import Interval, fmt_duration, fmt_frac
from tests.gen import SEEDS, grid, interval, rng

N = 300


def points_in(iv, pts):
    return {p for p in pts if iv.contains_point(p)}


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("integer", [False, True])
def test_intersect_is_set_intersection(seed, integer):
    r = rng(seed)
    for _ in range(N):
        a, b = interval(r, integer), interval(r, integer)
        pts = grid([a, b])
        if integer:
            pts = sorted({F(round(p)) for p in pts} | set(pts))
        assert points_in(a.intersect(b), pts) == points_in(a, pts) & points_in(b, pts)
        assert a.intersect(b).same_set(b.intersect(a))


@pytest.mark.parametrize("seed", SEEDS)
def test_intersect_associative(seed):
    r = rng(seed)
    for _ in range(N):
        a, b, c = interval(r), interval(r), interval(r)
        assert a.intersect(b).intersect(c).same_set(a.intersect(b.intersect(c)))


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("integer", [False, True])
def test_is_empty_matches_points(seed, integer):
    r = rng(seed)
    for _ in range(N):
        a = interval(r, integer)
        pts = grid([a])
        if integer:
            pts = sorted({F(round(p)) for p in pts})
        if a.is_empty():
            assert not points_in(a, pts)
        else:
            # non-empty real intervals always contain their midpoint or a closed endpoint
            assert points_in(a, pts) or (integer and a.is_empty())


@pytest.mark.parametrize("seed", SEEDS)
def test_touches_and_gap(seed):
    r = rng(seed)
    for _ in range(N):
        a, b = interval(r), interval(r)
        assert a.touches(b) == b.touches(a)
        g = a.gap_to(b)
        assert g == b.gap_to(a)
        if a.touches(b):
            assert g == 0
        if g is not None:
            assert g >= 0 and a.intersect(b).is_empty()


@pytest.mark.parametrize("seed", SEEDS)
def test_contains(seed):
    r = rng(seed)
    for _ in range(N):
        a, b = interval(r), interval(r)
        pts = grid([a, b])
        if a.contains(b):
            assert points_in(b, pts) <= points_in(a, pts)
        assert a.contains(a.intersect(b))


def test_examples():
    temp_gt_35 = Interval.make(F(35), False, F(125), True)
    temp_lt_30 = Interval.make(F(-40), True, F(30), False)
    assert str(temp_gt_35) == "(35, 125]"
    assert temp_gt_35.gap_to(temp_lt_30) == 5
    assert not temp_gt_35.touches(temp_lt_30)
    le_35 = Interval.make(F(-40), True, F(35), True)
    assert temp_gt_35.touches(le_35) and temp_gt_35.gap_to(le_35) == 0
    lt_40 = Interval.make(F(-40), True, F(40), False)
    assert str(temp_gt_35.intersect(lt_40)) == "(35, 40)"


def test_integer_normalisation():
    door_gt0_lt1 = Interval.make(F(0), False, F(1), False, integer=True)
    assert door_gt0_lt1.is_empty()
    door_gt0 = Interval.make(F(0), False, F(1), True, integer=True)
    assert str(door_gt0) == "{1}"
    zero = Interval.closed(0, 0, integer=True)
    assert zero.touches(door_gt0)


@pytest.mark.parametrize("x,s", [(F(35), "35"), (F(45, 2), "22.5"), (F(340, 9), "340/9"),
                                 (F(-1, 8), "-0.125"), (F(1, 1000), "0.001"), (F(-40), "-40")])
def test_fmt_frac(x, s):
    assert fmt_frac(x) == s


@pytest.mark.parametrize("ms,s", [(10, "10ms"), (1500, "1500ms"), (10_000, "10s"),
                                  (300_000, "5min"), (3_600_000, "1h")])
def test_fmt_duration(ms, s):
    assert fmt_duration(ms) == s
