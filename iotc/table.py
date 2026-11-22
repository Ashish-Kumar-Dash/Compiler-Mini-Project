"""IR -> runtime table (the plain-data contract of iotc/runtime, see runtime/engine.py) and the
LED-matrix layout (Architecture §11.8). Both back ends embed this same table."""
from __future__ import annotations

import math

from .ir import AvgTest, CodeRange, IRProgram, Notify, SetState

MATRIX_SENSORS, MATRIX_RULES, MATRIX_ACTUATORS = 4, 10, 5


def _state(s: str) -> int:
    return 1 if s == "ON" else 0


def test_entry(ir: IRProgram, t) -> list:
    if isinstance(t, CodeRange):
        if t.var.agg is None:
            return ["range", "raw", ir.sensor(t.var.sensor).id, t.lo, t.hi]
        return ["range", t.var.agg, ir.window_for(t.var).id, t.lo, t.hi]
    assert isinstance(t, AvgTest)
    return ["avg", ir.window_for(t.var).id, t.op, t.p, t.q]


def scenario_align_ms(ir: IRProgram) -> int:
    """Scenario starts happen on ticks that are multiples of this (§9.9, conformance mode).

    Window bucket ids are k(t) = t*B // W in absolute time, so the bucket phase seen by a scenario
    depends on its start time. Shifting t by P shifts every k by a whole number exactly when
    W / gcd(W, B) divides P; starting on multiples of the lcm over all windows (and of S) makes
    every iteration see the same phase as the compiler's prediction."""
    S = ir.sample_ms
    align = S
    for w in ir.windows:
        B = min(32, w.window_ms // S)
        p = w.window_ms // math.gcd(w.window_ms, B)
        align = align * p // math.gcd(align, p)
    return align


def build_table(ir: IRProgram) -> dict:
    rules = []
    for r in ir.rules:
        if isinstance(r.action, SetState):
            action = ["set", r.action.actuator, _state(r.action.state)]
        else:
            assert isinstance(r.action, Notify)
            action = ["notify", r.action.text]
        rules.append({"name": r.name, "tests": [test_entry(ir, t) for t in r.tests], "hold_ms": r.hold_ms,
                      "action": action, "placement": r.placement, "mask_bit": r.mask_bit})
    return {
        "program": ir.name,
        "sample_ms": ir.sample_ms,
        "scenario_align_ms": scenario_align_ms(ir),
        "heartbeat_ms": ir.heartbeat_ms,
        "failsafe_timeout_ms": ir.failsafe_timeout_ms,
        "target": ir.target,
        "sensors": [{"name": s.name, "adc_max": s.adc_max, "kind": s.kind, "unit": s.unit,
                     "lo": [s.lo.numerator, s.lo.denominator], "hi": [s.hi.numerator, s.hi.denominator]}
                    for s in ir.sensors],
        "actuators": [{"name": a.name, "init": _state(a.init)} for a in ir.actuators],
        "windows": [{"sensor": w.sensor_id, "window_ms": w.window_ms, "mcu": w.on_mcu, "linux": w.on_linux}
                    for w in ir.windows],
        "rules": rules,
        "nevers": [[n.a, _state(n.s1), n.b, _state(n.s2)] for n in ir.nevers],
        "ensures": [{"tests": [test_entry(ir, t) for t in e.tests],
                     "closure": [[a, _state(s)] for a, s in e.closure]} for e in ir.ensures],
        "fs": [[a, _state(s)] for a, s in ir.fs_closure],
        "matrix": matrix_layout(ir),
    }


def _thresholds(ir: IRProgram, sensor_id: int) -> list[int]:
    out: set[int] = set()
    tests = [t for r in ir.rules for t in r.tests] + [t for e in ir.ensures for t in e.tests]
    for t in tests:
        if ir.sensor(t.var.sensor).id != sensor_id:
            continue
        if isinstance(t, CodeRange):
            if t.never:
                continue
            out.update(v for v in (t.lo, t.hi) if v is not None)
        else:
            out.add(math.floor(t.p / t.q))
    adc_max = ir.sensors[sensor_id].adc_max
    return sorted(c for c in out if 0 <= c <= adc_max)


def matrix_layout(ir: IRProgram) -> dict:
    bars = []
    for s in ir.sensors[:MATRIX_SENSORS]:
        ticks = _thresholds(ir, s.id)
        A = s.adc_max
        if not ticks or A < 10:
            zlo, zhi = 0, A
        else:
            mn, mx = ticks[0], ticks[-1]
            m = max(A // 8, (mx - mn) // 4)
            zlo, zhi = max(0, mn - m), min(A, mx + m)
            if zhi - zlo < 9:
                zlo, zhi = max(0, zlo - 5), min(A, zhi + 5)
        bars.append({"zlo": zlo, "zhi": zhi, "ticks": ticks})
    return {"bars": bars, "rules": min(len(ir.rules), MATRIX_RULES),
            "acts": min(len(ir.actuators), MATRIX_ACTUATORS)}
