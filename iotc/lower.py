"""Lowering: checked AST -> Rule IR (Architecture §7).

Builds real boxes (for analyses) and integer code tests (for runtime), windows, placement, mask
bits, ENSURE/FAILSAFE closures and the initial state. Reports S032, S036 and P2xx placement codes.
"""
from __future__ import annotations

from fractions import Fraction

from . import adc
from . import ast as A
from .analysis.closure import closure
from .analysis.placement import place
from .diagnostics import Sink
from .ir import (
    OFF,
    Bound,
    CodeTest,
    Interval,
    IRActuator,
    IREnsure,
    IRFailsafe,
    IRNever,
    IRProgram,
    IRRule,
    IRSensor,
    IRWindow,
    Notify,
    SetState,
    Var,
    fmt_duration,
)
from .sema.checker import Checked

INT63 = 2**63


def var_of(c: A.Cmp) -> Var:
    v = c.value
    if isinstance(v, A.Agg):
        return Var(v.sensor.text, v.fn, v.window_ms)
    return Var(v.sensor.text)


class Lowerer:
    def __init__(self, checked: Checked, sink: Sink, name: str, sha: str) -> None:
        self.ck = checked
        self.p = checked.program
        self.opts = checked.options
        self.prof = self.opts.profile
        self.sink = sink
        self.name = name
        self.sha = sha
        self.sensors: dict[str, IRSensor] = {}
        self.act_id = {a.name.text: i for i, a in enumerate(self.p.actuators)}
        self.window_keys: list[tuple[str, int]] = []
        self.window_users: dict[tuple[str, int], set[str]] = {}
        self.window_span: dict[tuple[str, int], object] = {}

    # -- sensors, domains ---------------------------------------------------------------------
    def build_sensors(self) -> tuple[IRSensor, ...]:
        out = []
        for i, s in enumerate(self.p.sensors):
            if s.kind == "ANALOG":
                ir = IRSensor(i, s.name.text, s.pin.text, "ANALOG", s.unit.text,  # type: ignore[union-attr]
                              Fraction(s.lo.text), Fraction(s.hi.text), self.prof.adc_max, s.span)  # type: ignore[union-attr]
            else:
                ir = IRSensor(i, s.name.text, s.pin.text, "DIGITAL", None, Fraction(0), Fraction(1), 1, s.span)
            self.sensors[s.name.text] = ir
            out.append(ir)
        return tuple(out)

    def domain(self, var: Var) -> Interval:
        s = self.sensors[var.sensor]
        integer = s.kind == "DIGITAL" and var.agg != "avg"
        return Interval.closed(s.lo, s.hi, integer)

    def cmp_interval(self, var: Var, op: str, t: Fraction) -> Interval:
        d = self.domain(var)
        lo, hi = d.lo, d.hi
        if op == ">":
            iv = Interval(Bound(t, False), hi, d.integer)
        elif op == ">=":
            iv = Interval(Bound(t, True), hi, d.integer)
        elif op == "<":
            iv = Interval(lo, Bound(t, False), d.integer)
        elif op == "<=":
            iv = Interval(lo, Bound(t, True), d.integer)
        else:
            iv = Interval(Bound(t, True), Bound(t, True), d.integer)
        return d.intersect(iv)

    # -- guards -------------------------------------------------------------------------------
    def guard(self, cmps: tuple[A.Cmp, ...], user: str) -> tuple[dict[Var, Interval], tuple[CodeTest, ...]]:
        box: dict[Var, Interval] = {}
        ranges: dict[Var, list] = {}
        avg_tests: list[CodeTest] = []
        order: list[Var] = []
        for c in cmps:
            var = var_of(c)
            t = self.ck.thresholds[c]
            iv = self.cmp_interval(var, c.op, t)
            box[var] = box[var].intersect(iv) if var in box else iv
            if var not in order:
                order.append(var)
            if var.is_window:
                key = (var.sensor, var.window_ms)
                if key not in self.window_users:
                    self.window_keys.append(key)
                    self.window_users[key] = set()
                    self.window_span[key] = c.value.span
                self.window_users[key].add(user)
            s = self.sensors[var.sensor]
            r = s.code_of(t)
            if var.agg == "avg":
                test = adc.avg_test(var, c.op, r, s.adc_max)
                self.check_exact_bounds(test, s, var, c)
                avg_tests.append(test)
            else:
                ranges.setdefault(var, []).append(adc.bounds(c.op, r))
        tests: list[CodeTest] = []
        for var in order:
            if var in ranges:
                tests.append(adc.merge(var, ranges[var], self.sensors[var.sensor].adc_max))
        tests.extend(avg_tests)
        return box, tuple(tests)

    def check_exact_bounds(self, test, s: IRSensor, var: Var, c: A.Cmp) -> None:
        W, S = var.window_ms or 0, self.opts.sample_ms
        if W < S or S <= 0:
            return
        maxcount = W // S + min(32, W // S) + 1
        if test.q * s.adc_max * maxcount >= INT63 or abs(test.p) * maxcount >= INT63:
            self.sink.add("S036", f"threshold {c.number.text} is too precise for an exact average over "
                          f"{fmt_duration(W)}", c.number.span, "use fewer decimal places or a shorter window")

    # -- program ------------------------------------------------------------------------------
    def run(self) -> IRProgram:
        sensors = self.build_sensors()
        placed = place(self.p, self.opts, self.sink)
        nevers = []
        for s in self.p.safeties:
            if isinstance(s, A.Never) and s.a.text != s.b.text:
                nevers.append(IRNever(len(nevers), self.act_id[s.a.text], s.s1, self.act_id[s.b.text], s.s2, s.span))
        nevers_t = tuple(nevers)

        rules = []
        for i, (r, pl) in enumerate(zip(self.p.rules, placed)):
            box, tests = self.guard(r.guard, pl.placement)
            if isinstance(r.action, A.SetState):
                action = SetState(self.act_id[r.action.actuator.text], r.action.state)
            else:
                action = Notify(r.action.text)
            rules.append(IRRule(i, r.name.text, box, tests, r.hold_ms, action, r.deadline_ms,
                                pl.placement, pl.reasons, pl.mask_bit, r.span))

        ensures = []
        for s in self.p.safeties:
            if isinstance(s, A.Ensure):
                box, tests = self.guard(s.guard, "MCU")
                a = self.act_id[s.actuator.text]
                cl = closure([(a, s.state)], nevers_t)
                ensures.append(IREnsure(len(ensures), a, s.state, box, tests, cl.assignments, s.span))

        failsafes = tuple(IRFailsafe(self.act_id[f.actuator.text], f.state, f.span)
                          for f in self.p.failsafes)
        seen: dict[int, str] = {}
        for f in failsafes:
            seen.setdefault(f.actuator, f.state)
        fs = closure(list(seen.items()), nevers_t)
        fs_map = fs.as_dict()
        actuators = tuple(
            IRActuator(i, a.name.text, a.pin.text, self.prof.active_low(a.pin.text),
                       fs_map.get(i, OFF), seen.get(i), a.span)
            for i, a in enumerate(self.p.actuators))

        windows = []
        mcu_bytes = 0
        for wid, key in enumerate(self.window_keys):
            sname, w = key
            b = max(1, min(32, w // self.opts.sample_ms))
            users = self.window_users[key]
            on_mcu, on_linux = "MCU" in users, "LINUX" in users
            windows.append(IRWindow(wid, self.sensors[sname].id, w, b, on_mcu, on_linux))
            if on_mcu and self.prof.window_ram_budget is not None:
                mcu_bytes += b * self.prof.slot_bytes
                if mcu_bytes > self.prof.window_ram_budget:
                    self.sink.add("S032", f"window buffers need {mcu_bytes} bytes; board {self.prof.name} "
                                  f"allows {self.prof.window_ram_budget}", self.window_span[key],
                                  "use fewer distinct windows (same sensor and duration share one)")

        return IRProgram(
            name=self.name, source_sha=self.sha, board=self.opts.board, target=self.opts.target,  # type: ignore[arg-type]
            sensors=sensors, actuators=actuators, windows=tuple(windows), rules=tuple(rules),
            nevers=nevers_t, ensures=tuple(ensures), failsafes=failsafes, fs_closure=fs.assignments,
            sample_ms=self.opts.sample_ms, heartbeat_ms=self.opts.heartbeat_ms,
            failsafe_timeout_ms=self.opts.failsafe_timeout_ms,
            place_windows=self.opts.place_windows,  # type: ignore[arg-type]
            extras={"plan": tuple((pl.plan, pl.plan_reasons) for pl in placed)},
        )


def lower(checked: Checked, sink: Sink, name: str, sha: str) -> IRProgram:
    return Lowerer(checked, sink, name, sha).run()
