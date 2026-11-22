"""Python model of the generated sketch's loop() on the UNO Q (cpp_core.py pass()).

runtime.mcu.McuModel is the normative pass; this adds the sketch's Bridge glue around it: the
handler snapshot (ADR-23), hb_ack, the input layer (sim latch with the 3 s fallback scenario, or a
compiled-in scenario started on link UP, §9.9) and the sample / event / link / stats / sync
notifies. Used by the virtual board (iotc dashboard) and by the conformance prediction.
"""
from __future__ import annotations

import threading
import time

from .backend import bridge
from .runtime.mcu import McuModel

SIM_TIMEOUT_MS = 3000           # cpp_core.py SIM_TIMEOUT_MS


class VirtualMcu:
    """The sketch's loop() around McuModel (cpp_core.py pass(), steps 1-12), input sim or scenario.

    sim: slider values latched on ticks; after 3 s without one, the compiled-in scenario loops.
    scenario: suspended (streaming the initial codes) until the link is UP on a tick aligned to
    scenario_align_ms, then a scenario start (sync) and one row per tick; after the last row it is
    suspended again until the next aligned tick (§9.9)."""

    def __init__(self, table: dict, scenario: list[list[int]] | None, sim_init: list[int],
                 clock, deadline_ms: int | None = None, autoplay: bool = False,
                 input_mode: str = "sim") -> None:
        if input_mode not in ("sim", "scenario") or (input_mode == "scenario" and not scenario):
            raise ValueError("VirtualMcu input is sim, or scenario with rows")
        self.mode = input_mode
        self.started = False
        self.row = 0
        self.iteration = 0
        self.t = table
        self.model = McuModel(table)
        self.S = table["sample_ms"]
        self.clock = clock                       # -> board milliseconds
        self.scen = scenario or []
        self.autoplay = autoplay and bool(self.scen)
        self.lock = threading.Lock()
        self.rx: tuple[int, int, int, int] | None = None
        self.sim_rx = list(sim_init)
        self.sim_rx_ms = 0
        self.sim_ever = False
        self.codes = list(sim_init)
        self.fb_row = 0
        self.last_n: int | None = None
        self.provided: dict = {}
        self.last_stats: int | None = None
        self.deadline_us = (deadline_ms or 0) * 1000
        self.max_pass_us = 0
        self.overruns = 0
        self.last_pass: float | None = None
        self.rule_id = {r["name"]: i for i, r in enumerate(table["rules"])}
        self.on_event = None                     # optional hook: every model event (prediction)

    # -- Bridge seen from main.py: Bridge.notify / Bridge.provide -----------------------------
    def notify(self, name: str, *args) -> None:
        """Linux -> MCU (the provide_safe handlers): store under the lock, as the sketch does."""
        msg = bridge.by_name(name)
        kinds = [k for _, k in msg.args]
        if len(args) != len(kinds) or not all(isinstance(a, int) and not isinstance(a, bool) for a in args):
            raise TypeError(f"{name}{args}: the MCU handler takes exactly {len(kinds)} integers")
        for a, k in zip(args, kinds):
            bridge.check_arg(k, a)
        now = self.clock()
        with self.lock:
            if name == "heartbeat":
                self.rx = (args[0], args[1], args[2], now)
            elif name == "sim_set":
                s, c = args
                if s < len(self.sim_rx) and c <= self.t["sensors"][s]["adc_max"]:
                    self.sim_rx[s] = c
                    self.sim_rx_ms = now
                    self.sim_ever = True

    def provide(self, name: str, fn) -> None:
        self.provided[name] = fn

    def _send(self, name: str, *args) -> None:
        fn = self.provided.get(name)
        if fn is not None:
            fn(*args)

    # -- one pass ---------------------------------------------------------------------------
    def encode(self, ev: tuple) -> tuple:
        _, t, a, s, src, result, detail = ev
        if src == "FAILSAFE":
            code = bridge.SRC_FAILSAFE
        elif src.startswith("ENSURE#"):
            code = bridge.SRC_ENSURE_BASE + int(src[7:]) - 1
        else:
            code = self.rule_id[src]
        det = bridge.DETAIL_NONE if detail == "-" else int(detail.split("#")[1]) - 1
        return (t, a, s, code, bridge.RESULTS.index(result), det)

    def _out(self, events: list[tuple]) -> None:
        for ev in events:
            if ev[0] == "LK":
                self._send("link", ev[1], 1 if ev[2] else 0)
            elif ev[0] == "EV":
                self._send("event", *self.encode(ev))
            elif ev[0] == "SY":
                self._send("sync", ev[1], ev[2])
            if self.on_event is not None:
                self.on_event(ev)

    def _stats(self, now: int) -> None:
        if self.last_stats is None or now - self.last_stats >= 1000:
            self.last_stats = now
            self._send("stats", now, self.overruns, self.max_pass_us, 0)

    def pass_(self, now: int) -> None:
        wall = time.perf_counter()
        if self.last_pass is not None:
            dt = int((wall - self.last_pass) * 1e6)
            self.max_pass_us = max(self.max_pass_us, dt)
            if self.deadline_us and dt > self.deadline_us:
                self.overruns += 1
        self.last_pass = wall
        with self.lock:                                         # step 1: snapshot (ADR-23)
            rx, self.rx = self.rx, None
            sim, ever, at = list(self.sim_rx), self.sim_ever, self.sim_rx_ms
        if rx is not None:
            self.model.deliver_heartbeat(*rx)
            self._send("hb_ack", rx[0])
        tick, tau, events = self.model.begin_pass(now)          # steps 2, 4a: tick, link
        self._out(events)
        if self.mode == "scenario":
            if tick and not self.started and self.model.link_up and tau % self.t["scenario_align_ms"] == 0:
                self.started, self.row = True, 0
                self.iteration += 1
                self._out([self.model.scenario_start(tau, self.iteration)])
            if not self.started:                                # suspended: initial codes only
                if tick:
                    self._send("sample", tau, *self.codes)
                return
            if tick:
                self.codes = list(self.scen[self.row])
        elif tick:                                              # step 3: inputs (sim latch)
            stale = self.autoplay or not ever or now - at >= SIM_TIMEOUT_MS
            if stale and self.scen:
                self.codes = list(self.scen[self.fb_row])
                self.fb_row = (self.fb_row + 1) % len(self.scen)
            elif ever:
                self.codes = sim
        self._out(self.model.finish_pass(now, tick, tau, self.codes))
        if tick:                                                # step 12: stream
            self._send("sample", tau, *self.codes)
        self._stats(now)
        if self.mode == "scenario" and tick:
            self.row += 1
            if self.row >= len(self.scen):                      # restart on the next aligned tick
                self.row = 0
                self.started = False
