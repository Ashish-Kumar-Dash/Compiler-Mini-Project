"""Reference model of one MCU pass (Architecture §9.3). NORMATIVE; the generated C++ mirrors it.

Stdlib only (lives in the runtime package). Events are tuples:
    ("LK", t, up)                                   link UP/DOWN
    ("EV", t, actuator, state, src, result, detail) state 1/0; result applied|refused|forced|failsafe
    ("NT", t, rule_id)                              NOTIFY fired (mcu-only target)
    ("SY", t, iteration)                            scenario start (§9.9)
Mutants (fuzzer self-checks, §10.2): "disable-monitor", "single-pass-never", "fs-over-ensure".
"""
from __future__ import annotations

from .engine import RuleClock, make_windows, tests_hold

ON, OFF = 1, 0


class McuModel:
    def __init__(self, table: dict, mutant: str | None = None) -> None:
        self.t = table
        self.unoq = table["target"] == "unoq"
        self.S = table["sample_ms"]
        self.T = table["failsafe_timeout_ms"]
        self.mutant = mutant
        n_act = len(table["actuators"])
        self.cur = [a["init"] for a in table["actuators"]]
        self.refused_val: list[int | None] = [None] * n_act
        self.prev_p_eq_cur = [True] * n_act
        self.last_n: int | None = None
        # link state (§9.1)
        self.link_up = False
        self.ever_beat = False
        self.last_beat = 0
        self.ack_t = 0
        self.mask_rx = 0
        self.mask_l = 0
        self.last_seq = 0
        self.ack_floor: int | None = None          # scenario start: ignore older acknowledgements
        self.pending_beat: tuple[int, int, int, int] | None = None
        self.hb_acks: list[int] = []               # seqs to acknowledge (Bridge RTT, §9.3 step 12)
        mcu_rules = [i for i, r in enumerate(table["rules"]) if r["placement"] == "MCU"]
        self.mcu_rules = mcu_rules
        self.windows = make_windows(table, "mcu")
        self.clock = RuleClock(table, mcu_rules)
        self.mask_rule = {r["mask_bit"]: i for i, r in enumerate(table["rules"]) if r["mask_bit"] is not None}
        # display state for the LED matrix (§11.8)
        self.status = {"failsafe": False, "ensure": False, "refused_until": -1, "beat": False}
        self.firing: list[int] = [0] * len(table["rules"])

    # -- inputs from the Bridge (handler side, ADR-23) ----------------------------------------
    def deliver_heartbeat(self, seq: int, mask: int, ack: int, beat_ms: int) -> None:
        self.pending_beat = (seq, mask, ack, beat_ms)

    def force_link_up(self, now: int) -> None:
        """Scenario start (§9.9) / --link-up-at-start: link UP with mask 0, no LK event."""
        self.link_up = True
        self.ever_beat = True
        self.last_beat = now
        self.ack_t = now
        self.mask_l = 0
        self.mask_rx = 0
        self.ack_floor = now

    # -- one pass ---------------------------------------------------------------------------
    def step(self, now: int, codes: list[int]) -> list[tuple]:
        tick, tau, events = self.begin_pass(now)
        return events + self.finish_pass(now, tick, tau, codes)

    def begin_pass(self, now: int) -> tuple[bool, int, list[tuple]]:
        """Steps 2 and 4a: tick detection and, on ticks, the link latch. The C++ starts a scenario
        between this and finish_pass (cpp_core.py pass())."""
        events: list[tuple] = []
        n = now // self.S
        tick = n != self.last_n
        if tick:
            self.last_n = n
        tau = n * self.S
        if tick and self.unoq:
            if self.pending_beat is not None:
                seq, mask, ack, beat_ms = self.pending_beat
                self.pending_beat = None
                self.hb_acks.append(seq)
                if self.ack_floor is None or ack >= self.ack_floor:
                    self.ever_beat = True
                    self.mask_rx = mask
                    self.last_beat = beat_ms
                    self.ack_t = ack
                    self.last_seq = seq
                    self.status["beat"] = not self.status["beat"]
            up = self.ever_beat and (now - self.last_beat) < self.T and (tau - self.ack_t) < self.T
            if up != self.link_up:
                self.link_up = up
                events.append(("LK", now, up))
            if self.link_up:
                self.mask_l = self.mask_rx
        return tick, tau, events

    def finish_pass(self, now: int, tick: bool, tau: int, codes: list[int]) -> list[tuple]:
        """Steps 4b-11 of one pass, after begin_pass."""
        t, events = self.t, []
        if tick:
            for wid, w in self.windows.items():
                w.push(tau, codes[t["windows"][wid]["sensor"]])
            for rid in self.clock.tick(tau, codes, self.windows):
                events.append(("NT", tau, rid))
            for a in range(len(self.cur)):
                if self.prev_p_eq_cur[a]:
                    self.refused_val[a] = None

        # step 5-6: requests
        req: list[dict[int, list[int]]] = [{ON: [], OFF: []} for _ in self.cur]
        self.firing = [0] * len(t["rules"])
        for i in self.mcu_rules:
            rule = t["rules"][i]
            if rule["action"][0] != "set":
                continue
            if self.clock.fires(i, codes, self.windows):
                _, a, s = rule["action"]
                req[a][s].append(i)
                self.firing[i] = 1
            elif i in self.clock.timers and self.clock.timers[i].running:
                self.firing[i] = 2
        if self.unoq and self.link_up:
            for bit, i in sorted(self.mask_rule.items()):
                if self.mask_l >> bit & 1:
                    _, a, s = t["rules"][i]["action"]
                    req[a][s].append(i)
                    self.firing[i] = 1

        # step 7: resolve (OFF wins)
        nxt = list(self.cur)
        for a in range(len(nxt)):
            if req[a][OFF]:
                nxt[a] = OFF
            elif req[a][ON]:
                nxt[a] = ON
        mark: list[tuple[str, str] | None] = [None] * len(nxt)    # (result, src) for immune values

        def apply_fs() -> None:
            if self.unoq and not self.link_up:
                for a, s in t["fs"]:
                    if self.mutant == "fs-over-ensure" or mark[a] is None:
                        nxt[a] = s
                        mark[a] = ("failsafe", "FAILSAFE")

        def apply_ensures() -> None:
            for k, e in enumerate(t["ensures"]):
                if tests_hold(e["tests"], codes, self.windows):
                    for a, s in e["closure"]:
                        nxt[a] = s
                        if mark[a] is None or mark[a][0] != "forced":
                            mark[a] = ("forced", f"ENSURE#{k + 1}")

        if self.mutant == "fs-over-ensure":
            apply_ensures()
            apply_fs()
        else:
            apply_fs()
            apply_ensures()
        self.status["failsafe"] = self.unoq and not self.link_up and bool(t["fs"])
        self.status["ensure"] = any(m is not None and m[0] == "forced" for m in mark)
        p = list(nxt)

        # step 10: NEVER fixpoint
        refused_by: list[int | None] = [None] * len(nxt)
        if self.mutant != "disable-monitor":
            rounds = 1 if self.mutant == "single-pass-never" else max(1, len(nxt))
            for _ in range(rounds):
                if self.mutant == "single-pass-never":
                    for j, (a, s1, b, s2) in enumerate(t["nevers"]):     # v2: one sequential pass
                        if nxt[a] == s1 and nxt[b] == s2:
                            for x in (a, b):
                                if mark[x] is None and nxt[x] != self.cur[x]:
                                    nxt[x] = self.cur[x]
                                    refused_by[x] = j if refused_by[x] is None else refused_by[x]
                    break
                violated = [j for j, (a, s1, b, s2) in enumerate(t["nevers"]) if nxt[a] == s1 and nxt[b] == s2]
                if not violated:
                    break
                revert: dict[int, int] = {}
                for j in violated:
                    a, _, b, _ = t["nevers"][j]
                    for x in (a, b):
                        if mark[x] is None and nxt[x] != self.cur[x] and x not in revert:
                            revert[x] = j
                if not revert:
                    break
                for x, j in revert.items():
                    nxt[x] = self.cur[x]
                    if refused_by[x] is None:
                        refused_by[x] = j

        # step 11: commit and log (actuators in declaration order)
        for a in range(len(nxt)):
            if nxt[a] != self.cur[a]:
                if mark[a] is not None:
                    result, src = mark[a]
                else:
                    result, src = "applied", t["rules"][min(req[a][nxt[a]])]["name"]
                events.append(("EV", now, a, nxt[a], src, result, "-"))
                self.refused_val[a] = None
            elif p[a] != self.cur[a]:
                if self.refused_val[a] != p[a]:
                    src = t["rules"][min(req[a][p[a]])]["name"]
                    detail = f"never#{refused_by[a] + 1}" if refused_by[a] is not None else "-"
                    events.append(("EV", now, a, p[a], src, "refused", detail))
                    self.refused_val[a] = p[a]
                    self.status["refused_until"] = now + 1000
            self.prev_p_eq_cur[a] = p[a] == self.cur[a]
        self.cur = nxt
        return events

    # -- scenario start ---------------------------------------------------------------------
    def scenario_start(self, now: int, iteration: int) -> tuple:
        for w in self.windows.values():
            w.reset()
        self.clock.reset()
        if self.unoq:
            self.force_link_up(now)
        return ("SY", now, iteration)
