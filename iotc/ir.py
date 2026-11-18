"""Rule IR (Architecture §7). FROZEN CONTRACT: the front end produces it, the analyses annotate
it, the runtime/interpreter and both back ends consume it. Change it only together with
Architecture.md and every consumer.

Conventions
* Actuator, sensor, window, rule, NEVER and ENSURE ids are 0-based indices in source order.
* States are the strings "ON" / "OFF".
* Real-valued bounds are fractions.Fraction (exact). Generated code compares integer ADC codes
  (CodeRange / AvgTest), never floats (ADR-17).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal, Mapping, Union

from .source import Span

State = Literal["ON", "OFF"]
Placement = Literal["MCU", "LINUX"]
ON: State = "ON"
OFF: State = "OFF"


def other(state: State) -> State:
    return OFF if state == ON else ON


# ---------------------------------------------------------------------------------------------
# Number formatting (deterministic, exact)


def fmt_frac(x: Fraction) -> str:
    """Exact, human-friendly: integers as '35', terminating decimals as '22.5', others as
    '340/9'."""
    x = Fraction(x)
    if x.denominator == 1:
        return str(x.numerator)
    d = x.denominator
    twos = fives = 0
    while d % 2 == 0:
        d //= 2
        twos += 1
    while d % 5 == 0:
        d //= 5
        fives += 1
    if d == 1:
        digits = max(twos, fives)
        scaled = abs(x.numerator) * 10**digits // x.denominator
        sign = "-" if x < 0 else ""
        s = str(scaled).rjust(digits + 1, "0")
        return f"{sign}{s[:-digits]}.{s[-digits:]}"
    return f"{x.numerator}/{x.denominator}"


def fmt_duration(ms: int) -> str:
    for unit, size in (("h", 3_600_000), ("min", 60_000), ("s", 1000)):
        if ms % size == 0:
            return f"{ms // size}{unit}"
    return f"{ms}ms"


# ---------------------------------------------------------------------------------------------
# Variables and intervals


@dataclass(frozen=True)
class Var:
    """A rule input: a raw sensor value, or a window aggregate over a sensor."""

    sensor: str
    agg: str | None = None          # "avg" | "min" | "max" | None
    window_ms: int | None = None

    @property
    def is_window(self) -> bool:
        return self.agg is not None

    def label(self) -> str:
        if self.agg is None:
            return self.sensor
        return f"{self.agg}({self.sensor}, {fmt_duration(self.window_ms or 0)})"

    def sort_key(self) -> tuple:
        return (self.sensor, self.agg or "", self.window_ms or 0)


@dataclass(frozen=True)
class Bound:
    value: Fraction
    closed: bool


@dataclass(frozen=True)
class Interval:
    """A real (or integer) interval. integer=True intervals are kept normalised to closed
    integer bounds."""

    lo: Bound
    hi: Bound
    integer: bool = False

    @staticmethod
    def make(lo: Fraction, lo_closed: bool, hi: Fraction, hi_closed: bool,
             integer: bool = False) -> "Interval":
        iv = Interval(Bound(Fraction(lo), lo_closed), Bound(Fraction(hi), hi_closed), integer)
        return iv.normalised()

    @staticmethod
    def closed(lo: Fraction | int, hi: Fraction | int, integer: bool = False) -> "Interval":
        return Interval.make(Fraction(lo), True, Fraction(hi), True, integer)

    def normalised(self) -> "Interval":
        if not self.integer:
            return self
        lo = self.lo.value
        lo_i = math.ceil(lo) if self.lo.closed else math.floor(lo) + 1
        hi = self.hi.value
        hi_i = math.floor(hi) if self.hi.closed else math.ceil(hi) - 1
        return Interval(Bound(Fraction(lo_i), True), Bound(Fraction(hi_i), True), True)

    def is_empty(self) -> bool:
        if self.lo.value > self.hi.value:
            return True
        if self.lo.value == self.hi.value:
            return not (self.lo.closed and self.hi.closed)
        return False

    def intersect(self, o: "Interval") -> "Interval":
        if self.lo.value > o.lo.value or (self.lo.value == o.lo.value and not self.lo.closed):
            lo = self.lo
        else:
            lo = o.lo
        if self.hi.value < o.hi.value or (self.hi.value == o.hi.value and not self.hi.closed):
            hi = self.hi
        else:
            hi = o.hi
        return Interval(lo, hi, self.integer or o.integer).normalised()

    def contains_point(self, x: Fraction) -> bool:
        if self.is_empty():
            return False
        if x < self.lo.value or (x == self.lo.value and not self.lo.closed):
            return False
        if x > self.hi.value or (x == self.hi.value and not self.hi.closed):
            return False
        return not self.integer or Fraction(x).denominator == 1

    def contains(self, o: "Interval") -> bool:
        """o ⊆ self."""
        if o.is_empty():
            return True
        if self.is_empty():
            return False
        return self.intersect(o).same_set(Interval(o.lo, o.hi, self.integer or o.integer))

    def same_set(self, o: "Interval") -> bool:
        if self.is_empty() and o.is_empty():
            return True
        return self.normalised() == o.normalised()

    def touches(self, o: "Interval") -> bool:
        """Disjoint, but with no gap: closures meet (reals) or adjacent integers."""
        if self.is_empty() or o.is_empty() or not self.intersect(o).is_empty():
            return False
        if self.integer and o.integer:
            return self.hi.value + 1 == o.lo.value or o.hi.value + 1 == self.lo.value
        return self.hi.value == o.lo.value or o.hi.value == self.lo.value

    def gap_to(self, o: "Interval") -> Fraction | None:
        """Non-negative distance between two disjoint non-empty intervals; None if they
        intersect or either is empty."""
        if self.is_empty() or o.is_empty() or not self.intersect(o).is_empty():
            return None
        if self.hi.value <= o.lo.value:
            return o.lo.value - self.hi.value
        return self.lo.value - o.hi.value

    def __str__(self) -> str:
        if self.is_empty():
            return "∅"
        if self.integer and self.lo.value == self.hi.value:
            return "{" + fmt_frac(self.lo.value) + "}"
        lb = "[" if self.lo.closed else "("
        rb = "]" if self.hi.closed else ")"
        return f"{lb}{fmt_frac(self.lo.value)}, {fmt_frac(self.hi.value)}{rb}"


Box = Mapping[Var, Interval]          # unmentioned variables = full domain


# ---------------------------------------------------------------------------------------------
# Compiled integer tests (ADR-17)


@dataclass(frozen=True)
class CodeRange:
    """Holds iff lo <= value <= hi for a raw code or a window's min/max code.
    lo/hi None = unbounded. lo > hi = never holds (constant false)."""

    var: Var
    lo: int | None
    hi: int | None

    @property
    def never(self) -> bool:
        return self.lo is not None and self.hi is not None and self.lo > self.hi


@dataclass(frozen=True)
class AvgTest:
    """Holds iff the window is non-empty and  q*sum OP p*count  (exact rational average test).
    op is one of '>', '>=', '<', '<=', '=='."""

    var: Var
    op: str
    p: int
    q: int


CodeTest = Union[CodeRange, AvgTest]


# ---------------------------------------------------------------------------------------------
# Program entities


@dataclass(frozen=True)
class IRSensor:
    id: int
    name: str
    pin: str                  # canonical pin name, e.g. "A0", "D2"
    kind: Literal["ANALOG", "DIGITAL"]
    unit: str | None          # "C", "F", "PCT", "LUX", "CM"; None for DIGITAL
    lo: Fraction              # value of code 0   (DIGITAL: 0)
    hi: Fraction              # value of code adc_max (DIGITAL: 1)
    adc_max: int              # 1023 for ANALOG on uno/unoq; 1 for DIGITAL
    span: Span | None = None

    def value_of(self, code: int) -> Fraction:
        """Exact real value of a code: x(c) = lo + c*(hi-lo)/A."""
        return self.lo + Fraction(code) * (self.hi - self.lo) / self.adc_max

    def code_of(self, value: Fraction) -> Fraction:
        """Exact inverse r = x^-1(value) (not rounded)."""
        return (Fraction(value) - self.lo) * self.adc_max / (self.hi - self.lo)

    def quantise(self, value: Fraction) -> int:
        """Trace/slider quantisation: clamp(floor(r + 1/2), 0, A)."""
        r = self.code_of(value)
        return max(0, min(self.adc_max, math.floor(r + Fraction(1, 2))))

    def domain(self) -> Interval:
        return Interval.closed(self.lo, self.hi, integer=self.kind == "DIGITAL")


@dataclass(frozen=True)
class IRActuator:
    id: int
    name: str
    pin: str                  # canonical, e.g. "D7", "LED3R"
    active_low: bool
    init: State               # initial state = FS closure entry or OFF (§8.3)
    failsafe: State | None    # declared FAILSAFE state (None if none declared)
    span: Span | None = None


@dataclass(frozen=True)
class IRWindow:
    id: int
    sensor_id: int
    window_ms: int
    buckets: int              # B = min(32, window_ms // sample_ms)
    on_mcu: bool              # used by an MCU-placed rule
    on_linux: bool            # used by a Linux-placed rule


@dataclass(frozen=True)
class SetState:
    actuator: int
    state: State


@dataclass(frozen=True)
class Notify:
    text: str


Action = Union[SetState, Notify]
Assignment = tuple[int, State]       # (actuator id, state)


@dataclass(frozen=True)
class IRRule:
    id: int
    name: str
    guard: Box                         # real box (analysis)
    tests: tuple[CodeTest, ...]        # compiled integer tests (runtime); all must hold
    hold_ms: int | None                # FOR
    action: Action
    deadline_ms: int | None            # REALTIME
    placement: Placement
    reasons: tuple[str, ...]           # printed in the placement table
    mask_bit: int | None               # Linux-placed SetState rules only
    span: Span | None = None


@dataclass(frozen=True)
class IRNever:
    id: int
    a: int
    s1: State
    b: int
    s2: State
    span: Span | None = None


@dataclass(frozen=True)
class IREnsure:
    id: int
    actuator: int
    state: State
    guard: Box
    tests: tuple[CodeTest, ...]
    closure: tuple[Assignment, ...]    # sorted by actuator id; includes (actuator, state)
    span: Span | None = None


@dataclass(frozen=True)
class IRFailsafe:
    actuator: int
    state: State
    span: Span | None = None


@dataclass(frozen=True)
class IRProgram:
    name: str                          # source display name
    source_sha: str                    # sha256 of normalised source
    board: Literal["uno", "unoq"]
    target: Literal["unoq", "mcu-only"]
    sensors: tuple[IRSensor, ...]
    actuators: tuple[IRActuator, ...]
    windows: tuple[IRWindow, ...]
    rules: tuple[IRRule, ...]
    nevers: tuple[IRNever, ...]
    ensures: tuple[IREnsure, ...]
    failsafes: tuple[IRFailsafe, ...]
    fs_closure: tuple[Assignment, ...]  # FS: union of failsafe closures, sorted by actuator id
    sample_ms: int = 100
    heartbeat_ms: int = 500
    failsafe_timeout_ms: int = 2000
    place_windows: Literal["linux", "mcu"] = "linux"
    extras: Mapping[str, object] = field(default_factory=dict)

    # -- lookups -------------------------------------------------------------------------------
    def sensor(self, name: str) -> IRSensor:
        for s in self.sensors:
            if s.name == name:
                return s
        raise KeyError(name)

    def window_for(self, var: Var) -> IRWindow:
        sid = self.sensor(var.sensor).id
        for w in self.windows:
            if w.sensor_id == sid and w.window_ms == var.window_ms:
                return w
        raise KeyError(var)

    @property
    def linux_setstate_rules(self) -> tuple[IRRule, ...]:
        return tuple(r for r in self.rules if r.mask_bit is not None)

    @property
    def init(self) -> tuple[State, ...]:
        return tuple(a.init for a in self.actuators)
