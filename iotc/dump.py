"""`iotc dump --stage tokens|ast|ir|table`: print the output of one compiler stage.

tokens: the lexer's token stream (line:col-endcol KIND text value), the same text as the goldens.
ast:    the parser's abstract syntax tree (ast.dump).
ir:     the event IR after semantic checks and lowering: sensors with their code maps, windows,
        rules with their real boxes, compiled integer tests and placement, and the safety layer.
table:  the runtime table the back ends and the interpreter share (JSON).
"""
from __future__ import annotations

import json

from .adc import describe
from .ir import AvgTest, CodeRange, IRProgram, Notify, SetState, fmt_duration, fmt_frac
from .table import build_table


def tokens_text(tokens) -> str:
    lines = [f"{t.span.line}:{t.span.col}-{t.span.end_col} {t.kind.value} {t.text!r} {t.value!r}" for t in tokens]
    return "\n".join(lines) + "\n"


def _test(ir: IRProgram, t) -> str:
    name = t.var.label()
    if isinstance(t, CodeRange):
        return f"{name}: {describe(t)}"
    assert isinstance(t, AvgTest)
    return f"{name}: window non-empty and {t.q}*sum {t.op} {t.p}*count"


def _box(box) -> str:
    if not box:
        return "true (full domain)"
    return " AND ".join(f"{v.label()} in {iv}" for v, iv in sorted(box.items(), key=lambda kv: kv[0].sort_key()))


def _state(s) -> str:
    return s.value if hasattr(s, "value") else str(s)


def ir_text(ir: IRProgram) -> str:
    acts = ir.actuators
    out = [f"program {ir.name}  board {ir.board}  target {ir.target}",
           f"  sample {ir.sample_ms} ms, heartbeat {ir.heartbeat_ms} ms, failsafe timeout "
           f"{ir.failsafe_timeout_ms} ms, windows placed on {ir.place_windows}",
           "", "sensors"]
    for s in ir.sensors:
        if s.kind == "ANALOG":
            out.append(f"  s{s.id} {s.name} AT {s.pin} ANALOG {s.unit} [{fmt_frac(s.lo)}, {fmt_frac(s.hi)}]"
                       f"  code map x(c) = {fmt_frac(s.lo)} + c*{fmt_frac(s.hi - s.lo)}/{s.adc_max}, c in 0..{s.adc_max}")
        else:
            out.append(f"  s{s.id} {s.name} AT {s.pin} DIGITAL  c in 0..1")
    out += ["", "actuators"]
    for a in acts:
        out.append(f"  a{a.id} {a.name} AT {a.pin}{' (active-low)' if a.active_low else ''}  init {_state(a.init)}"
                   f"  failsafe {_state(a.failsafe) if a.failsafe is not None else '-'}")
    out += ["", "windows"]
    if not ir.windows:
        out.append("  (none)")
    for w in ir.windows:
        where = "+".join(p for p, on in (("MCU", w.on_mcu), ("Linux", w.on_linux)) if on) or "unused"
        out.append(f"  w{w.id} {ir.sensors[w.sensor_id].name} over {fmt_duration(w.window_ms)}: "
                   f"{w.buckets} buckets, computed on {where}")
    out += ["", "rules (event IR: WHEN box [FOR] -> action, compiled to integer code tests)"]
    for r in ir.rules:
        extra = []
        if r.deadline_ms is not None:
            extra.append(f"REALTIME {fmt_duration(r.deadline_ms)}")
        if r.hold_ms is not None:
            extra.append(f"FOR {fmt_duration(r.hold_ms)}")
        if r.mask_bit is not None:
            extra.append(f"mask bit {r.mask_bit}")
        out.append(f"  r{r.id} {r.name}  on {r.placement}" + (f"  ({', '.join(extra)})" if extra else ""))
        out.append(f"      box     {_box(r.guard)}")
        for t in r.tests:
            out.append(f"      test    {_test(ir, t)}")
        if isinstance(r.action, SetState):
            out.append(f"      action  {acts[r.action.actuator].name} := {_state(r.action.state)}")
        else:
            assert isinstance(r.action, Notify)
            out.append(f"      action  NOTIFY {r.action.text!r}")
        out.append(f"      why     {'; '.join(r.reasons)}")
    out += ["", "safety"]
    for n in ir.nevers:
        out.append(f"  never#{n.id + 1}  NOT ({acts[n.a].name} {_state(n.s1)} AND {acts[n.b].name} {_state(n.s2)})")
    for e in ir.ensures:
        out.append(f"  ensure#{e.id + 1}  {acts[e.actuator].name} {_state(e.state)} WHEN {_box(e.guard)}")
        for t in e.tests:
            out.append(f"      test    {_test(ir, t)}")
        out.append("      closure " + ", ".join(f"{acts[a].name} {_state(s)}" for a, s in e.closure))
    fs = ", ".join(f"{acts[a].name} {_state(s)}" for a, s in ir.fs_closure) or "(none)"
    out.append(f"  failsafe closure  {fs}")
    out.append("  initial state     " + ", ".join(f"{a.name} {_state(a.init)}" for a in acts))
    return "\n".join(out) + "\n"


def table_text(ir: IRProgram) -> str:
    return json.dumps(build_table(ir), indent=1, ensure_ascii=False) + "\n"
