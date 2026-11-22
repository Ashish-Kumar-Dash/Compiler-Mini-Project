"""Conformance mode (Architecture §3.5 of the plan, §9.9): the compiler predicts the board's log.

For an App built with `--input scenario`, the compiler runs the board model in a closed loop: the
sketch's pass (board_model.VirtualMcu around McuModel) and the Linux side (runtime.engine.LinuxSide,
the code main.py runs), one pass per tick, every heartbeat delivered on the next tick. That is the
board's behaviour whenever a Bridge round trip is shorter than one sample period.

The prediction is grouped by scenario iteration and keyed by the tick index from that iteration's
scenario start (SY), so it does not depend on when the board booted. The page compares it line by
line with what the board reports.
"""
from __future__ import annotations

from .backend.linux_py import sim_init
from .board_model import VirtualMcu
from .interp import format_event
from .ir import IRProgram
from .runtime.engine import LinuxSide
from .table import build_table


def body(line: str) -> str:
    """A canonical log line without its time field: 'EV,3000,fan,ON,...' -> 'EV,fan,ON,...'."""
    kind, _, rest = line.split(",", 2)
    return f"{kind},{rest}"


def simulate(ir: IRProgram, scenario: list[list[int]], iterations: int, boot_ms: int = 0) -> list[list[dict]]:
    """Closed-loop board model; the events of the first `iterations` scenario iterations.
    `boot_ms` is the board time of the first pass (tests: the result must not depend on it)."""
    table = build_table(ir)
    S = table["sample_ms"]
    clock = [0]
    mcu = VirtualMcu(table, scenario, sim_init(ir, scenario), lambda: clock[0], input_mode="scenario")
    side = LinuxSide(table)
    iters: list[list[dict]] = []
    starts: list[int] = []

    def record(ev: tuple) -> None:
        if ev[0] == "SY":
            iters.append([])
            starts.append(ev[1])
        elif iters:
            line = format_event(ev, table)
            iters[-1].append({"tick": (ev[1] - starts[-1]) // S, "body": body(line)})

    def on_sample(t: int, *codes: int) -> None:
        hb, fired, _ = side.on_sample(t, list(codes))
        for rid in fired:
            record(("NT", t, rid))
        if hb is not None:
            mcu.notify("heartbeat", *hb)

    mcu.on_event = record
    mcu.provide("sample", on_sample)
    mcu.provide("sync", lambda t, it: side.reset())
    limit = (iterations + 2) * (len(scenario) + table["scenario_align_ms"] // S + 50)
    k = 0
    while len(iters) <= iterations and k < limit:
        clock[0] = boot_ms + k * S
        mcu.pass_(clock[0])
        k += 1
    if len(iters) <= iterations:
        raise RuntimeError("the scenario never started: the model link did not come UP")
    return iters[:iterations]


def predict(ir: IRProgram, scenario: list[list[int]]) -> dict:
    """Predicted log for the page: iteration 1, iteration 2, and whether 2 repeats from then on."""
    its = simulate(ir, scenario, 3)
    steady = its[1] == its[2]
    return {"sample_ms": ir.sample_ms, "rows": len(scenario), "steady": steady,
            "iterations": its[:2] if steady else its}
