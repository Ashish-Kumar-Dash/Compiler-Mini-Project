"""The `iotc check` report (Architecture §8): code thresholds, placement, safety, resources.
Rendered as text and as the JSON `reports` object. Floats appear here for display only."""
from __future__ import annotations

from dataclasses import dataclass

from .. import adc
from .. import ast as A
from ..ir import AvgTest, IRProgram, Var
from . import common as C
from .resources import Resources
from .safety import SafetyLine


@dataclass
class Report:
    thresholds: list[dict]
    placement: list[dict]
    safety: list[SafetyLine]
    resources: Resources
    target: str
    board: str

    # -- text ---------------------------------------------------------------------------------
    def text(self) -> str:
        out: list[str] = []
        if self.thresholds:
            out.append(f"Code thresholds (board {self.board}, 10-bit ADC)")
            w1 = max(len(t["where"]) for t in self.thresholds)
            w2 = max(len(t["condition"]) for t in self.thresholds)
            for t in self.thresholds:
                out.append(f"  {t['where']:<{w1}}  {t['condition']:<{w2}}  ⇔  {t['code']}")
            out.append("")
        if self.placement:
            title = "Placement" if self.target == "unoq" else \
                "Placement (plan for --target unoq; --target mcu-only runs everything on the MCU)"
            out.append(title)
            w = max([len(p["rule"]) for p in self.placement] + [len("FAILSAFE")])
            out.append(f"  {'rule':<{w}}  runs on  reason")
            for p in self.placement:
                out.append(f"  {p['rule']:<{w}}  {p['runs_on']:<7}  {p['reason']}")
            out.append("")
        if self.safety:
            out.append("Safety")
            w = max(len(s.head) for s in self.safety)
            for s in self.safety:
                mark = "✓" if s.status == "proven" else "⚠"
                out.append(f"  {mark} {s.head:<{w}}  {s.detail[0]}")
                for d in s.detail[1:]:
                    out.append(f"    {'':<{w}}  {d}")
            out.append("")
        r = self.resources
        out.append(f"Resources (board {self.board}, target {self.target})")
        out.append(f"  sample period      {r.sample_ms} ms, {r.sensors} sensor{'s' if r.sensors != 1 else ''}")
        for wtxt in r.windows or ["none"]:
            out.append(f"  window             {wtxt}")
        out.append(f"  MCU window RAM     {r.mcu_window_bytes} B")
        if r.bridge_bps is not None:
            out.append(f"  Linux mask bits    {r.mask_bits} of 32")
            out.append(f"  Bridge MCU->Linux  ~{r.bridge_bps} B/s ({r.bridge_pct:.1f}% of 11520 B/s)")
            out.append(f"  Linux->MCU max     {r.largest_in} B per message (limit 256)")
        if r.matrix:
            out.append(f"  LED matrix         {r.matrix}")
        return "\n".join(out)

    # -- json ---------------------------------------------------------------------------------
    def json(self) -> dict:
        r = self.resources
        return {
            "thresholds": self.thresholds,
            "placement": self.placement,
            "safety": [{"status": s.status, "property": s.head, "detail": s.detail} for s in self.safety],
            "resources": {
                "sample_ms": r.sample_ms, "sensors": r.sensors, "windows": r.windows,
                "mcu_window_bytes": r.mcu_window_bytes, "linux_mask_bits": r.mask_bits,
                "bridge_bytes_per_s": r.bridge_bps,
                "bridge_percent": None if r.bridge_pct is None else round(r.bridge_pct, 1),
                "linux_to_mcu_max_bytes": r.largest_in, "matrix": r.matrix,
            },
        }


def _value_text(x) -> str:
    return f"{float(x):.2f}".rstrip("0").rstrip(".")


def threshold_rows(ctx: C.Ctx) -> list[dict]:
    ir = ctx.ir
    rows: list[dict] = []
    groups: list[tuple[str, tuple[A.Cmp, ...]]] = [(r.name.text, r.guard) for r in ctx.ast_rules]
    groups += [(f"ENSURE#{i + 1}", e.guard) for i, e in enumerate(ctx.ast_ensures)]
    for where, cmps in groups:
        for c in cmps:
            var = _var(c)
            s = ir.sensor(var.sensor)
            t = ctx.checked.thresholds[c]
            r = s.code_of(t)
            unit = f" {s.unit}" if s.unit else ""
            if var.agg == "avg":
                test: AvgTest = adc.avg_test(var, c.op, r, s.adc_max)
                code = f"{test.q}·sum {c.op} {test.p}·count  (average code {c.op} {_value_text(r)})"
            else:
                rng = adc.merge(var, [adc.bounds(c.op, r)], s.adc_max)
                code = adc.describe(rng)
                if s.kind == "ANALOG" and not rng.never:
                    edge = rng.lo if rng.lo is not None else rng.hi
                    if edge is not None:
                        code += f"  (code {edge} = {_value_text(s.value_of(edge))}{unit})"
                if var.is_window:
                    code = code.replace("code", f"{var.agg} code", 1)
            rows.append({"where": where, "condition": C.cmp_text(c), "code": code})
    return rows


def _var(c: A.Cmp) -> Var:
    v = c.value
    return Var(v.sensor.text, v.fn, v.window_ms) if isinstance(v, A.Agg) else Var(v.sensor.text)


def placement_rows(ir: IRProgram) -> list[dict]:
    rows = []
    plan = ir.extras.get("plan") if ir.target == "mcu-only" else None
    for i, r in enumerate(ir.rules):
        where, reasons = (plan[i] if plan else (r.placement, r.reasons))
        rows.append({"rule": r.name, "runs_on": "MCU" if where == "MCU" else "Linux", "reason": "; ".join(reasons)})
    if ir.nevers or ir.ensures:
        rows.append({"rule": "SAFETY", "runs_on": "MCU", "reason": "MCU owns the pins and can veto any write"})
    if ir.failsafes:
        rows.append({"rule": "FAILSAFE", "runs_on": "MCU",
                     "reason": "applied by the MCU when Linux heartbeats stop"})
    return rows
