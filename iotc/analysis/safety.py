"""Safety analysis (Architecture §8.3): closures, ENSURE/FAILSAFE consistency, REALTIME
interference and sound NEVER proofs. Returns the safety report lines."""
from __future__ import annotations

from dataclasses import dataclass

from ..diagnostics import Related
from ..ir import OFF, ON, IRNever, IRRule, SetState, other
from . import common as C
from .closure import closure


@dataclass(frozen=True)
class SafetyLine:
    status: str          # "proven" | "enforced" | "info"
    head: str            # e.g. "NEVER heater ON AND fan ON"
    detail: list[str]    # continuation lines


def _never_text(ctx: C.Ctx, n: IRNever) -> str:
    return f"NEVER {ctx.act_name(n.a)} {n.s1} AND {ctx.act_name(n.b)} {n.s2}"


def _closure_text(ctx: C.Ctx, assignments) -> str:
    return ", ".join(f"{ctx.act_name(a)} {s}" for a, s in assignments)


def closures(ctx: C.Ctx) -> None:
    ir = ctx.ir
    ens_ast = ctx.ast_ensures
    for e in ir.ensures:
        cl = closure([(e.actuator, e.state)], ir.nevers)
        if cl.conflict is not None:
            ctx.sink.add("E116", f"ENSURE {ctx.act_name(e.actuator)} {e.state} is impossible: the NEVER pairs "
                         f"force {ctx.act_name(cl.conflict)} both ON and OFF", ens_ast[e.id].span,
                         "remove or change one of the NEVER pairs involved")
        elif len(e.closure) > 1:
            forced = [(a, s) for a, s in e.closure if a != e.actuator]
            ctx.sink.add("W107", f"ENSURE {ctx.act_name(e.actuator)} {e.state} also forces "
                         f"{_closure_text(ctx, forced)} (through NEVER pairs)", ens_ast[e.id].span,
                         "forced partners override their rules while the condition holds")
    for i, e1 in enumerate(ir.ensures):
        for e2 in ir.ensures[i + 1:]:
            overlap = C.intersect(ir, e1.guard, e2.guard)
            if C.is_empty(overlap):
                continue
            d1, d2 = dict(e1.closure), dict(e2.closure)
            clash = [a for a in d1 if a in d2 and d1[a] != d2[a]]
            if clash:
                a = clash[0]
                ctx.sink.add("E108", f"ENSURE#{e1.id + 1} and ENSURE#{e2.id + 1} force {ctx.act_name(a)} "
                             f"{d1[a]} and {d2[a]} when {C.fmt_box(overlap, ctx)}", ens_ast[e2.id].span,
                             "make the ENSURE conditions disjoint",
                             [Related(ens_ast[e1.id].span, f"ENSURE#{e1.id + 1} here")],
                             overlap=C.fmt_box(overlap, ctx))
    fs = closure([(f.actuator, f.state) for f in ir.failsafes], ir.nevers)
    fs_ast = ctx.checked.program.failsafes
    if fs.conflict is not None:
        ctx.sink.add("E109", f"the FAILSAFE states force {ctx.act_name(fs.conflict)} both ON and OFF "
                     "through NEVER pairs", fs_ast[0].span, "change one of the FAILSAFE states")
    else:
        init = dict(enumerate(ir.init))
        for n, n_ast in zip(ir.nevers, ctx.ast_nevers):
            if init[n.a] == n.s1 and init[n.b] == n.s2:
                fix = n.b if ir.actuators[n.b].failsafe is None else n.a
                ctx.sink.add("E109", f"the initial state violates {_never_text(ctx, n)}: "
                             f"{ctx.act_name(n.a)} starts {n.s1} and {ctx.act_name(n.b)} starts {n.s2}",
                             n_ast.span, f"add FAILSAFE {ctx.act_name(fix)} {other(init[fix])}")
    fs_map = dict(ir.fs_closure)
    for e in ir.ensures:
        clash = [(a, s) for a, s in e.closure if a in fs_map and fs_map[a] != s]
        if clash:
            parts = ", ".join(f"{ctx.act_name(a)} {s} (FAILSAFE: {fs_map[a]})" for a, s in clash)
            ctx.sink.add("W110", f"ENSURE {ctx.act_name(e.actuator)} {e.state} contradicts the FAILSAFE "
                         f"states: it forces {parts}", ens_ast[e.id].span,
                         "ENSURE wins while its condition holds, also when Linux is down")


def interference(ctx: C.Ctx) -> None:
    ir = ctx.ir
    unoq = ir.target == "unoq"
    fs_map = dict(ir.fs_closure)
    for r in ir.rules:
        if not isinstance(r.action, SetState):
            continue
        a, s = r.action.actuator, r.action.state
        if unoq and a in fs_map and r.placement == "MCU" and fs_map[a] != s:
            ctx.sink.add("W119", f"FAILSAFE {ctx.act_name(a)} {fs_map[a]} overrides MCU rule '{r.name}' "
                         "while Linux is down", ctx.rule_name_span(r),
                         f"'{r.name}' cannot set {ctx.act_name(a)} {s} without the Linux heartbeat")
        if r.deadline_ms is None:
            continue
        why = []
        for n in ir.nevers:
            if (n.a, n.s1) == (a, s) or (n.b, n.s2) == (a, s):
                why.append(f"refused by {_never_text(ctx, n)}")
        for e in ir.ensures:
            if dict(e.closure).get(a) == other(s):
                why.append(f"overridden by ENSURE#{e.id + 1}")
        if unoq and fs_map.get(a) == other(s):
            why.append("overridden by FAILSAFE")
        if why:
            ctx.sink.add("W111", f"REALTIME rule '{r.name}' ({ctx.act_name(a)} {s}) can be {', '.join(why)}",
                         ctx.rule_name_span(r), "its deadline covers evaluation, not the outcome")


def _covering(ctx: C.Ctx, r: IRRule, partner: int) -> bool:
    for q in ctx.ir.rules:
        if (isinstance(q.action, SetState) and q.action.actuator == partner and q.action.state == OFF
                and q.hold_ms is None and q.placement == r.placement and C.subset(ctx.ir, r.guard, q.guard)):
            return True
    return False


def _requests(ctx: C.Ctx, aid: int, state: str) -> list[IRRule]:
    return [r for r in ctx.ir.rules
            if isinstance(r.action, SetState) and r.action.actuator == aid and r.action.state == state
            and not C.is_empty(r.guard)]


def never_report(ctx: C.Ctx, n: IRNever) -> SafetyLine:
    head = _never_text(ctx, n)
    if (n.s1, n.s2) != (ON, ON):
        return SafetyLine("enforced", head, ["not proven (pairs with an OFF side are checked at runtime only)",
                                             "→ enforced by the MCU monitor (incumbent wins)"])
    for x, y in ((n.a, n.b), (n.b, n.a)):
        for r in _requests(ctx, x, ON):
            if not _covering(ctx, r, y):
                detail = [f"not proven: {r.name} ({ctx.act_name(x)} ON on {C.guard_text(ctx.rule_ast(r).guard)}) "
                          f"is not covered by a {ctx.act_name(y)} OFF rule"]
                for q in _requests(ctx, y, ON):
                    if not C.is_empty(C.intersect(ctx.ir, r.guard, q.guard)):
                        detail.append(f"and {q.name} ({ctx.act_name(y)} ON on "
                                      f"{C.guard_text(ctx.rule_ast(q).guard)}) can hold at the same time")
                        break
                detail.append("→ enforced by the MCU monitor (incumbent wins)")
                return SafetyLine("enforced", head, detail)
    return SafetyLine("proven", head, ["proven: every ON request is covered by an OFF request of the "
                                       "partner (OFF wins); the monitor still checks it"])


def report(ctx: C.Ctx) -> list[SafetyLine]:
    ir = ctx.ir
    lines = [never_report(ctx, n) for n in ir.nevers]
    for e, e_ast in zip(ir.ensures, ctx.ast_ensures):
        head = f"ENSURE {ctx.act_name(e.actuator)} {e.state} WHEN {C.guard_text(e_ast.guard)}"
        detail = "enforced by the MCU monitor every pass"
        if len(e.closure) > 1:
            detail += f"; closure: {_closure_text(ctx, e.closure)} (W107)"
        lines.append(SafetyLine("proven", head, [detail]))
    for f in ir.failsafes:
        head = f"FAILSAFE {ctx.act_name(f.actuator)} {f.state}"
        if ir.target == "unoq":
            detail = (f"initial state; applied after {ir.failsafe_timeout_ms} ms without a fresh heartbeat")
        else:
            detail = "initial state only (--target mcu-only has no Linux side)"
        lines.append(SafetyLine("proven", head, [detail]))
    return lines


def run(ctx: C.Ctx) -> list[SafetyLine]:
    closures(ctx)
    interference(ctx)
    return report(ctx)
