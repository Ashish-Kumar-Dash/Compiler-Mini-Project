"""AST: frozen dataclasses in source order (Architecture §5.3).

Numbers are kept as source text so the middle end builds exact Fractions; every node and every
name carries a span for diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from .source import Span


@dataclass(frozen=True)
class Name:
    text: str
    span: Span


@dataclass(frozen=True)
class SensorDecl:
    name: Name
    pin: Name                    # canonical pin name (LED3 -> LED3R); span of the source token
    kind: str                    # "ANALOG" | "DIGITAL"
    unit: Name | None            # canonical unit (% -> PCT)
    lo: Name | None              # number source text
    hi: Name | None
    span: Span


@dataclass(frozen=True)
class ActuatorDecl:
    name: Name
    pin: Name
    span: Span


@dataclass(frozen=True)
class SensorRef:
    sensor: Name
    span: Span


@dataclass(frozen=True)
class Agg:
    fn: str                      # "avg" | "min" | "max"
    sensor: Name
    window_ms: int
    window_span: Span
    span: Span


Value = Union[SensorRef, Agg]


@dataclass(frozen=True)
class Cmp:
    value: Value
    op: str
    op_span: Span
    number: Name                 # source text, e.g. "-40", "22.5"
    unit: Name | None
    span: Span


@dataclass(frozen=True)
class SetState:
    actuator: Name
    state: str                   # "ON" | "OFF"
    span: Span


@dataclass(frozen=True)
class Notify:
    text: str
    span: Span


Action = Union[SetState, Notify]


@dataclass(frozen=True)
class Rule:
    name: Name
    deadline_ms: int | None
    deadline_span: Span | None
    guard: tuple[Cmp, ...]
    hold_ms: int | None
    hold_span: Span | None
    action: Action
    span: Span


@dataclass(frozen=True)
class Never:
    a: Name
    s1: str
    b: Name
    s2: str
    span: Span


@dataclass(frozen=True)
class Ensure:
    actuator: Name
    state: str
    guard: tuple[Cmp, ...]
    span: Span


@dataclass(frozen=True)
class Failsafe:
    actuator: Name
    state: str
    span: Span


Safety = Union[Never, Ensure]


@dataclass(frozen=True)
class Program:
    sensors: tuple[SensorDecl, ...]
    actuators: tuple[ActuatorDecl, ...]
    rules: tuple[Rule, ...]
    safeties: tuple[Safety, ...]
    failsafes: tuple[Failsafe, ...]


# ---------------------------------------------------------------------------------------------
# Deterministic text dump (AST snapshot goldens)


def dump(p: Program) -> str:
    def sp(s: Span | None) -> str:
        return f"@{s.line}:{s.col}" if s else ""

    def cmp_text(c: Cmp) -> str:
        v = c.value
        if isinstance(v, SensorRef):
            val = v.sensor.text
        else:
            val = f"{v.fn}({v.sensor.text}, {v.window_ms}ms)"
        unit = f" {c.unit.text}" if c.unit else ""
        return f"{val} {c.op} {c.number.text}{unit}"

    out: list[str] = []
    for s in p.sensors:
        extra = (f" UNIT {s.unit.text} RANGE {s.lo.text}..{s.hi.text}"  # type: ignore[union-attr]
                 if s.kind == "ANALOG" else "")
        out.append(f"SENSOR {s.name.text} AT {s.pin.text} {s.kind}{extra} {sp(s.span)}")
    for a in p.actuators:
        out.append(f"ACTUATOR {a.name.text} AT {a.pin.text} {sp(a.span)}")
    for r in p.rules:
        rt = f" REALTIME {r.deadline_ms}ms" if r.deadline_ms is not None else ""
        hold = f" FOR {r.hold_ms}ms" if r.hold_ms is not None else ""
        act = (f"{r.action.actuator.text} {r.action.state}" if isinstance(r.action, SetState)
               else f"NOTIFY {r.action.text!r}")
        guard = " AND ".join(cmp_text(c) for c in r.guard)
        out.append(f"RULE {r.name.text}{rt}: WHEN {guard}{hold} THEN {act} {sp(r.span)}")
    for s in p.safeties:
        if isinstance(s, Never):
            out.append(f"NEVER {s.a.text} {s.s1} AND {s.b.text} {s.s2} {sp(s.span)}")
        else:
            guard = " AND ".join(cmp_text(c) for c in s.guard)
            out.append(f"ENSURE {s.actuator.text} {s.state} WHEN {guard} {sp(s.span)}")
    for f in p.failsafes:
        out.append(f"FAILSAFE {f.actuator.text} {f.state} {sp(f.span)}")
    return "\n".join(out) + "\n"
