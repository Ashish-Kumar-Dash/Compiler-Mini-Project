"""Reference interpreter (Architecture §9.10, §10.1): runs a trace through the MCU model and, for
the unoq target, the Linux engine, and produces the canonical event log (§9.8)."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .escape import csv_field
from .ir import IRProgram
from .runtime.engine import LinuxSide
from .runtime.matrix import frame
from .runtime.mcu import McuModel
from .table import build_table

KIND_RANK = {"LK": 0, "EV": 1, "NT": 2, "SY": 3}


class TraceError(Exception):
    """Invalid trace file (exit code 2)."""


@dataclass
class Trace:
    rows: list[tuple[int, list[int]]]


def parse_trace(text: str, ir: IRProgram, name: str = "trace") -> Trace:
    reader = csv.reader(io.StringIO(text))
    lines = [(i + 1, row) for i, row in enumerate(reader)
             if row and not row[0].lstrip().startswith("#")]
    if not lines:
        raise TraceError(f"{name}: empty trace")
    (hline, header), body = lines[0], lines[1:]
    header = [h.strip() for h in header]
    if header[0] != "t_ms":
        raise TraceError(f"{name}:{hline}: first column must be t_ms")
    cols: list[tuple[int, bool]] = []
    seen: set[str] = set()
    for h in header[1:]:
        sname, _, suffix = h.partition(":")
        if suffix not in ("", "raw"):
            raise TraceError(f"{name}:{hline}: bad column '{h}' (use <sensor> or <sensor>:raw)")
        try:
            s = ir.sensor(sname)
        except KeyError:
            raise TraceError(f"{name}:{hline}: unknown sensor column '{sname}'") from None
        if sname in seen:
            raise TraceError(f"{name}:{hline}: duplicate column '{sname}'")
        seen.add(sname)
        cols.append((s.id, suffix == "raw"))
    missing = [s.name for s in ir.sensors if s.name not in seen]
    if missing:
        raise TraceError(f"{name}:{hline}: missing column(s) {', '.join(missing)}")
    S = ir.sample_ms
    rows: list[tuple[int, list[int]]] = []
    last_t = None
    for lineno, row in body:
        if len(row) != len(header):
            raise TraceError(f"{name}:{lineno}: expected {len(header)} fields, got {len(row)}")
        try:
            t = int(row[0])
        except ValueError:
            raise TraceError(f"{name}:{lineno}: t_ms must be an integer") from None
        if t < 0 or t % S:
            raise TraceError(f"{name}:{lineno}: t_ms {t} is not a non-negative multiple of {S}")
        if last_t is not None and t <= last_t:
            raise TraceError(f"{name}:{lineno}: t_ms must increase strictly")
        last_t = t
        codes = [0] * len(ir.sensors)
        for (sid, raw), cell in zip(cols, row[1:]):
            s = ir.sensors[sid]
            try:
                v = Fraction(cell.strip())
            except (ValueError, ZeroDivisionError):
                raise TraceError(f"{name}:{lineno}: bad value '{cell}'") from None
            if s.kind == "DIGITAL":
                if v not in (0, 1):
                    raise TraceError(f"{name}:{lineno}: DIGITAL sensor '{s.name}' must be 0 or 1")
                codes[sid] = int(v)
            elif raw:
                if v.denominator != 1 or not 0 <= v <= s.adc_max:
                    raise TraceError(f"{name}:{lineno}: raw code for '{s.name}' must be 0..{s.adc_max}")
                codes[sid] = int(v)
            else:
                codes[sid] = s.quantise(v)
        rows.append((t, codes))
    return Trace(rows)


def load_scenario(path: str | Path, ir: IRProgram) -> list[list[int]]:
    """A compiled-in scenario (§9.2): a trace whose rows are consecutive ticks from t = 0."""
    p = Path(path)
    tr = parse_trace(p.read_text(encoding="utf-8"), ir, p.as_posix())
    for i, (t, _) in enumerate(tr.rows):
        if t != i * ir.sample_ms:
            raise TraceError(f"{p.as_posix()}: scenario row {i + 1} has t_ms {t}; rows must be consecutive "
                             f"ticks 0, {ir.sample_ms}, {2 * ir.sample_ms}, ...")
    if not tr.rows:
        raise TraceError(f"{p.as_posix()}: empty scenario")
    if len(tr.rows) > 3000:
        raise TraceError(f"{p.as_posix()}: scenario longer than 3000 rows")
    return [codes for _, codes in tr.rows]


def load_trace(path: str | Path, ir: IRProgram) -> Trace:
    p = Path(path)
    return parse_trace(p.read_text(encoding="utf-8"), ir, p.as_posix())


class LinuxSim:
    """The Linux side under the §9.10 time model, row by row: runtime.engine.LinuxSide (the same
    code the board runs) plus kill / revive / deaf injection. Its output depends only on the trace,
    so the differential test can precompute the heartbeats it delivers to the compiled C++."""

    def __init__(self, table: dict, kill_at: int | None = None, revive_at: int | None = None,
                 deaf_at: int | None = None) -> None:
        self.side = LinuxSide(table)
        self.kill_at, self.revive_at, self.deaf_at = kill_at, revive_at, deaf_at
        self.was_dead = False

    def row(self, t: int, codes: list[int]) -> tuple[tuple[int, int, int] | None, list[tuple]]:
        """Process row t after the MCU pass: (heartbeat to deliver at the next row, NT events)."""
        dead = (self.kill_at is not None and t >= self.kill_at
                and not (self.revive_at is not None and t >= self.revive_at))
        if dead:
            self.was_dead = True
            return None, []
        if self.was_dead:
            self.side.reset()
            self.was_dead = False
        if self.deaf_at is not None and t >= self.deaf_at:
            if self.side.last_sent is None or t - self.side.last_sent >= self.side.H:
                return self.side.beat(t), []          # still heartbeating, acknowledgement stale
            return None, []
        hb, fired, _ = self.side.on_sample(t, codes)
        return hb, [("NT", t, rid) for rid in fired]


@dataclass
class Interpreter:
    ir: IRProgram
    kill_at: int | None = None
    revive_at: int | None = None
    deaf_at: int | None = None
    link_up_at_start: bool = False
    mutant: str | None = None
    keep_frames: bool = False
    frames: list[tuple[int, list[int]]] = field(default_factory=list)
    states: list[tuple[int, list[int]]] = field(default_factory=list)   # (t, cur) after each pass
    heartbeats: list[tuple[int, tuple[int, int, int]]] = field(default_factory=list)  # (row t, hb)

    def run(self, trace: Trace) -> list[tuple]:
        table = build_table(self.ir)
        self.table = table
        unoq = table["target"] == "unoq"
        mcu = McuModel(table, self.mutant)
        self.mcu = mcu
        linux = LinuxSim(table, self.kill_at, self.revive_at, self.deaf_at) if unoq else None
        events: list[tuple] = []
        pending: tuple[int, int, int] | None = None
        for idx, (t, codes) in enumerate(trace.rows):
            if idx == 0 and self.link_up_at_start:
                events.append(mcu.scenario_start(t, 1))
            if pending is not None:
                self.heartbeats.append((t, pending))
                mcu.deliver_heartbeat(pending[0], pending[1], pending[2], t)
                pending = None
            events.extend(mcu.step(t, codes))
            self.states.append((t, list(mcu.cur)))
            if self.keep_frames:
                self.frames.append((t, self._frame(mcu, codes, t)))
            if linux is not None:
                pending, nts = linux.row(t, codes)
                events.extend(nts)
        return events

    def _frame(self, mcu: McuModel, codes: list[int], t: int) -> list[int]:
        st = mcu.status
        status = {"failsafe": st["failsafe"], "ensure": st["ensure"], "refused": t < st["refused_until"],
                  "beat": st["beat"], "bridge_failed": False}
        n = len(self.table["matrix"]["bars"])
        return frame(self.table["matrix"], codes[:n], mcu.firing, mcu.cur, status, (t // self.ir.sample_ms) & 1)


def format_event(ev: tuple, table: dict) -> str:
    kind = ev[0]
    if kind == "LK":
        return f"LK,{ev[1]},{'UP' if ev[2] else 'DOWN'}"
    if kind == "EV":
        _, t, a, s, src, result, detail = ev
        return f"EV,{t},{table['actuators'][a]['name']},{'ON' if s else 'OFF'},{src},{result},{detail}"
    if kind == "NT":
        rule = table["rules"][ev[2]]
        return f"NT,{ev[1]},{rule['name']},{csv_field(rule['action'][1])}"
    if kind == "SY":
        return f"SY,{ev[1]},{ev[2]}"
    raise ValueError(kind)


def canonical(events: list[tuple], table: dict) -> list[str]:
    def key(ev: tuple) -> tuple:
        idx = ev[2] if ev[0] in ("EV", "NT") else 0
        return (ev[1], KIND_RANK[ev[0]], idx)
    return [format_event(ev, table) for ev in sorted(events, key=key)]


def run_trace(ir: IRProgram, trace: Trace, **kw) -> list[str]:
    it = Interpreter(ir, **kw)
    events = it.run(trace)
    return canonical(events, it.table)
