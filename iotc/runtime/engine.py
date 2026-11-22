"""Shared rule-evaluation runtime (Architecture §9.4–§9.6). NORMATIVE.

Stdlib only: this package is byte-copied into the board App (python/iotc_runtime/) and drives the
Linux side there; the interpreter uses the same code. It consumes a plain *table* (dicts, lists,
ints, strings) produced by the compiler (iotc/table.py):

  table = {
    "sample_ms", "heartbeat_ms", "failsafe_timeout_ms", "target",
    "sensors":   [{"name", "adc_max"}],
    "actuators": [{"name", "init"}],                       # init: 1 = ON, 0 = OFF
    "windows":   [{"sensor", "window_ms", "mcu", "linux"}],
    "rules":     [{"name", "tests", "hold_ms", "action", "placement", "mask_bit"}],
    "nevers":    [[a, s1, b, s2]],
    "ensures":   [{"tests", "closure": [[a, s]]}],
    "fs":        [[a, s]],
  }
  tests:  ["range", "raw", sensor, lo, hi]  |  ["range", "min"|"max", window, lo, hi]
          ["avg", window, op, p, q]                        (lo/hi None = unbounded)
  action: ["set", actuator, 1|0]  |  ["notify", text]
"""
from __future__ import annotations

from .window import Window

U32 = 0xFFFFFFFF


def make_windows(table: dict, side: str) -> dict[int, Window]:
    """Windows maintained on one side ("mcu" or "linux"), keyed by window id."""
    S = table["sample_ms"]
    return {i: Window(w["window_ms"], S) for i, w in enumerate(table["windows"]) if w[side]}


def test_holds(test: list, codes: list[int], windows: dict[int, Window]) -> bool:
    if test[0] == "range":
        _, kind, idx, lo, hi = test
        if lo is not None and hi is not None and lo > hi:
            return False
        if kind == "raw":
            c = codes[idx]
            return (lo is None or c >= lo) and (hi is None or c <= hi)
        return windows[idx].range_test(kind, lo, hi)
    _, idx, op, p, q = test
    return windows[idx].avg_test(op, p, q)


def tests_hold(tests: list, codes: list[int], windows: dict[int, Window]) -> bool:
    return all(test_holds(t, codes, windows) for t in tests)


class ForTimer:
    """FOR h, updated on ticks/samples only (§9.6). Times are unbounded ints (64-bit in C++)."""

    def __init__(self, hold_ms: int) -> None:
        self.hold_ms = hold_ms
        self.since: int | None = None
        self.held = False

    def update(self, tau: int, box: bool) -> None:
        if box:
            if self.since is None:
                self.since = tau
            self.held = self.held or (tau - self.since >= self.hold_ms)
        else:
            self.since = None
            self.held = False

    @property
    def running(self) -> bool:
        return self.since is not None and not self.held

    def reset(self) -> None:
        self.since = None
        self.held = False


class Notifier:
    """Edge-triggered NOTIFY with an armed flag (§9.6, ADR-10)."""

    def __init__(self) -> None:
        self.armed = True

    def update(self, holds: bool) -> bool:
        """Returns True when the notification fires on this tick/sample."""
        if holds and self.armed:
            self.armed = False
            return True
        if not holds:
            self.armed = True
        return False

    def reset(self) -> None:
        self.armed = True


class RuleClock:
    """Per-rule FOR timer and NOTIFY state for the rules evaluated on one side."""

    def __init__(self, table: dict, rule_ids: list[int]) -> None:
        self.table = table
        self.rule_ids = rule_ids
        self.timers = {i: ForTimer(table["rules"][i]["hold_ms"]) for i in rule_ids
                       if table["rules"][i]["hold_ms"] is not None}
        self.notifiers = {i: Notifier() for i in rule_ids if table["rules"][i]["action"][0] == "notify"}

    def tick(self, tau: int, codes: list[int], windows: dict[int, Window]) -> list[int]:
        """Update FOR timers and NOTIFY edges at a tick; return rule ids whose NOTIFY fires."""
        fired: list[int] = []
        for i in self.rule_ids:
            rule = self.table["rules"][i]
            box = tests_hold(rule["tests"], codes, windows)
            if i in self.timers:
                self.timers[i].update(tau, box)
            if i in self.notifiers:
                holds = box and (i not in self.timers or self.timers[i].held)
                if self.notifiers[i].update(holds):
                    fired.append(i)
        return fired

    def fires(self, i: int, codes: list[int], windows: dict[int, Window]) -> bool:
        """A SetState rule fires iff its tests hold now and (if FOR) its timer is held."""
        if not tests_hold(self.table["rules"][i]["tests"], codes, windows):
            return False
        return i not in self.timers or self.timers[i].held

    def reset(self) -> None:
        for t in self.timers.values():
            t.reset()
        for n in self.notifiers.values():
            n.reset()


class LinuxEngine:
    """The Linux side of the unoq target (§9.4): windows, FOR, NOTIFY and the firing mask.

    on_sample() is pure logic; heartbeat timing and I/O are done by the caller."""

    def __init__(self, table: dict) -> None:
        self.table = table
        self.linux_rules = [i for i, r in enumerate(table["rules"]) if r["placement"] == "LINUX"]
        self.windows = make_windows(table, "linux")
        self.clock = RuleClock(table, self.linux_rules)
        self.mask = 0
        self.last_t32: int | None = None
        self.t_hi = 0                  # unwrap of the u32 sample time

    def reset(self) -> None:
        for w in self.windows.values():
            w.reset()
        self.clock.reset()
        self.mask = 0
        self.last_t32 = None
        self.t_hi = 0

    def on_sample(self, t32: int, codes: list[int]) -> tuple[int, list[int], bool]:
        """Process one sample. Returns (mask, fired NOTIFY rule ids, reset_detected)."""
        reset = False
        t32 &= U32
        if self.last_t32 is not None:
            if (t32 - self.last_t32) & U32 >= 0x80000000:
                self.reset()
                reset = True
            elif t32 < self.last_t32:
                self.t_hi += 1 << 32
        self.last_t32 = t32
        tau = self.t_hi + t32
        for wid, w in self.windows.items():
            w.push(tau, codes[self.table["windows"][wid]["sensor"]])
        fired = self.clock.tick(tau, codes, self.windows)
        mask = 0
        for i in self.linux_rules:
            rule = self.table["rules"][i]
            if rule["mask_bit"] is not None and self.clock.fires(i, codes, self.windows):
                mask |= 1 << rule["mask_bit"]
        self.mask = mask
        return mask, fired, reset


class LinuxSide:
    """Sample processing plus the heartbeat policy of §9.4, shared by the board's main.py and the
    interpreter (which adds kill / revive / deaf injection around it).

    on_sample(t, codes) -> (heartbeat or None, fired NOTIFY rule ids, reset detected).
    A heartbeat (seq, mask, ack) is due when the mask changed, none was sent yet, or at least
    heartbeat_ms of sample time passed since the last one."""

    def __init__(self, table: dict) -> None:
        self.engine = LinuxEngine(table)
        self.H = table["heartbeat_ms"]
        self.seq = 0
        self.last_sent: int | None = None
        self.last_ack = 0

    def reset(self) -> None:
        self.engine.reset()
        self.last_sent = None

    def on_sample(self, t: int, codes: list[int]) -> tuple[tuple[int, int, int] | None, list[int], bool]:
        old = self.engine.mask
        mask, fired, reset = self.engine.on_sample(t, codes)
        self.last_ack = t & U32
        if mask != old or reset or self.last_sent is None or t - self.last_sent >= self.H:
            return self.beat(t), fired, reset
        return None, fired, reset

    def beat(self, t: int) -> tuple[int, int, int]:
        """A heartbeat now (also used by the caller's fallback timer when samples stop)."""
        self.seq = (self.seq + 1) & U32
        self.last_sent = t
        return self.seq, self.engine.mask & U32, self.last_ack
