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
from .runtime.engine import LinuxEngine
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


def load_trace(path: str | Path, ir: IRProgram) -> Trace:
    p = Path(path)
    return parse_trace(p.read_text(encoding="utf-8"), ir, p.as_posix())


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

    def run(self, trace: Trace) -> list[tuple]:
        table = build_table(self.ir)
        self.table = table
        unoq = table["target"] == "unoq"
        H = table["heartbeat_ms"]
        mcu = McuModel(table, self.mutant)
        self.mcu = mcu
        linux = LinuxEngine(table) if unoq else None
        events: list[tuple] = []
        pending: tuple[int, int, int] | None = None
        last_sent: int | None = None
        last_ack = 0
        seq = 0
        was_dead = False
        for idx, (t, codes) in enumerate(trace.rows):
            if idx == 0 and self.link_up_at_start:
                events.append(mcu.scenario_start(t, 1))
            if pending is not None:
                mcu.deliver_heartbeat(pending[0], pending[1], pending[2], t)
                pending = None
            events.extend(mcu.step(t, codes))
            self.states.append((t, list(mcu.cur)))
            if self.keep_frames:
                self.frames.append((t, self._frame(mcu, codes, t)))
            if not unoq:
                continue
            dead = self.kill_at is not None and t >= self.kill_at and \
                not (self.revive_at is not None and t >= self.revive_at)
            if dead:
                was_dead = True
                continue
            if was_dead:
                linux.reset()
                last_sent = None
                was_dead = False
            changed = False
            if self.deaf_at is None or t < self.deaf_at:
                old = linux.mask
                mask, fired, _ = linux.on_sample(t, codes)
                last_ack = t
                changed = mask != old
                events.extend(("NT", t, rid) for rid in fired)
            if changed or last_sent is None or t - last_sent >= H:
                seq = (seq + 1) & 0xFFFFFFFF
                pending = (seq, linux.mask, last_ack)
                last_sent = t
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
