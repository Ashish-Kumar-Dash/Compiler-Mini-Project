"""Virtual board (iotc dashboard): the generated main.py against the Python MCU glue.

The closed loop (VirtualMcu + the generated Brain on one fake clock) must give exactly the
interpreter's event log; the sim latch and fallback must follow cpp_core.py; the web server must
serve the unchanged dashboard and carry page messages to the Linux side."""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from iotc.backend.linux_py import sim_init
from iotc.interp import Interpreter, canonical, load_scenario, parse_trace
from iotc.options import Options
from iotc.pipeline import compile_file
from iotc.table import build_table
from iotc.board_model import VirtualMcu
from iotc.virtual_board import SHIM_JS, Hub, VirtualBoard, load_main

ROOT = Path(__file__).resolve().parents[1]
RANK = {"LK": 0, "EV": 1, "NT": 2}


@pytest.fixture(scope="module")
def board_ir():
    res = compile_file(ROOT / "examples" / "greenhouse_board.iot", Options.make("unoq"))
    assert res.ok
    return res


class FakeClock:
    def __init__(self):
        self.ms = 0

    def __call__(self):
        return self.ms


def closed_loop(res, tmp_path, scenario, rows):
    """VirtualMcu (autoplay) + generated Brain, one pass per tick, Linux handled synchronously."""
    from iotc.backend.app_writer import write
    out = tmp_path / "app"
    write(res.ir, out, "sim", scenario, res.report, True)
    clock = FakeClock()
    mcu = VirtualMcu(build_table(res.ir), scenario, sim_init(res.ir, scenario), clock, None, autoplay=True)
    main, _ = load_main(out, mcu, Hub())
    lines = []
    brain = main.Brain(main.TABLE, mcu.notify, lambda k, d: None, lines.append, lambda: clock.ms / 1000)
    mcu.provided = {
        "sample": lambda t, *codes: brain.on_sample(t, codes, clock.ms / 1000),
        "event": brain.on_event, "link": brain.on_link, "stats": brain.on_stats,
        "hb_ack": lambda seq: brain.on_hb_ack(seq, clock.ms / 1000), "sync": brain.on_sync,
    }
    S = res.ir.sample_ms
    for k in range(rows):
        clock.ms = k * S
        mcu.pass_(clock.ms)
        brain.tick(clock.ms / 1000)
    return [ln for ln in lines if ln[:2] in RANK], brain, mcu


def test_closed_loop_equals_interpreter(board_ir, tmp_path):
    scen = load_scenario(ROOT / "scenarios" / "demo_tour.csv", board_ir.ir)
    got, _, _ = closed_loop(board_ir, tmp_path, scen, len(scen))
    S = board_ir.ir.sample_ms
    names = [s.name for s in board_ir.ir.sensors]
    text = "t_ms," + ",".join(f"{n}:raw" for n in names) + "\n" + \
        "".join(f"{k * S}," + ",".join(map(str, row)) + "\n" for k, row in enumerate(scen))
    it = Interpreter(board_ir.ir)
    want = canonical(it.run(parse_trace(text, board_ir.ir)), it.table)
    got = sorted(got, key=lambda ln: (int(ln.split(",")[1]), RANK[ln[:2]]))
    assert len(want) >= 10 and any(",refused," in ln for ln in want) and any(ln.startswith("NT,") for ln in want)
    assert got == want


def test_sim_values_latch_on_ticks_and_fall_back_after_3s(board_ir):
    scen = load_scenario(ROOT / "scenarios" / "fallback_sweep.csv", board_ir.ir)
    clock = FakeClock()
    mcu = VirtualMcu(build_table(board_ir.ir), scen, sim_init(board_ir.ir, scen), clock)
    samples = []
    mcu.provide("sample", lambda t, *codes: samples.append((t, codes)))
    clock.ms = 50
    mcu.notify("sim_set", 0, 700)
    mcu.pass_(50)                                   # first pass is a tick: latched
    assert samples[-1] == (0, (700, scen[0][1]))
    for t in range(60, 3100, 10):
        clock.ms = t
        mcu.pass_(t)
    assert samples[-1][1][0] == 700                 # t = 3000: 2950 ms since the last sim_set
    clock.ms = 3100
    mcu.pass_(3100)                                 # 3050 ms: stale, the compiled-in scenario plays
    assert samples[-1] == (3100, tuple(scen[0]))
    clock.ms = 3200
    mcu.pass_(3200)
    assert samples[-1] == (3200, tuple(scen[1]))


def test_bridge_types_are_checked_like_the_mcu(board_ir):
    mcu = VirtualMcu(build_table(board_ir.ir), None, [512, 512], FakeClock())
    with pytest.raises(TypeError):
        mcu.notify("sim_set", True, 5)              # a bool is not an integer on the MCU side
    with pytest.raises(TypeError):
        mcu.notify("heartbeat", 1, 2)               # exact arity
    with pytest.raises(ValueError):
        mcu.notify("sim_set", 0, 70000)             # never truncate silently
    mcu.notify("sim_set", 0, 5000)                  # in u16 but above adc_max: ignored by the sketch
    assert mcu.sim_rx[0] == 512


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read()


def _wait(pred, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    return None


def test_server_serves_the_dashboard_and_carries_slider_moves(board_ir):
    scen = load_scenario(ROOT / "scenarios" / "fallback_sweep.csv", board_ir.ir)
    lines = []
    vb = VirtualBoard(board_ir.ir, board_ir.report, scen, port=0, emit=lines.append).start()
    try:
        base = vb.url
        status, html = _get(base)
        assert status == 200 and b"Content-Security-Policy" in html
        cfg = json.loads(_get(base + "config.json")[1])
        assert cfg["virtual"] is True and cfg["input"] == "sim"
        assert _get(base + "libs/arduino.js")[1] == SHIM_JS.encode()
        host, port = vb.server.server_address[:2]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/../python/main.py")
        assert conn.getresponse().status == 404
        conn.close()
        # the event stream: our session id, then the snapshot sent on connect
        ev = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        ev.request("GET", "/_iotc/events")
        resp = ev.getresponse()
        first = json.loads(resp.readline().decode()[6:])
        assert first["kind"] == "_sid"
        snap = None
        for _ in range(50):                         # live events may arrive before the snapshot
            line = resp.readline().decode()
            if line.startswith("data: "):
                msg = json.loads(line[6:])
                if msg["kind"] == "snapshot":
                    snap = msg
                    break
        assert snap is not None and len(snap["data"]["state"]["matrix"]) == 104
        ev.close()
        # a slider move from the page reaches the MCU: 72 C -> ENSURE forces the fan ON
        body = json.dumps({"kind": "sim_set", "data": {"sensor": 0, "code": 700}, "sid": first["data"]}).encode()
        req = urllib.request.Request(base + "_iotc/send", data=body, headers={"Content-Type": "application/json"})
        assert urllib.request.urlopen(req, timeout=5).status == 204
        assert _wait(lambda: json.loads(_get(base + "api/state")[1])["state"]["cur"][0] == 1)
        assert _wait(lambda: any(ln.startswith("EV,") and ",fan,ON,ENSURE#1,forced," in ln for ln in lines))
        assert any(ln.startswith("LK,") and ln.endswith(",UP") for ln in lines)
    finally:
        vb.close()


def test_cli_dashboard_needs_the_full_unoq_target():
    env = dict(os.environ, PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-m", "iotc", "dashboard", "examples/greenhouse_board.iot",
                        "--target", "mcu-only", "--no-browser"], cwd=ROOT, env=env, capture_output=True)
    assert r.returncode == 2 and b"full UNO Q App" in r.stderr
    r = subprocess.run([sys.executable, "-m", "iotc", "dashboard", "examples/greenhouse_board.iot",
                        "--autoplay", "--no-browser"], cwd=ROOT, env=env, capture_output=True)
    assert r.returncode == 2 and b"--autoplay needs --scenario" in r.stderr
