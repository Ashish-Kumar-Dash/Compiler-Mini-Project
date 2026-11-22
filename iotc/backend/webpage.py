"""Dashboard generator (Architecture §11.9): assets/index.html, app.js, style.css (static), the
generated assets/config.json, and the two client libraries copied from the board's template."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..analysis.report import Report
from ..diagnostics import to_ascii
from ..ir import CodeRange, IRProgram
from .linux_py import sim_init

WEB = Path(__file__).parent / "web"
LIBS = Path(__file__).parent / "templates" / "blink-with-ui" / "assets" / "libs"
STATIC = ("index.html", "app.js", "style.css")


def _ticks(ir: IRProgram, report: Report | None) -> dict[int, list[dict]]:
    """Threshold codes per sensor with a label (raw and min/max code tests, average thresholds)."""
    out: dict[int, dict[int, str]] = {s.id: {} for s in ir.sensors}
    groups = [(r.name, r.tests) for r in ir.rules] + [(f"ENSURE#{e.id + 1}", e.tests) for e in ir.ensures]
    for where, tests in groups:
        for t in tests:
            sid = ir.sensor(t.var.sensor).id
            if isinstance(t, CodeRange):
                if t.never:
                    continue
                for v in (t.lo, t.hi):
                    if v is not None:
                        out[sid].setdefault(v, where)
            else:
                out[sid].setdefault(t.p // t.q, where)
    return {sid: [{"code": c, "label": lab} for c, lab in sorted(d.items())] for sid, d in out.items()}


def config(ir: IRProgram, report: Report | None, input_mode: str, scenario, demo_controls: bool) -> dict:
    ticks = _ticks(ir, report)
    deadlines = [r.deadline_ms for r in ir.rules if r.deadline_ms is not None and r.placement == "MCU"]
    rep = report.json() if report else {"placement": [], "safety": []}
    return {
        "program": Path(ir.name).name,
        "board": ir.board,
        "target": ir.target,
        "input": input_mode,
        "demo_controls": bool(demo_controls),
        "sample_ms": ir.sample_ms,
        "deadline_ms": min(deadlines) if deadlines else None,
        "sim_init": sim_init(ir, scenario),
        "sensors": [{"name": s.name, "unit": s.unit, "kind": s.kind, "lo": float(s.lo), "hi": float(s.hi),
                     "adc_max": s.adc_max, "ticks": ticks[s.id]} for s in ir.sensors],
        "actuators": [{"name": a.name, "pin": a.pin} for a in ir.actuators],
        "nevers": [{"a": ir.actuators[n.a].name, "s1": n.s1, "b": ir.actuators[n.b].name, "s2": n.s2}
                   for n in ir.nevers],
        "placement": [p for p in rep["placement"] if p["rule"] not in ("SAFETY", "FAILSAFE")],
        "safety": rep["safety"],
        "report": to_ascii(report.text()) if report else "",
    }


def assets(ir: IRProgram, report: Report | None, input_mode: str, scenario, demo_controls: bool) -> dict[str, bytes]:
    files = {f"assets/{name}": (WEB / name).read_bytes() for name in STATIC}
    cfg = json.dumps(config(ir, report, input_mode, scenario, demo_controls), ensure_ascii=False, indent=1)
    files["assets/config.json"] = (cfg + "\n").encode("utf-8")
    for lib in ("arduino.js", "socket.io.min.js"):
        files[f"assets/libs/{lib}"] = (LIBS / lib).read_bytes()
    return files


def check_static(html: str, js: str) -> list[str]:
    """Problems that must never ship (used by tests): innerHTML, remote URLs, inline scripts."""
    problems = []
    if "innerHTML" in js or "innerHTML" in html:
        problems.append("innerHTML used")
    if re.search(r"https?://", html + re.sub(r"`http://\$\{", "", js)):
        problems.append("remote URL")
    if re.search(r"<script>(?!\s*</script>)", html):
        problems.append("inline script")
    return problems
