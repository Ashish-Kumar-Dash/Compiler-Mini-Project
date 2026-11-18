"""Resource and timing checks (Architecture §8.4, §9.6, §9.7, §11.5, §11.8): W120, W121, I122,
P205, plus the numbers printed in the resources report."""
from __future__ import annotations

from dataclasses import dataclass

from ..ir import fmt_duration
from . import common as C

LINK_BYTES_PER_S = 11_520          # 115200 baud, 10 bits per byte
BUDGET_FRACTION = 0.30
MAX_LINUX_TO_MCU = 256
MATRIX_SENSORS, MATRIX_RULES, MATRIX_ACTUATORS = 4, 10, 5


def notify_bytes(name: str, arg_sizes: list[int]) -> int:
    """Worst-case msgpack size of a notify: [2, name, [args...]]."""
    n = len(arg_sizes)
    arr = 1 if n <= 15 else 3
    s = len(name)
    str_hdr = 1 if s <= 31 else 2
    return 1 + 1 + str_hdr + s + arr + sum(arg_sizes)


U8, U16, U32 = 2, 3, 5


@dataclass(frozen=True)
class Resources:
    windows: list[str]
    mcu_window_bytes: int
    mask_bits: int
    sample_ms: int
    sensors: int
    bridge_bps: int | None
    bridge_pct: float | None
    largest_in: int | None
    matrix: str | None


def run(ctx: C.Ctx) -> Resources:
    ir, prof, sink = ctx.ir, ctx.options.profile, ctx.sink
    S = ir.sample_ms
    for r in ir.rules:
        ast = ctx.rule_ast(r)
        if r.hold_ms is not None and (r.hold_ms < S or r.hold_ms % S):
            eff = -(-r.hold_ms // S) * S
            sink.add("W120", f"FOR {fmt_duration(r.hold_ms)} is not a multiple of the {S}ms sample period; "
                     f"it behaves as FOR {fmt_duration(eff)}", ast.hold_span,
                     f"use a multiple of {S}ms")
        if r.deadline_ms is not None and r.deadline_ms < prof.min_deadline_ms:
            sink.add("W121", f"REALTIME {fmt_duration(r.deadline_ms)} is below the {prof.name} pass floor "
                     f"of about {prof.min_deadline_ms}ms", ast.deadline_span,
                     f"use REALTIME {prof.min_deadline_ms}ms or more")

    windows, mcu_bytes = [], 0
    for w in ir.windows:
        s = ir.sensors[w.sensor_id]
        where = " and ".join(p for p, on in (("MCU", w.on_mcu), ("Linux", w.on_linux)) if on)
        windows.append(f"{s.name} over {fmt_duration(w.window_ms)}: {w.buckets} buckets on {where}")
        if w.on_mcu:
            mcu_bytes += w.buckets * prof.slot_bytes

    bps = pct = largest = None
    matrix = None
    if ir.target == "unoq":
        n = len(ir.sensors)
        sample = notify_bytes("sample", [U32] + [U16] * n)
        stats = notify_bytes("stats", [U32] * 4)
        bps = int(sample * 1000 / S) + stats
        pct = 100.0 * bps / LINK_BYTES_PER_S
        largest = max(notify_bytes("heartbeat", [U32] * 3), notify_bytes("sim_set", [U8, U16]))
        if pct > 100 * BUDGET_FRACTION:
            sink.add("P205", f"estimated Bridge traffic {bps} B/s is {pct:.0f}% of the 115200-baud link "
                     f"(budget {int(100 * BUDGET_FRACTION)}%)", None,
                     "raise --sample-ms or use fewer sensors")
    if prof.has_matrix and ir.target in ("unoq", "mcu-only"):
        over = []
        if len(ir.sensors) > MATRIX_SENSORS:
            over.append(f"{len(ir.sensors)} sensors (shows {MATRIX_SENSORS})")
        if len(ir.rules) > MATRIX_RULES:
            over.append(f"{len(ir.rules)} rules (shows {MATRIX_RULES})")
        if len(ir.actuators) > MATRIX_ACTUATORS:
            over.append(f"{len(ir.actuators)} actuators (shows {MATRIX_ACTUATORS})")
        if over:
            sink.add("I122", "LED matrix display truncated: " + ", ".join(over), None,
                     "the first ones in source order are shown")
        matrix = (f"{min(len(ir.sensors), MATRIX_SENSORS)} sensor bars, "
                  f"{min(len(ir.rules), MATRIX_RULES)} rule pixels, "
                  f"{min(len(ir.actuators), MATRIX_ACTUATORS)} actuator blocks")
    return Resources(windows, mcu_bytes, len(ir.linux_setstate_rules), S, len(ir.sensors), bps, pct,
                     largest, matrix)
