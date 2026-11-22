"""iotc dump: every stage prints, and the printed stages agree with the goldens and the IR."""
import os
import subprocess
import sys
from pathlib import Path

from iotc.dump import ir_text, table_text
from iotc.options import Options
from iotc.pipeline import compile_file

ROOT = Path(__file__).resolve().parents[1]
PROG = "examples/greenhouse_board.iot"


def run(*args):
    env = dict(os.environ, PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-m", "iotc", "dump", PROG, *args], cwd=ROOT, env=env, capture_output=True)
    return r.returncode, r.stdout.decode("utf-8").replace("\r\n", "\n")


def test_tokens_and_ast_match_the_goldens():
    assert run("--stage", "tokens") == (0, (ROOT / "tests/golden/lexer/greenhouse_board.tokens").read_text())
    assert run("--stage", "ast") == (0, (ROOT / "tests/golden/parser/greenhouse_board.ast").read_text())


def test_ir_shows_boxes_code_tests_placement_and_closures():
    text = ir_text(compile_file(ROOT / PROG, Options.make("unoq")).ir)
    for piece in ("box     temp in (35, 125]", "test    temp: code >= 466", "r2 warm  on LINUX  (mask bit 0)",
                  "5*sum < 1798*count", "closure fan ON, heater OFF", "failsafe closure  heater OFF",
                  "w0 temp over 10s: 32 buckets, computed on Linux"):
        assert piece in text, piece


def test_table_is_json_and_errors_exit_1(tmp_path):
    import json
    t = json.loads(table_text(compile_file(ROOT / PROG, Options.make("unoq")).ir))
    assert t["scenario_align_ms"] == 2500 and len(t["rules"]) == 6
    env = dict(os.environ, PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-m", "iotc", "dump", "examples/errors/L005_unknown_word.iot", "--stage",
                        "tokens"], cwd=ROOT, env=env, capture_output=True)
    assert r.returncode == 1 and b"L005" in r.stderr and b"ERROR" in r.stdout
