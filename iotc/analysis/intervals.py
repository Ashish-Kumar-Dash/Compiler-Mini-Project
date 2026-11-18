"""Interval analysis (Architecture §8.1): reachability, conflicts, hysteresis, setters."""
from __future__ import annotations

from .. import adc
from ..diagnostics import Related
from ..ir import OFF, ON, CodeRange, IRRule, SetState, fmt_frac
from . import common as C


def _code_set(t: CodeRange, adc_max: int) -> set[int]:
    return {c for c in range(adc_max + 1) if adc.code_range_holds(t, c)}


def reachability(ctx: C.Ctx) -> None:
    ir = ctx.ir
    for r in ir.rules:
        ast = ctx.rule_ast(r)
        span = ctx.guard_span(ast.guard)
        if C.is_empty(r.guard):
            empty = [v for v, iv in r.guard.items() if iv.is_empty()]
            ctx.sink.add("W101", f"rule '{r.name}' can never fire: no value of {empty[0].label()} "
                         f"satisfies {C.guard_text(ast.guard)}", span,
                         "the conditions contradict each other or the sensor's RANGE")
            continue
        never = [t for t in r.tests if isinstance(t, CodeRange) and t.never]
        if never:
            v = never[0].var
            s = ir.sensor(v.sensor)
            step = fmt_frac(round((s.hi - s.lo) / s.adc_max, 4)) if s.kind == "ANALOG" else "1"
            ctx.sink.add("W113", f"rule '{r.name}' can never fire on the board: no ADC code lies in "
                         f"{v.label()} ∈ {r.guard[v]}{ctx.unit(v)} (codes step by about {step}{ctx.unit(v)})",
                         span, "widen the interval to at least one ADC step (see the code thresholds)")
            continue
        if C.is_full(ir, r.guard):
            if C.raw_only(r.guard):
                ctx.sink.add("W102", f"rule '{r.name}' always fires: {C.guard_text(ast.guard)} covers the "
                             "whole RANGE", span, "check the threshold against the sensor's RANGE")
            else:
                ctx.sink.add("I118", f"rule '{r.name}' fires whenever its window has data", span,
                             "a windowed comparison is false only while the window is empty")


def _setstate_rules(ctx: C.Ctx, aid: int, state: str) -> list[IRRule]:
    return [r for r in ctx.ir.rules
            if isinstance(r.action, SetState) and r.action.actuator == aid and r.action.state == state]


def _pair_related(ctx: C.Ctx, on: IRRule, off: IRRule) -> list[Related]:
    return [Related(ctx.rule_name_span(on), f"'{on.name}' sets {ctx.act_name(on.action.actuator)} ON"),
            Related(ctx.rule_name_span(off), f"'{off.name}' sets {ctx.act_name(off.action.actuator)} OFF")]


def conflicts_and_hysteresis(ctx: C.Ctx) -> None:
    ir = ctx.ir
    J = ctx.options.profile.jitter_codes
    for act in ir.actuators:
        for on in _setstate_rules(ctx, act.id, ON):
            for off in _setstate_rules(ctx, act.id, OFF):
                if C.is_empty(on.guard) or C.is_empty(off.guard):
                    continue
                overlap = C.intersect(ir, on.guard, off.guard)
                later = max(on, off, key=lambda r: r.id)
                if not C.is_empty(overlap):
                    raw = C.raw_only(on.guard) and C.raw_only(off.guard)
                    code = "E103" if raw else "W103"
                    what = "conflict" if raw else "possible conflict (through a window)"
                    ctx.sink.add(code, f"{what}: '{on.name}' sets {act.name} ON and '{off.name}' sets it "
                                 f"OFF when {C.fmt_box(overlap, ctx)}", ctx.rule_name_span(later),
                                 "OFF wins at runtime; make the two conditions disjoint",
                                 _pair_related(ctx, on, off), overlap=C.fmt_box(overlap, ctx))
                    continue
                if len(on.guard) != 1 or len(off.guard) != 1 or set(on.guard) != set(off.guard):
                    continue
                (var,) = on.guard
                a, b = on.guard[var], off.guard[var]
                if a.integer:
                    continue
                gap = a.gap_to(b)
                if gap is None:
                    continue
                unit = ctx.unit(var)
                if gap == 0:
                    ctx.sink.add("W104", f"no hysteresis: '{on.name}' ({act.name} ON) and '{off.name}' "
                                 f"({act.name} OFF) touch at {var.label()} = "
                                 f"{fmt_frac(a.lo.value if a.lo.value == b.hi.value else a.hi.value)}{unit}",
                                 ctx.rule_name_span(later), f"leave a gap so {act.name} does not chatter",
                                 _pair_related(ctx, on, off), gap="0")
                    continue
                codes_txt, dead = "", None
                if var.agg != "avg":
                    s = ir.sensor(var.sensor)
                    t_on = next(t for t in on.tests if isinstance(t, CodeRange))
                    t_off = next(t for t in off.tests if isinstance(t, CodeRange))
                    dead = (s.adc_max + 1) - len(_code_set(t_on, s.adc_max) | _code_set(t_off, s.adc_max))
                    codes_txt = f" ({dead} ADC code{'s' if dead != 1 else ''})"
                ctx.sink.add("I105", f"hysteresis gap of {fmt_frac(gap)}{unit}{codes_txt} between "
                             f"'{on.name}' ({act.name} ON) and '{off.name}' ({act.name} OFF)",
                             ctx.rule_name_span(later), None, _pair_related(ctx, on, off),
                             gap=fmt_frac(gap), codes=dead)
                if dead is not None and dead < 2 * J + 1:
                    ctx.sink.add("W112", f"hysteresis dead band of {dead} code{'s' if dead != 1 else ''} is "
                                 f"narrower than the ADC jitter (±{J} codes): {act.name} may chatter",
                                 ctx.rule_name_span(later), f"widen the gap to at least {2 * J + 1} codes",
                                 _pair_related(ctx, on, off))


def setters(ctx: C.Ctx) -> None:
    ir = ctx.ir
    unoq = ir.target == "unoq"
    for act in ir.actuators:
        on = bool(_setstate_rules(ctx, act.id, ON))
        off = bool(_setstate_rules(ctx, act.id, OFF))
        for e in ir.ensures:
            for a, s in e.closure:
                if a == act.id:
                    on |= s == ON
                    off |= s == OFF
        if unoq:
            for a, s in ir.fs_closure:
                if a == act.id:
                    on |= s == ON
                    off |= s == OFF
        span = ctx.checked.program.actuators[act.id].name.span
        if on and not off:
            ctx.sink.add("W106", f"actuator '{act.name}' can be switched ON but never OFF", span,
                         f"add a rule that sets {act.name} OFF, or FAILSAFE {act.name} OFF")
        elif off and not on and act.init == OFF:
            ctx.sink.add("W117", f"actuator '{act.name}' can never turn ON (it starts OFF and is only "
                         "ever set OFF)", span, f"add a rule that sets {act.name} ON")


def run(ctx: C.Ctx) -> None:
    reachability(ctx)
    conflicts_and_hysteresis(ctx)
    setters(ctx)
