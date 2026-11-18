"""Check the hand-built greenhouse_board IR against exact arithmetic (ADR-17).

For every code 0..1023, the compiled integer test must agree with the exact real comparison
x(c) OP T. This validates the fixture that the runtime and back ends are built against."""
import operator
from fractions import Fraction as F

import pytest

from iotc.ir import AvgTest, CodeRange
from tests.fixtures.greenhouse_board_ir import build

OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}

# (rule or ensure name, sensor, op, threshold) as written in examples/greenhouse_board.iot
SOURCE_CMPS = {
    "cool": ("temp", ">", F(35)),
    "uncool": ("temp", "<", F(30)),
    "alert": ("temp", ">", F(45)),
    "dry": ("humidity", "<", F(20)),
    "ENSURE#1": ("temp", ">", F(50)),
}


def _tests_by_name(p):
    out = {r.name: r.tests for r in p.rules}
    out.update({f"ENSURE#{e.id + 1}": e.tests for e in p.ensures})
    return out


@pytest.mark.parametrize("name", sorted(SOURCE_CMPS))
def test_code_ranges_match_exact_comparison(name):
    p = build()
    sensor_name, op, threshold = SOURCE_CMPS[name]
    (test,) = _tests_by_name(p)[name]
    assert isinstance(test, CodeRange)
    sensor = p.sensor(sensor_name)
    for c in range(sensor.adc_max + 1):
        exact = OPS[op](sensor.value_of(c), threshold)
        lo_ok = test.lo is None or c >= test.lo
        hi_ok = test.hi is None or c <= test.hi
        assert (lo_ok and hi_ok) == exact, (name, c)


@pytest.mark.parametrize("name,op,threshold", [("warm", "<", F(18)), ("unwarm", ">", F(22))])
def test_avg_tests_match_exact_average(name, op, threshold):
    p = build()
    (test,) = _tests_by_name(p)[name]
    assert isinstance(test, AvgTest) and test.op == op
    temp = p.sensor("temp")
    # sums/counts near the threshold, including exact hits
    for count in (1, 2, 3, 5, 7, 32, 100):
        centre = int(temp.code_of(threshold) * count)
        for s in range(max(0, centre - 12), centre + 12):
            avg_value = temp.lo + F(s, count) * (temp.hi - temp.lo) / temp.adc_max
            exact = OPS[op](avg_value, threshold)
            compiled = OPS[op](test.q * s, test.p * count)
            assert compiled == exact, (name, s, count)


def test_structure():
    p = build()
    assert [r.name for r in p.linux_setstate_rules] == ["warm", "unwarm"]
    assert [r.mask_bit for r in p.linux_setstate_rules] == [0, 1]
    assert p.init == ("OFF", "OFF")
    assert p.ensures[0].closure == ((0, "ON"), (1, "OFF"))
    mcu = build(target="mcu-only")
    assert all(r.placement == "MCU" for r in mcu.rules)
    assert mcu.windows[0].on_mcu and not mcu.windows[0].on_linux
