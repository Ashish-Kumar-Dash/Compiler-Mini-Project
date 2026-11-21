"""Bridge contract (Architecture §11.5). THE ONLY PLACE BRIDGE NAMES LIVE (§0 rule 2).

Both emitters read this table: the C++ back end for notify calls and provide_safe handlers, the
Python back end for Bridge.provide handlers and argument coercion. Phase 0 showed that the MCU
side rejects a wrong type (an int for a bool) and silently truncates out-of-range integers, so the
Python side coerces and range-checks every argument before sending (§19).
"""
from __future__ import annotations

from dataclasses import dataclass

CPP_TYPES = {"u8": "uint8_t", "u16": "uint16_t", "u32": "uint32_t", "bool": "bool"}
MAX = {"u8": 0xFF, "u16": 0xFFFF, "u32": 0xFFFFFFFF}


@dataclass(frozen=True)
class Message:
    name: str
    direction: str                   # "mcu->linux" | "linux->mcu"
    args: tuple[tuple[str, str], ...]   # (arg name, type); "codes" = variadic u16 per sensor
    purpose: str

    def cpp_handler_params(self) -> str:
        return ", ".join(f"{CPP_TYPES[t]} {n}" for n, t in self.args)


SAMPLE = Message("sample", "mcu->linux", (("t", "u32"), ("codes", "u16*")), "sensor codes on each tick")
EVENT = Message("event", "mcu->linux",
                (("t", "u32"), ("act", "u8"), ("state", "u8"), ("src", "u8"), ("result", "u8"), ("detail", "u8")),
                "EV lines")
LINK = Message("link", "mcu->linux", (("t", "u32"), ("up", "u8")), "LK lines")
STATS = Message("stats", "mcu->linux",
                (("t", "u32"), ("overruns", "u32"), ("max_pass_us", "u32"), ("faults", "u32")), "ST lines")
HB_ACK = Message("hb_ack", "mcu->linux", (("seq", "u32"),), "Bridge round-trip measurement")
SYNC = Message("sync", "mcu->linux", (("t", "u32"), ("iteration", "u16")), "scenario start (§9.9)")
HEARTBEAT = Message("heartbeat", "linux->mcu", (("seq", "u32"), ("mask", "u32"), ("ack", "u32")),
                    "liveness + Linux rule flags")
SIM_SET = Message("sim_set", "linux->mcu", (("sensor", "u8"), ("code", "u16")), "--input sim")

MESSAGES = (SAMPLE, EVENT, LINK, STATS, HB_ACK, SYNC, HEARTBEAT, SIM_SET)

# event payload encodings (§11.5)
SRC_ENSURE_BASE = 0xF0
SRC_FAILSAFE = 0xFF
RESULTS = ("applied", "refused", "forced", "failsafe")
DETAIL_NONE = 0xFF


def by_name(name: str) -> Message:
    for m in MESSAGES:
        if m.name == name:
            return m
    raise KeyError(name)


def check_arg(kind: str, value: int) -> int:
    """Range-check an integer argument before sending it to the MCU (never silently truncate)."""
    v = int(value)
    if not 0 <= v <= MAX[kind]:
        raise ValueError(f"{v} does not fit {kind}")
    return v
