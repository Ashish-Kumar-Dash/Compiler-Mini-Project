"""Placement analysis (Architecture §8.2, ADR-9): which processor runs each rule.

The unoq plan is always computed (and printed by `iotc check`); for --target mcu-only every rule
actually runs on the MCU."""
from __future__ import annotations

from dataclasses import dataclass

from .. import ast as A
from ..diagnostics import Related, Sink
from ..ir import fmt_duration
from ..options import Options

MAX_MASK_BITS = 32
MAX_SENSORS = 16


@dataclass(frozen=True)
class Placed:
    placement: str               # "MCU" | "LINUX" (actual, for this target)
    reasons: tuple[str, ...]
    plan: str                    # placement under --target unoq (for the report)
    plan_reasons: tuple[str, ...]
    mask_bit: int | None


def _aggs(rule: A.Rule) -> list[A.Agg]:
    return [c.value for c in rule.guard if isinstance(c.value, A.Agg)]


def plan_rule(rule: A.Rule, opts: Options) -> tuple[str, tuple[str, ...], A.Agg | None]:
    """unoq plan for one rule: (placement, reasons, offending window for P201 or None)."""
    must_mcu = rule.deadline_ms is not None
    must_linux = isinstance(rule.action, A.Notify)
    aggs = _aggs(rule) if opts.place_windows == "linux" else []
    if must_linux:
        return "LINUX", ("NOTIFY leaves the MCU",), None
    if must_mcu:
        return "MCU", (f"REALTIME {fmt_duration(rule.deadline_ms)}",), (aggs[0] if aggs else None)
    if aggs:
        a = aggs[0]
        return "LINUX", (f"{a.fn}({a.sensor.text}, {fmt_duration(a.window_ms)}): window history kept on Linux",), None
    return "MCU", ("default: no Bridge hop",), None


def place(program: A.Program, opts: Options, sink: Sink) -> list[Placed]:
    out: list[Placed] = []
    bit = 0
    for rule in program.rules:
        plan, plan_reasons, offending = plan_rule(rule, opts)
        if offending is not None and opts.target == "unoq":
            sink.add("P201", f"REALTIME rule '{rule.name.text}' uses {offending.fn}({offending.sensor.text}, "
                     f"{fmt_duration(offending.window_ms)}), which --place-windows=linux puts on Linux",
                     offending.span, "the Bridge has no bounded latency; use --place-windows=mcu or remove REALTIME",
                     [Related(rule.deadline_span, "REALTIME deadline here")] if rule.deadline_span else [])
        if opts.target == "mcu-only":
            placement = "MCU"
            reasons = plan_reasons if rule.deadline_ms is not None else ("--target mcu-only",)
        else:
            placement, reasons = plan, plan_reasons
        mask_bit = None
        if placement == "LINUX" and isinstance(rule.action, A.SetState):
            mask_bit = bit
            bit += 1
            if mask_bit == MAX_MASK_BITS:
                sink.add("P203", f"more than {MAX_MASK_BITS} rules placed on Linux", rule.name.span,
                         "the heartbeat mask has 32 bits; move rules to the MCU (--place-windows=mcu)")
        out.append(Placed(placement, reasons, plan, plan_reasons, mask_bit))
    for s in program.safeties:
        if isinstance(s, A.Ensure):
            for c in s.guard:
                if isinstance(c.value, A.Agg):
                    sink.add("P202", "ENSURE condition uses a windowed value", c.value.span,
                             "ENSURE runs on the MCU every pass; use raw sensor values only")
    if len(program.sensors) > MAX_SENSORS:
        extra = program.sensors[MAX_SENSORS]
        sink.add("P204", f"{len(program.sensors)} sensors declared (at most {MAX_SENSORS})", extra.name.span,
                 "one sample message carries at most 16 sensor codes")
    return out
