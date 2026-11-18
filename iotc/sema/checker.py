"""Per-construct semantic checks (Architecture §5.4, §6 S-codes). Produces a Checked program
whose thresholds are exact Fractions in each sensor's unit."""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from .. import ast as A
from ..diagnostics import Related, Sink
from ..ir import fmt_duration, fmt_frac
from ..options import Options
from . import pins, symbols, units

MAX_DURATION_MS = 24 * 3_600_000


@dataclass
class Checked:
    program: A.Program
    options: Options
    syms: symbols.Symbols
    thresholds: dict[A.Cmp, Fraction] = field(default_factory=dict)   # in the sensor's unit
    used_sensors: set[str] = field(default_factory=set)
    used_actuators: set[str] = field(default_factory=set)

    def sensor_range(self, name: str) -> tuple[Fraction, Fraction]:
        s = self.syms.sensors[name]
        if s.kind == "DIGITAL":
            return Fraction(0), Fraction(1)
        return Fraction(s.lo.text), Fraction(s.hi.text)  # type: ignore[union-attr]


class Checker:
    def __init__(self, program: A.Program, options: Options, sink: Sink) -> None:
        self.p = program
        self.opts = options
        self.sink = sink
        self.syms = symbols.build(program, sink)
        self.out = Checked(program, options, self.syms)

    # -- durations ----------------------------------------------------------------------------
    def duration(self, ms: int, span, what: str) -> bool:
        if ms == 0:
            self.sink.add("S030", f"{what} of zero", span, "durations must be greater than 0")
            return False
        if ms > MAX_DURATION_MS:
            self.sink.add("S037", f"{what} of {fmt_duration(ms)} is longer than 24h", span,
                          "use a duration of at most 24h")
            return False
        return True

    # -- declarations -------------------------------------------------------------------------
    def sensors(self) -> None:
        for s in self.p.sensors:
            if s.kind == "ANALOG":
                lo, hi = Fraction(s.lo.text), Fraction(s.hi.text)  # type: ignore[union-attr]
                if lo >= hi:
                    what = "empty" if lo == hi else "inverted"
                    self.sink.add("S012", f"RANGE {s.lo.text}..{s.hi.text} is {what}",  # type: ignore[union-attr]
                                  s.lo.span.to(s.hi.span),  # type: ignore[union-attr]
                                  f"write RANGE {s.hi.text}..{s.lo.text}" if lo > hi else  # type: ignore[union-attr]
                                  "the lower bound must be below the upper bound")

    # -- comparisons --------------------------------------------------------------------------
    def cmp(self, c: A.Cmp) -> None:
        v = c.value
        sname = v.sensor if isinstance(v, A.SensorRef) else v.sensor
        if not symbols.resolve(self.syms, sname, "sensor", self.sink):
            return
        sensor = self.syms.sensors[sname.text]
        self.out.used_sensors.add(sname.text)
        if isinstance(v, A.Agg):
            if self.duration(v.window_ms, v.window_span, "window"):
                S = self.opts.sample_ms
                if v.window_ms < S:
                    self.sink.add("S035", f"window {fmt_duration(v.window_ms)} is shorter than the "
                                  f"sample period ({S}ms)", v.window_span,
                                  f"use a window of at least {S}ms or lower --sample-ms")
                elif v.window_ms // S < 32:
                    self.sink.add("S031", f"window {fmt_duration(v.window_ms)} spans only "
                                  f"{v.window_ms // S} sample periods; {v.window_ms // S} buckets used",
                                  v.window_span, f"windows of at least {fmt_duration(32 * S)} use all 32 buckets")
            if sensor.kind == "DIGITAL":
                self.sink.add("S033", f"{v.fn}() over DIGITAL sensor '{sname.text}'", v.span,
                              "avg over a DIGITAL sensor is the fraction of time it was 1" if v.fn == "avg"
                              else None)
        value = Fraction(c.number.text)
        if c.unit is not None:
            if sensor.kind == "DIGITAL":
                self.sink.add("S013", f"unit {c.unit.text} on DIGITAL sensor '{sname.text}'",
                              c.unit.span, "DIGITAL values are 0 or 1 and take no unit")
                return
            su = sensor.unit.text  # type: ignore[union-attr]
            if not units.same_dimension(c.unit.text, su):
                self.sink.add("S010", f"unit {c.unit.text} ({units.DIMENSION[c.unit.text]}) does not "
                              f"match sensor '{sname.text}' ({su}, {units.DIMENSION[su]})", c.unit.span,
                              f"compare {sname.text} in {su}" + (" or F" if su == "C" else
                                                                  " or C" if su == "F" else ""))
                return
            value = units.convert(value, c.unit.text, su)
        lo, hi = self.out.sensor_range(sname.text)
        if lo < hi and (value < lo or value > hi):
            unit = f" {sensor.unit.text}" if sensor.unit else ""
            shown = fmt_frac(value) if c.unit is None or c.unit.text == (sensor.unit.text if sensor.unit else None) \
                else f"{c.number.text} {c.unit.text} = {fmt_frac(value)}{unit}"
            self.sink.add("S011", f"threshold {shown} is outside the RANGE of '{sname.text}' "
                          f"({fmt_frac(lo)}..{fmt_frac(hi)}{unit})", c.number.span,
                          "the comparison is then always or never true")
        if c.op == "==" and sensor.kind == "ANALOG":
            self.sink.add("S042", f"'==' on ANALOG sensor '{sname.text}' is almost never true",
                          c.op_span, "use a range: x >= a AND x <= b")
        self.out.thresholds[c] = value

    # -- rules and safety ---------------------------------------------------------------------
    def rules(self) -> None:
        for r in self.p.rules:
            if r.deadline_ms is not None:
                self.duration(r.deadline_ms, r.deadline_span, "REALTIME deadline")
                if isinstance(r.action, A.Notify):
                    self.sink.add("S034", f"REALTIME on NOTIFY rule '{r.name.text}'", r.deadline_span,
                                  "NOTIFY runs on Linux; remove REALTIME")
            if r.hold_ms is not None:
                self.duration(r.hold_ms, r.hold_span, "FOR duration")
            for c in r.guard:
                self.cmp(c)
            if isinstance(r.action, A.SetState):
                if symbols.resolve(self.syms, r.action.actuator, "actuator", self.sink):
                    self.out.used_actuators.add(r.action.actuator.text)

    def safeties(self) -> None:
        pairs: dict[frozenset, A.Never] = {}
        for s in self.p.safeties:
            if isinstance(s, A.Never):
                ok_a = symbols.resolve(self.syms, s.a, "actuator", self.sink)
                ok_b = symbols.resolve(self.syms, s.b, "actuator", self.sink)
                for ok, n in ((ok_a, s.a), (ok_b, s.b)):
                    if ok:
                        self.out.used_actuators.add(n.text)
                if s.a.text == s.b.text:
                    self.sink.add("S040", f"NEVER pair names '{s.a.text}' twice", s.b.span,
                                  "a NEVER pair forbids two different actuators being in given states")
                    continue
                key = frozenset({(s.a.text, s.s1), (s.b.text, s.s2)})
                if key in pairs:
                    self.sink.add("S043", "duplicate NEVER pair", s.span, "remove one of them",
                                  [Related(pairs[key].span, "first declared here")])
                else:
                    pairs[key] = s
            else:
                if symbols.resolve(self.syms, s.actuator, "actuator", self.sink):
                    self.out.used_actuators.add(s.actuator.text)
                for c in s.guard:
                    self.cmp(c)
        seen: dict[str, A.Failsafe] = {}
        for f in self.p.failsafes:
            if symbols.resolve(self.syms, f.actuator, "actuator", self.sink):
                self.out.used_actuators.add(f.actuator.text)
            if f.actuator.text in seen:
                self.sink.add("S041", f"second FAILSAFE for '{f.actuator.text}'", f.span,
                              "each actuator has at most one FAILSAFE state",
                              [Related(seen[f.actuator.text].span, "first FAILSAFE here")])
            else:
                seen[f.actuator.text] = f

    def unused(self) -> None:
        for s in self.p.sensors:
            if s.name.text not in self.out.used_sensors and self.syms.kind_of.get(s.name.text) == "sensor":
                self.sink.add("S050", f"sensor '{s.name.text}' is never used", s.name.span,
                              "remove it or use it in a rule")
        for a in self.p.actuators:
            if a.name.text not in self.out.used_actuators and self.syms.kind_of.get(a.name.text) == "actuator":
                self.sink.add("S051", f"actuator '{a.name.text}' is never referenced", a.name.span,
                              "remove it or set it in a rule")

    def run(self) -> Checked:
        self.sensors()
        pins.check_pins(self.p, self.opts.profile, self.sink)
        self.rules()
        self.safeties()
        self.unused()
        return self.out


def check(program: A.Program, options: Options, sink: Sink) -> Checked:
    return Checker(program, options, sink).run()
