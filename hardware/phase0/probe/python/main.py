"""iotc Phase 0 probe — Linux side (hand-written; Architecture.md §15.1).

Prints a report every 10 s (App Lab "Python" console / `arduino-app-cli app logs`), writes it to
data/probe_report.json in the App folder, and serves it at http://<board>.local:7000 .
"""
import json
import os
import queue
import statistics
import sys
import time
from pathlib import Path

from arduino.app_bricks.web_ui import WebUI
from arduino.app_utils import App, Bridge

try:
    from arduino.app_utils import Leds
except Exception as exc:  # noqa: BLE001 - probe records everything
    Leds = None
    LEDS_IMPORT_ERROR = repr(exc)
else:
    LEDS_IMPORT_ERROR = None

T0 = time.monotonic()
DATA = Path(__file__).resolve().parent.parent / "data"
INFO_KEYS = {
    1: "micros_resolution_us", 2: "matrix_draw_avg_us_x10", 3: "adc_floating_min",
    4: "adc_floating_max", 5: "pinmode_keeps_prior_level", 6: "bridge_begin_ok",
    7: "burst_2kb_pass_us", 8: "serial_println_us", 9: "serial_begin_us",
    10: "hb_stack_distance_min", 11: "hb_stack_distance_max", 12: "matrix_draw_max_us",
    13: "adc_faults", 14: "last_heartbeat_age_ms",
}
TYPE_TESTS = [  # (bridge name, value sent, echo id, expected echo value or None if rejection expected, label)
    ("p_u8", 7, 0, 7, "u8 <- int 7"),
    ("p_u8", 255, 0, 255, "u8 <- int 255"),
    ("p_u16", 1023, 1, 1023, "u16 <- int 1023"),
    ("p_u32", 5, 2, 5, "u32 <- small int 5"),
    ("p_u32", 4_000_000_000, 2, 4_000_000_000, "u32 <- int 4e9"),
    ("p_bool", True, 3, 1, "bool <- True"),
    ("p_bool", 1, 3, 1, "bool <- int 1"),
    ("p_f", 1.5, 4, 1500, "float <- 1.5"),
    ("p_f", 2, 4, 2000, "float <- int 2"),
    ("p_u16", 70000, 1, None, "u16 <- 70000 (should be rejected)"),
]

inbox: "queue.Queue[tuple]" = queue.Queue(maxsize=4096)
report: dict = {
    "probe_version": 1,
    "python": sys.version,
    "python_started_monotonic": T0,
    "versions": {},
    "mcu": {},
    "loop_windows": [],
    "heartbeat_context": {},
    "rtt_ms": {},
    "types": {},
    "sample_message": {},
    "burst_messages_received": 0,
    "led1": {"import_error": LEDS_IMPORT_ERROR, "ok": 0, "errors": []},
    "mcu_uptime_at_first_contact_ms": None,
}


def _q(*item):
    try:
        inbox.put_nowait(item)
    except queue.Full:
        pass


# Bridge handlers run on a dedicated thread: only enqueue.
Bridge.provide("p_ack", lambda seq: _q("ack", seq, time.monotonic()))
Bridge.provide("p_echo", lambda i, v: _q("echo", i, v, time.monotonic()))
Bridge.provide("p_sample", lambda t, *codes: _q("sample", t, codes))
Bridge.provide("p_loop", lambda *a: _q("loop", a))
Bridge.provide("p_info", lambda k, v: _q("info", k, v))
Bridge.provide("p_burst", lambda k, *codes: _q("burst", k, codes))

ui = WebUI()
ui.expose_api("GET", "/api/report", lambda: state["snapshot"])

state = {
    "seq": 0, "sent": {}, "rtts": [], "last_hb": 0.0, "last_print": 0.0,
    "type_idx": 0, "type_next": None, "type_pending": {}, "led": False, "last_led": 0.0,
    "snapshot": {"status": "starting"}, "last_snap": 0.0,
}


def _versions() -> None:
    try:
        from importlib import metadata
        for dist in ("arduino_app_bricks", "arduino-router-bridge", "arduino_router_bridge"):
            try:
                report["versions"][dist] = metadata.version(dist)
            except metadata.PackageNotFoundError:
                pass
    except Exception as exc:  # noqa: BLE001
        report["versions"]["error"] = repr(exc)
    report["versions"]["env_APP_SOCKET"] = os.environ.get("APP_SOCKET")


def _handle(msg: tuple) -> None:
    kind = msg[0]
    if kind == "ack":
        _, seq, t = msg
        sent = state["sent"].pop(seq, None)
        if sent is not None:
            state["rtts"].append((t - sent) * 1000.0)
    elif kind == "echo":
        _, i, v, t = msg
        for label, (echo_id, expected, _sent) in list(state["type_pending"].items()):
            if echo_id == i and (expected is None or v == expected):
                report["types"][label] = {"received": True, "value": v,
                                          "ok": expected is not None}
                del state["type_pending"][label]
                break
    elif kind == "sample":
        _, t, codes = msg
        if report["mcu_uptime_at_first_contact_ms"] is None:
            report["mcu_uptime_at_first_contact_ms"] = t
        report["sample_message"] = {
            "arity": 1 + len(codes),
            "t_type": type(t).__name__,
            "code_types": sorted({type(c).__name__ for c in codes}),
            "codes_ok": list(codes) == [i * 64 + (codes[0] % 64) for i in range(16)],
            "last_mcu_t": t,
        }
        state["last_sample_t"] = t
    elif kind == "loop":
        a = msg[1]
        keys = ["mcu_t_ms", "passes", "min_us", "median_us", "max_us", "mean_us",
                "hb_total", "hb_main_stack", "hb_other_stack", "hb_during_loop"]
        row = dict(zip(keys, a))
        report["loop_windows"] = (report["loop_windows"] + [row])[-15:]
        report["heartbeat_context"] = {k: row[k] for k in keys[6:]}
    elif kind == "info":
        _, k, v = msg
        report["mcu"][INFO_KEYS.get(k, f"key{k}")] = v
    elif kind == "burst":
        report["burst_messages_received"] += 1


def _rtt_summary() -> dict:
    r = state["rtts"]
    if not r:
        return {"n": 0}
    s = sorted(r)
    return {"n": len(r), "median": round(statistics.median(s), 2),
            "p95": round(s[int(0.95 * (len(s) - 1))], 2), "max": round(s[-1], 2),
            "min": round(s[0], 2)}


def _type_tests(now: float) -> None:
    if "last_sample_t" not in state:
        return
    if state["type_next"] is None:
        state["type_next"] = now + 3.0
    for label, (_i, expected, sent) in list(state["type_pending"].items()):
        if now - sent > 2.0:
            report["types"][label] = {"received": False, "ok": expected is None}
            del state["type_pending"][label]
    if state["type_idx"] < len(TYPE_TESTS) and now >= state["type_next"]:
        name, value, echo_id, expected, label = TYPE_TESTS[state["type_idx"]]
        state["type_idx"] += 1
        state["type_next"] = now + 0.5
        state["type_pending"][label] = (echo_id, expected, now)
        try:
            Bridge.notify(name, value)
        except Exception as exc:  # noqa: BLE001
            report["types"][label] = {"received": False, "send_error": repr(exc)}


def _led1(now: float) -> None:
    if Leds is None or now - state["last_led"] < 1.0:
        return
    state["last_led"] = now
    state["led"] = not state["led"]
    try:
        Leds.set_led1_color(False, state["led"], False)
        report["led1"]["ok"] += 1
    except Exception as exc:  # noqa: BLE001
        if len(report["led1"]["errors"]) < 3:
            report["led1"]["errors"].append(repr(exc))


def _print_report() -> None:
    report["rtt_ms"] = _rtt_summary()
    report["python_uptime_s"] = round(time.monotonic() - T0, 1)
    text = json.dumps(report, indent=1, sort_keys=True, default=str)
    print("===== IOTC PROBE REPORT =====", flush=True)
    print(text, flush=True)
    print("===== END REPORT =====", flush=True)
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "probe_report.json").write_text(text + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print("could not write report:", repr(exc), flush=True)


def loop() -> None:
    try:
        while True:
            try:
                _handle(inbox.get_nowait())
            except queue.Empty:
                break
        now = time.monotonic()
        if now - state["last_hb"] >= 0.1:  # heartbeat every 100 ms for the RTT measurement
            state["last_hb"] = now
            state["seq"] = (state["seq"] + 1) & 0xFFFFFFFF
            seq = state["seq"]
            state["sent"][seq] = now
            if len(state["sent"]) > 500:
                state["sent"].pop(next(iter(state["sent"])))
            Bridge.notify("p_hb", seq, 0xA5A5A5A5, int(state.get("last_sample_t", 0)) & 0xFFFFFFFF)
        _type_tests(now)
        _led1(now)
        if now - state["last_snap"] >= 1.0:  # the web API serves a copy, never the live dict
            state["last_snap"] = now
            report["rtt_ms"] = _rtt_summary()
            state["snapshot"] = json.loads(json.dumps(report, default=str))
        if now - state["last_print"] >= 10.0:
            state["last_print"] = now
            _print_report()
    except Exception as exc:  # noqa: BLE001 - never let the probe die
        print("probe loop error:", repr(exc), flush=True)
    time.sleep(0.005)


_versions()
print(f"iotc probe python started; python {sys.version.split()[0]}", flush=True)
App.run(user_loop=loop)
