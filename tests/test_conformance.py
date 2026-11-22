"""Conformance mode: the compiler's predicted log (iotc/conformance.py) must equal
  * the reference interpreter on the scenario (an independent path),
  * the generated main.py driving the board model (the Linux program the board runs), and
  * the compiled C++ sketch in a closed loop with the Linux runtime (host shim, WSL g++),
and it must not depend on when the board booted (aligned scenario starts, §9.9)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from iotc.backend.mcu_cpp import emit
from iotc.board_model import VirtualMcu
from iotc.conformance import body, predict, simulate
from iotc.interp import Interpreter, canonical, format_event, load_scenario, parse_trace
from iotc.options import Options
from iotc.pipeline import compile_file, compile_text
from iotc.runtime.engine import LinuxSide
from iotc.table import build_table, scenario_align_ms
from iotc.virtual_board import Hub, load_main

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def demo():
    res = compile_file(ROOT / "examples" / "greenhouse_board.iot", Options.make("unoq"))
    assert res.ok
    return res, load_scenario(ROOT / "scenarios" / "demo_tour.csv", res.ir)


def keyed(lines, S):
    """Canonical lines grouped by SY into iterations of (tick from the start, body)."""
    its, t0 = [], None
    for ln in lines:
        kind, t = ln.split(",")[0], int(ln.split(",")[1])
        if kind == "SY":
            its.append([])
            t0 = t
        elif t0 is not None and kind in ("EV", "NT", "LK"):
            its[-1].append({"tick": (t - t0) // S, "body": body(ln)})
    return its


def test_scenario_alignment():
    def align(windows):
        decls = "".join(f"RULE r{i}: WHEN avg(temp, {w}) > 50 THEN fan ON;\nRULE q{i}: WHEN temp < 10 THEN fan OFF;\n"
                        for i, w in enumerate(windows))
        text = "SENSOR temp AT A0 ANALOG UNIT C RANGE 0..100;\nACTUATOR fan AT D7;\n" + decls
        res = compile_text(text, Options.make("unoq"))
        assert res.ok, [(d.code, d.message) for d in res.sink.items]
        return scenario_align_ms(res.ir)
    assert align([]) == 100                      # no windows: every tick
    assert align(["10s"]) == 2500                # B = 32: 10000 / gcd(10000, 32) = 625; lcm with 100
    assert align(["3200ms"]) == 100              # 3200 / gcd(3200, 32) = 100
    assert align(["10s", "7s"]) == 17500         # lcm(625, 875, 100)


def test_prediction_equals_interpreter(demo):
    res, scen = demo
    S = res.ir.sample_ms
    names = [s.name for s in res.ir.sensors]
    text = "t_ms," + ",".join(f"{n}:raw" for n in names) + "\n" + \
        "".join(f"{k * S}," + ",".join(map(str, r)) + "\n" for k, r in enumerate(scen))
    it = Interpreter(res.ir, link_up_at_start=True)
    want = keyed(canonical(it.run(parse_trace(text, res.ir)), it.table), S)[0]   # its own SY,0,1
    p = predict(res.ir, scen)
    assert p["steady"] and len(p["iterations"]) == 2
    assert p["iterations"][0] == want
    assert len(want) >= 9 and any("refused" in e["body"] for e in want) and any(e["body"].startswith("NT") for e in want)


@pytest.mark.parametrize("boot_ms", [0, 1700, 3300, 123400])
def test_prediction_does_not_depend_on_boot_time(demo, boot_ms):
    res, scen = demo
    assert simulate(res.ir, scen, 2, boot_ms) == simulate(res.ir, scen, 2, 0)


def test_generated_main_py_matches_prediction(demo, tmp_path):
    """The board model driven by the generated main.py Brain (scenario input), one pass per tick."""
    from iotc.backend.app_writer import write
    res, scen = demo
    out = tmp_path / "app"
    write(res.ir, out, "scenario", scen, res.report, True)
    clock = [5300]
    from iotc.backend.linux_py import sim_init
    mcu = VirtualMcu(build_table(res.ir), scen, sim_init(res.ir, scen), lambda: clock[0], input_mode="scenario")
    main, _ = load_main(out, mcu, Hub())
    lines = []
    brain = main.Brain(main.TABLE, mcu.notify, lambda k, d: None, lines.append, lambda: clock[0] / 1000)
    mcu.provided = {
        "sample": lambda t, *codes: brain.on_sample(t, codes, clock[0] / 1000),
        "event": brain.on_event, "link": brain.on_link, "stats": brain.on_stats,
        "hb_ack": lambda seq: brain.on_hb_ack(seq, clock[0] / 1000), "sync": brain.on_sync,
    }
    while sum(ln.startswith("SY,") for ln in lines) < 3:
        clock[0] += res.ir.sample_ms
        mcu.pass_(clock[0])
        brain.tick(clock[0] / 1000)
    p = predict(res.ir, scen)
    its = keyed(lines, res.ir.sample_ms)
    assert its[0] == p["iterations"][0] and its[1] == p["iterations"][1]


def test_page_config_carries_the_prediction(demo):
    from iotc.backend.webpage import config
    res, scen = demo
    cfg = config(res.ir, res.report, "scenario", scen, True)
    assert cfg["conformance"] == predict(res.ir, scen)
    assert config(res.ir, res.report, "sim", scen, True)["conformance"] is None


@pytest.mark.diff
def test_compiled_sketch_matches_prediction(demo, tmp_path):
    """Closed loop: the compiled C++ (host shim) on one side, the Linux runtime on the other."""
    from tests.test_differential import HAVE_GXX, SHIM, parse_output
    if not HAVE_GXX:
        pytest.skip("WSL g++ not available")
    res, scen = demo
    for fname, text in emit(res.ir, "scenario", scen).items():
        (tmp_path / fname).write_text(text, encoding="utf-8", newline="\n")
    for f in SHIM.iterdir():
        shutil.copy(f, tmp_path / f.name)
    build = subprocess.run(["wsl", "-e", "sh", "-c", 'cd "$(wslpath -a "$1")" && g++ -std=gnu++17 -O0 -Wall '
                            '-Wextra -Werror -I. main.cpp -o sim', "sh", str(tmp_path)], capture_output=True)
    assert build.returncode == 0, build.stderr.decode()[-3000:]
    proc = subprocess.Popen(["wsl", "-e", "sh", "-c", 'cd "$(wslpath -a "$1")" && ./sim', "sh", str(tmp_path)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    table = build_table(res.ir)
    side = LinuxSide(table)
    S = res.ir.sample_ms
    n_sens = len(res.ir.sensors)
    out, linux_nt, hb = [], [], None
    try:
        proc.stdin.write("M 1\n")
        t = 1700                                       # boot time: not aligned on purpose
        while sum(ln.startswith("S ") for ln in out) < 3 and t < 400000:
            if hb is not None:
                proc.stdin.write("H {} {} {}\n".format(*hb))
                hb = None
            proc.stdin.write(f"R {t} " + " ".join(["0"] * n_sens) + "\n")
            proc.stdin.flush()
            while True:
                line = proc.stdout.readline()
                assert line, "shim exited"
                line = line.rstrip("\n")
                out.append(line)
                parts = line.split()
                if parts[:2] == ["B", "sync"]:
                    side.reset()
                elif parts[:2] == ["B", "sample"]:
                    st, codes = int(parts[2]), [int(x) for x in parts[3:3 + n_sens]]
                    beat, fired, _ = side.on_sample(st, codes)
                    linux_nt += [("NT", st, rid) for rid in fired]
                    if beat is not None:
                        hb = beat
                elif parts[:1] == ["R"]:
                    break
            t += S
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)
    events, _, _ = parse_output("\n".join(out), table, res.ir)
    lines = canonical(events + linux_nt, table)
    its = keyed(lines, S)
    p = predict(res.ir, scen)
    assert len(its) >= 2
    assert its[0] == p["iterations"][0] and its[1] == p["iterations"][1]
