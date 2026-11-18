"""Reference interpreter (Architecture §9, §10.1): trace goldens and targeted semantics checks."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from iotc.interp import Interpreter, TraceError, canonical, parse_trace, run_trace
from iotc.options import Options
from iotc.pipeline import compile_file, compile_text
from iotc.runtime.matrix import ascii_art

ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "tests" / "traces"


def board_ir(target="unoq"):
    return compile_file(ROOT / "examples" / "greenhouse_board.iot", Options.make("unoq", target)).ir


def ir_of(text, target="unoq"):
    res = compile_text(text, Options.make("unoq", target))
    assert res.ok, [(d.code, d.message) for d in res.sink.items]
    return res.ir


def rows(header, *data, step=100):
    lines = [header] + [f"{i * step},{d}" for i, d in enumerate(data)]
    return "\n".join(lines) + "\n"


def run(ir, text, **kw):
    return run_trace(ir, parse_trace(text, ir), **kw)


# -- goldens -----------------------------------------------------------------------------------

def test_hysteresis_golden(golden):
    ir = board_ir()
    golden("interp/hysteresis.log", "\n".join(run(ir, (TRACES / "hysteresis.csv").read_text())) + "\n")


def test_demo_script_golden(golden):
    ir = board_ir()
    text = (TRACES / "demo_script.csv").read_text()
    log = run(ir, text, kill_at=15000, revive_at=20000)
    golden("interp/demo_script.log", "\n".join(log) + "\n")
    assert "EV,16700,heater,OFF,FAILSAFE,failsafe,-" in log      # within 2 s of the last ack
    assert "EV,16700,fan,ON,cool,applied,-" in log                 # MCU rule kept working


def test_demo_script_matrix_golden(golden):
    ir = board_ir()
    it = Interpreter(ir, kill_at=15000, revive_at=20000, keep_frames=True)
    it.run(parse_trace((TRACES / "demo_script.csv").read_text(), ir))
    frames = dict(it.frames)
    out = []
    for t in (3800, 15500, 16700, 45000, 53000):
        out.append(f"t={t}\n{ascii_art(frames[t])}")
    golden("interp/demo_script.matrix", "\n".join(out) + "\n")


def test_mcu_only_target_runs_everything_on_mcu(golden):
    ir = board_ir("mcu-only")
    log = run(ir, (TRACES / "demo_script.csv").read_text())
    golden("interp/demo_script.mcu-only.log", "\n".join(log) + "\n")
    assert not any(line.startswith("LK") for line in log)
    assert "NT,50000,alert,\"Overheat\"" in log                    # same tick as on Linux


# -- monitor semantics -------------------------------------------------------------------------

CHAIN = ("SENSOR x AT A0 ANALOG UNIT C RANGE 0..100;\n"
         "ACTUATOR a AT D7;\nACTUATOR b AT D8;\nACTUATOR c AT D9;\n"
         "RULE ra: WHEN x > 50 THEN a ON;\nRULE rb: WHEN x > 50 THEN b OFF;\nRULE rc: WHEN x > 50 THEN c ON;\n"
         "RULE la: WHEN x < 10 THEN a OFF;\nRULE lb: WHEN x < 10 THEN b ON;\nRULE lc: WHEN x < 10 THEN c OFF;\n")


def _states(ir, text, mutant=None):
    it = Interpreter(ir, mutant=mutant)
    it.run(parse_trace(text, ir))
    return it.states


@pytest.mark.parametrize("order", ["SAFETY NEVER a ON AND b ON;\nSAFETY NEVER b OFF AND c ON;\n",
                                   "SAFETY NEVER b OFF AND c ON;\nSAFETY NEVER a ON AND b ON;\n"])
def test_never_fixpoint_holds_where_single_pass_fails(order):
    """Review counterexample: one sequential pass leaves NEVER a ON AND b ON violated."""
    ir = ir_of(CHAIN + order + "FAILSAFE b ON;\n", "mcu-only")
    trace = rows("t_ms,x", 20, 60, 60)
    for _, (a, b, c) in _states(ir, trace):
        assert not (a == 1 and b == 1) and not (b == 0 and c == 1)
    bad = _states(ir, trace, mutant="single-pass-never")
    violated = any((a, b) == (1, 1) or (b, c) == (0, 1) for _, (a, b, c) in bad)
    # v2's single pass depends on order: visiting P1 first lets P2's revert recreate P1's violation
    assert violated == order.startswith("SAFETY NEVER a ON")


def test_never_result_independent_of_declaration_order():
    base = CHAIN + "FAILSAFE b ON;\n"
    trace = rows("t_ms,x", 20, 60, 60, 5, 60)
    a = run(ir_of(base + "SAFETY NEVER a ON AND b ON;\nSAFETY NEVER b OFF AND c ON;\n", "mcu-only"), trace)
    b = run(ir_of(base + "SAFETY NEVER b OFF AND c ON;\nSAFETY NEVER a ON AND b ON;\n", "mcu-only"), trace)
    norm = [line.replace("never#1", "never#X").replace("never#2", "never#X") for line in a]
    norm_b = [line.replace("never#1", "never#X").replace("never#2", "never#X") for line in b]
    assert norm == norm_b


GH = ("SENSOR temp AT A0 ANALOG UNIT C RANGE -40..125;\nACTUATOR fan AT D7;\nACTUATOR heater AT D8;\n"
      "RULE cool: WHEN temp > 35 THEN fan ON;\nRULE uncool: WHEN temp < 30 THEN fan OFF;\n"
      "RULE warm: WHEN temp < 10 THEN heater ON;\nRULE unwarm: WHEN temp > 15 THEN heater OFF;\n"
      "SAFETY NEVER heater ON AND fan ON;\n")


def test_refused_logged_once_while_request_persists():
    ir = ir_of(GH.replace("RULE unwarm: WHEN temp > 15", "RULE unwarm: WHEN temp > 60"), "mcu-only")
    log = run(ir, rows("t_ms,temp", 5, *([40] * 50)))
    assert sum(",refused," in line for line in log) == 1


def test_both_sides_change_in_same_pass_both_refused():
    ir = ir_of("SENSOR t AT A0 ANALOG UNIT C RANGE 0..100;\nSENSOR h AT A1 ANALOG UNIT PCT RANGE 0..100;\n"
               "ACTUATOR fan AT D7;\nACTUATOR heater AT D8;\n"
               "RULE a: WHEN t > 35 THEN fan ON;\nRULE b: WHEN t < 30 THEN fan OFF;\n"
               "RULE c: WHEN h < 10 THEN heater ON;\nRULE d: WHEN h > 20 THEN heater OFF;\n"
               "SAFETY NEVER heater ON AND fan ON;\n", "mcu-only")
    log = run(ir, rows("t_ms,t,h", "20,50", "40,5", "40,5"))
    assert log == ["EV,100,fan,ON,a,refused,never#1", "EV,100,heater,ON,c,refused,never#1"]


def test_ensure_overrides_and_forces_partner():
    ir = ir_of(GH + "SAFETY ENSURE fan ON WHEN temp > 50;\n", "mcu-only")
    log = run(ir, rows("t_ms,temp", 5, 5, 55))
    assert log == ["EV,0,heater,ON,warm,applied,-",
                   "EV,200,fan,ON,ENSURE#1,forced,-", "EV,200,heater,OFF,ENSURE#1,forced,-"]


def test_ensure_beats_failsafe_when_linux_is_down():
    ir = ir_of("SENSOR temp AT A0 ANALOG UNIT C RANGE -40..125;\nACTUATOR fan AT D7;\n"
               "RULE uncool: WHEN avg(temp, 1s) < 30 THEN fan OFF;\nRULE cool: WHEN avg(temp, 1s) > 35 THEN fan ON;\n"
               "SAFETY ENSURE fan ON WHEN temp > 50;\nFAILSAFE fan OFF;\n")
    log = run(ir, rows("t_ms,temp", *([20] * 5), *([60] * 40)), kill_at=0)
    assert "EV,500,fan,ON,ENSURE#1,forced,-" in log and not any(l.startswith("LK") for l in log)


def test_boot_starts_in_failsafe_without_events():
    ir = board_ir()
    log = run(ir, rows("t_ms,temp,humidity", "25,50", "25,50"))
    assert log == ["LK,100,UP"]


def test_deaf_linux_goes_down_on_stale_acknowledgement():
    ir = board_ir()
    log = run(ir, rows("t_ms,temp,humidity", *(["25,50"] * 60)), deaf_at=1000)
    assert "LK,2900,DOWN" in log        # last acknowledged sample 900, timeout 2000


def test_kill_window_bounds():
    ir = board_ir()
    for kill in (1000, 1300, 1500, 1800):
        log = run(ir, rows("t_ms,temp,humidity", *(["25,50"] * 60)), kill_at=kill)
        (down,) = [int(l.split(",")[1]) for l in log if l.endswith("DOWN")]
        assert kill + 1400 <= down <= kill + 2100, (kill, down)


def test_for_fires_on_exact_hold_and_one_tick_dip_resets():
    ir = ir_of("SENSOR t AT A0 ANALOG UNIT C RANGE 0..100;\n"
               "RULE hot: WHEN t > 50 FOR 1s THEN NOTIFY \"hot\";\n", "mcu-only")
    log = run(ir, rows("t_ms,t", *([60] * 10), 60, 40, *([60] * 12)))
    assert log == ['NT,1000,hot,"hot"', 'NT,2200,hot,"hot"']


def test_notify_rearms_only_after_condition_drops():
    ir = ir_of("SENSOR t AT A0 ANALOG UNIT C RANGE 0..100;\nRULE hot: WHEN t > 50 THEN NOTIFY \"hot\";\n", "mcu-only")
    log = run(ir, rows("t_ms,t", 60, 60, 60, 40, 60))
    assert log == ['NT,0,hot,"hot"', 'NT,400,hot,"hot"']


def test_mutants_change_behaviour():
    ir = ir_of(GH.replace("RULE unwarm: WHEN temp > 15", "RULE unwarm: WHEN temp > 60"), "mcu-only")
    trace = rows("t_ms,temp", 5, 40)
    assert ["EV,100,fan,ON,cool,applied,-"] == [l for l in run(ir, trace, mutant="disable-monitor") if "fan" in l]
    assert any(",refused," in l for l in run(ir, trace))


def test_link_up_at_start_scenario():
    ir = board_ir()
    log = run(ir, rows("t_ms,temp,humidity", *(["10,50"] * 5)), link_up_at_start=True)
    assert log[0] == "SY,0,1" and "EV,100,heater,ON,warm,applied,-" in log


# -- traces --------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,msg", [
    ("t,temp,humidity\n0,1,1\n", "first column"),
    ("t_ms,temp\n0,1\n", "missing column"),
    ("t_ms,temp,humidity,temp\n0,1,1,1\n", "duplicate"),
    ("t_ms,temp,humidity\n0,1,1\n0,1,1\n", "increase"),
    ("t_ms,temp,humidity\n50,1,1\n", "multiple of 100"),
    ("t_ms,temp,humidity\n0,abc,1\n", "bad value"),
    ("t_ms,temp:raw,humidity\n0,2000,1\n", "0..1023"),
    ("t_ms,temp,humidity\n0,1\n", "expected 3 fields"),
])
def test_trace_errors(text, msg):
    with pytest.raises(TraceError, match=msg):
        parse_trace(text, board_ir())


def test_raw_and_unit_columns_agree():
    ir = board_ir()
    a = parse_trace("t_ms,temp,humidity\n0,35,20\n", ir).rows
    b = parse_trace("t_ms,temp:raw,humidity:raw\n0,465,205\n", ir).rows
    assert a == b                                    # 35 C is exactly code 465


def test_cli_run_and_trace_error_exit_codes():
    env = {"PYTHONUTF8": "1"}
    import os
    full = dict(os.environ, **env)
    ok = subprocess.run([sys.executable, "-m", "iotc", "run", "examples/greenhouse_board.iot", "--trace",
                         "tests/traces/hysteresis.csv"], cwd=ROOT, capture_output=True, env=full)
    assert ok.returncode == 0 and b"EV,200,fan,ON,cool,applied,-" in ok.stdout
    bad = subprocess.run([sys.executable, "-m", "iotc", "run", "examples/greenhouse_board.iot", "--trace",
                          "tests/traces/does_not_exist.csv"], cwd=ROOT, capture_output=True, env=full)
    assert bad.returncode == 2


def test_runtime_package_is_stdlib_only(tmp_path):
    """iotc/runtime ships to the board as python/iotc_runtime/ (Architecture §11.6)."""
    pkg = tmp_path / "iotc_runtime"
    shutil.copytree(ROOT / "iotc" / "runtime", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    code = (f"import sys; sys.path.insert(0, {str(tmp_path)!r}); import iotc_runtime.engine, iotc_runtime.mcu, iotc_runtime.matrix, iotc_runtime.window;"
            "assert not any(m == 'iotc' or m.startswith('iotc.') for m in sys.modules)")
    subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, check=True)
