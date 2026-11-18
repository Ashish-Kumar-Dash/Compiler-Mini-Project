"""Lowering tests (Architecture §7): the real front end must reproduce the hand-built IR, and
compiled code tests must agree with exact arithmetic."""
import dataclasses
import operator
from fractions import Fraction as F
from pathlib import Path

import pytest

from iotc import adc
from iotc.ir import AvgTest, CodeRange, IRProgram, Var
from iotc.options import Options
from iotc.pipeline import compile_file, compile_text
from tests.fixtures import greenhouse_board_ir
from tests.gen import rng

ROOT = Path(__file__).resolve().parents[1]
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq}


def strip(obj):
    """Drop spans (and source identity) so lowered IR compares with the hand-built fixture."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        changes = {}
        for f in dataclasses.fields(obj):
            if f.name == "span":
                changes[f.name] = None
            elif f.name in ("name", "source_sha") and isinstance(obj, IRProgram):
                changes[f.name] = ""
            elif f.name == "extras":
                changes[f.name] = {}
            else:
                changes[f.name] = strip(getattr(obj, f.name))
        return dataclasses.replace(obj, **changes)
    if isinstance(obj, tuple):
        return tuple(strip(x) for x in obj)
    if isinstance(obj, dict):
        return {k: strip(v) for k, v in obj.items()}
    return obj


@pytest.mark.parametrize("target", ["unoq", "mcu-only"])
def test_lowering_reproduces_hand_built_fixture(target):
    res = compile_file(ROOT / "examples" / "greenhouse_board.iot", Options.make("unoq", target))
    assert res.ir is not None, [d.message for d in res.sink.items]
    expected = greenhouse_board_ir.build(target=target)
    got = strip(res.ir)
    exp = strip(expected)
    for f in dataclasses.fields(IRProgram):
        assert getattr(got, f.name) == getattr(exp, f.name), f.name


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("op", sorted(OPS))
def test_code_bounds_exhaustive(seed, op):
    """For random ranges and thresholds (<= 4 decimals), the integer test equals the exact real
    comparison for every code 0..1023 (ADR-17)."""
    r = rng(seed)
    for _ in range(25):
        lo = F(r.randint(-500, 500), r.choice([1, 2, 10]))
        hi = lo + F(r.randint(1, 4000), r.choice([1, 4, 10]))
        A = 1023

        def x(c):
            return lo + F(c) * (hi - lo) / A
        if r.random() < 0.3:
            t = x(r.randint(0, A))                       # exactly on a code
        else:
            t = lo + (hi - lo) * F(r.randint(-100, 11000), 10000)
            t = F(round(t * 10_000), 10_000)
        rr = (t - lo) * A / (hi - lo)
        test = adc.merge(Var("s"), [adc.bounds(op, rr)], A)
        for c in range(A + 1):
            assert adc.code_range_holds(test, c) == OPS[op](x(c), t), (lo, hi, t, op, c)


@pytest.mark.parametrize("op", sorted(OPS))
def test_avg_tests_exact(op):
    r = rng(3)
    for _ in range(400):
        rr = F(r.randint(-2000, 120000), r.choice([1, 3, 5, 10, 7]))
        t = adc.avg_test(Var("s", "avg", 1000), op, rr, 1023)
        count = r.randint(1, 40)
        total = r.randint(0, 1023 * count)
        assert adc.avg_holds(t, total, count) == OPS[op](F(total, count), rr)


def test_fahrenheit_threshold_converted_exactly():
    ir = compile_text("SENSOR t AT A0 ANALOG UNIT C RANGE -40..125;\nACTUATOR f AT D7;\n"
                      "RULE a: WHEN t > 95 F THEN f ON;\nRULE b: WHEN t < 100 F THEN f OFF;\n").ir
    a, b = ir.rules
    assert str(a.guard[Var("t")]) == "(35, 125]"
    assert str(b.guard[Var("t")]) == "[-40, 340/9)"
    # 100 F = 340/9 C -> r = 6.2*(340/9 + 40) = 6.2*700/9 = 482.2.. -> code <= 482
    assert b.tests == (CodeRange(Var("t"), None, 482),)


def test_merged_ranges_and_never():
    ir = compile_text("SENSOR t AT A0 ANALOG UNIT C RANGE -40..125;\nACTUATOR f AT D7;\n"
                      "RULE a: WHEN t > 30 AND t < 35 THEN f ON;\n"
                      "RULE b: WHEN t > 35 AND t < 35.1 THEN f OFF;\n").ir
    a, b = ir.rules
    assert a.tests == (CodeRange(Var("t"), 435, 464),)
    assert b.tests[0].never and not b.guard[Var("t")].is_empty()


def test_digital_and_avg_over_digital():
    ir = compile_text("SENSOR door AT D2 DIGITAL;\nACTUATOR f AT D7;\n"
                      "RULE a: WHEN door == 1 THEN f ON;\n"
                      "RULE b: WHEN avg(door, 10s) > 0.5 THEN f OFF;\n").ir
    a, b = ir.rules
    assert a.tests == (CodeRange(Var("door"), 1, None),)
    assert a.guard[Var("door")].integer
    assert isinstance(b.tests[0], AvgTest) and (b.tests[0].p, b.tests[0].q) == (1, 2)
    assert not b.guard[Var("door", "avg", 10000)].integer


def test_closures_and_init():
    ir = compile_text("ACTUATOR fan AT D7;\nACTUATOR heater AT D8;\nACTUATOR vent AT D9;\n"
                      "SENSOR t AT A0 ANALOG UNIT C RANGE 0..100;\n"
                      "RULE r: WHEN t > 1 THEN vent ON;\n"
                      "SAFETY NEVER fan ON AND heater ON;\nSAFETY NEVER heater OFF AND vent OFF;\n"
                      "SAFETY ENSURE fan ON WHEN t > 50;\nFAILSAFE heater ON;\n").ir
    # ENSURE fan ON -> heater OFF -> vent ON (transitive closure)
    assert ir.ensures[0].closure == ((0, "ON"), (1, "OFF"), (2, "ON"))
    # FAILSAFE heater ON -> fan OFF
    assert ir.fs_closure == ((0, "OFF"), (1, "ON"))
    assert ir.init == ("OFF", "ON", "OFF")


def test_mask_bits_only_for_linux_setstate():
    ir = compile_text("SENSOR t AT A0 ANALOG UNIT C RANGE 0..100;\nACTUATOR f AT D7;\n"
                      "RULE n: WHEN t > 9 THEN NOTIFY \"x\";\n"
                      "RULE a: WHEN avg(t, 10s) > 5 THEN f ON;\n"
                      "RULE b: WHEN avg(t, 10s) < 4 THEN f OFF;\n").ir
    assert [r.mask_bit for r in ir.rules] == [None, 0, 1]
    assert [r.placement for r in ir.rules] == ["LINUX", "LINUX", "LINUX"]


def test_place_windows_mcu_policy():
    ir = compile_text("SENSOR t AT A0 ANALOG UNIT C RANGE 0..100;\nACTUATOR f AT D7;\n"
                      "RULE a REALTIME 10ms: WHEN avg(t, 10s) > 5 THEN f ON;\n",
                      Options.make("unoq", place_windows="mcu")).ir
    assert ir is not None and ir.rules[0].placement == "MCU" and ir.windows[0].on_mcu
