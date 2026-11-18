"""Shared helpers for the analyses: variable domains and box algebra (Architecture §7, §8).

A box maps variables to intervals; an unmentioned variable ranges over its full domain."""
from __future__ import annotations

from dataclasses import dataclass

from .. import ast as A
from ..diagnostics import Sink
from ..ir import Box, Interval, IRProgram, IRRule, Var
from ..options import Options
from ..sema.checker import Checked
from ..source import Span


@dataclass
class Ctx:
    """Everything an analysis may need: the IR plus the checked AST for spans and source text."""

    ir: IRProgram
    checked: Checked
    options: Options
    sink: Sink

    @property
    def ast_rules(self) -> tuple[A.Rule, ...]:
        return self.checked.program.rules

    @property
    def ast_ensures(self) -> list[A.Ensure]:
        return [s for s in self.checked.program.safeties if isinstance(s, A.Ensure)]

    @property
    def ast_nevers(self) -> list[A.Never]:
        return [s for s in self.checked.program.safeties if isinstance(s, A.Never) and s.a.text != s.b.text]

    def rule_ast(self, rule: IRRule) -> A.Rule:
        return self.ast_rules[rule.id]

    def rule_name_span(self, rule: IRRule) -> Span:
        return self.rule_ast(rule).name.span

    def guard_span(self, cmps: tuple[A.Cmp, ...]) -> Span:
        return cmps[0].span.to(cmps[-1].span)

    def act_name(self, aid: int) -> str:
        return self.ir.actuators[aid].name

    def unit(self, var: Var) -> str:
        u = self.ir.sensor(var.sensor).unit
        return f" {u}" if u else ""


def domain(ir: IRProgram, var: Var) -> Interval:
    s = ir.sensor(var.sensor)
    return Interval.closed(s.lo, s.hi, integer=s.kind == "DIGITAL" and var.agg != "avg")


def get(ir: IRProgram, box: Box, var: Var) -> Interval:
    return box[var] if var in box else domain(ir, var)


def vars_of(*boxes: Box) -> list[Var]:
    seen: list[Var] = []
    for b in boxes:
        for v in b:
            if v not in seen:
                seen.append(v)
    return seen


def intersect(ir: IRProgram, a: Box, b: Box) -> dict[Var, Interval]:
    return {v: get(ir, a, v).intersect(get(ir, b, v)) for v in vars_of(a, b)}


def is_empty(box: Box) -> bool:
    return any(iv.is_empty() for iv in box.values())


def subset(ir: IRProgram, inner: Box, outer: Box) -> bool:
    """inner ⊆ outer (an empty inner box is a subset of anything)."""
    if is_empty(inner):
        return True
    return all(get(ir, outer, v).contains(get(ir, inner, v)) for v in vars_of(inner, outer))


def is_full(ir: IRProgram, box: Box) -> bool:
    return all(iv.same_set(domain(ir, v)) for v, iv in box.items())


def raw_only(box: Box) -> bool:
    return not any(v.is_window for v in box)


def fmt_box(box: Box, ctx: Ctx | None = None) -> str:
    parts = []
    for v, iv in box.items():
        unit = ctx.unit(v) if ctx and str(iv) not in ("∅",) and not iv.integer else ""
        parts.append(f"{v.label()} ∈ {iv}{unit}")
    return " and ".join(parts) if parts else "always"


def cmp_text(c: A.Cmp) -> str:
    v = c.value
    val = v.sensor.text if isinstance(v, A.SensorRef) else \
        f"{v.fn}({v.sensor.text}, {_dur(v.window_ms)})"
    unit = f" {c.unit.text}" if c.unit else ""
    return f"{val} {c.op} {c.number.text}{unit}"


def guard_text(cmps: tuple[A.Cmp, ...]) -> str:
    return " AND ".join(cmp_text(c) for c in cmps)


def _dur(ms: int) -> str:
    from ..ir import fmt_duration
    return fmt_duration(ms)
