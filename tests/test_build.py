"""Build output (Architecture §11.3, §11.7): goldens, identifiers, determinism, writer safety."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from iotc.backend.app_writer import OutputError, slug, write
from iotc.backend.mcu_cpp import emit
from iotc.interp import TraceError, load_scenario
from iotc.options import Options
from iotc.pipeline import compile_file, compile_text

ROOT = Path(__file__).resolve().parents[1]


def ir_of(name="greenhouse_board", opts=None):
    return compile_file(ROOT / "examples" / f"{name}.iot", opts or Options.make("unoq")).ir


@pytest.mark.parametrize("tag,name,opts,mode", [
    ("board.unoq.pins", "greenhouse_board", Options.make("unoq"), "pins"),
    ("board.unoq.sim", "greenhouse_board", Options.make("unoq"), "sim"),
    ("board.mcu-only.scenario", "greenhouse_board", Options.make("unoq", "mcu-only"), "scenario"),
    ("wokwi.uno.pins", "greenhouse_wokwi", Options.make("uno"), "pins"),
])
def test_generated_header_golden(golden, tag, name, opts, mode):
    ir = ir_of(name, opts)
    scen = None
    if mode in ("scenario", "sim"):
        scen = load_scenario(ROOT / "scenarios" / ("demo_tour.csv" if mode == "scenario" else "fallback_sweep.csv"), ir)
    files = emit(ir, mode, scen)
    golden(f"backend/{tag}.iotc_gen.h", files["iotc_gen.h"])
    assert "\r" not in files["iotc_gen.h"]


def _code_only(h: str) -> str:
    """C++ text with string literals and // comments removed."""
    h = re.sub(r'"(?:[^"\\]|\\.)*"', '""', h)
    return re.sub(r"//[^\n]*", "", h)


def test_no_dsl_name_becomes_an_identifier():
    text = ("SENSOR zebra AT A0 ANALOG UNIT C RANGE 0..100;\nACTUATOR quokka AT D7;\n"
            "RULE wombat: WHEN zebra > 50 THEN quokka ON;\nRULE numbat: WHEN zebra < 40 THEN quokka OFF;\n")
    h = emit(compile_text(text, Options.make("unoq", "mcu-only")).ir)["iotc_gen.h"]
    code = _code_only(h)
    for name in ("zebra", "quokka", "wombat", "numbat"):
        assert name not in code, name                 # never an identifier (ADR-26)
    for name in ("quokka", "wombat", "numbat"):       # actuator/rule names only as string literals
        assert f'"{name}"' in h, name


KEYWORD_NAMES = (
    "SENSOR int AT A0 ANALOG UNIT C RANGE 0..100;\n"
    "SENSOR class AT A1 ANALOG UNIT C RANGE 0..100;\n"
    "ACTUATOR loop AT D7;\n"
    "ACTUATOR setup AT D8;\n"
    "RULE delay: WHEN int > 50 THEN loop ON;\n"
    "RULE millis: WHEN int < 40 THEN loop OFF;\n"
    "RULE new: WHEN avg(class, 10s) > 50 THEN setup ON;\n"
    "RULE register: WHEN class < 40 THEN setup OFF;\n"
    'RULE pass: WHEN int > 90 THEN NOTIFY "C:\\\\ ??/ \\"q\\" </script>";\n'
)


@pytest.mark.diff
def test_keyword_names_and_nasty_text_compile(tmp_path):
    from tests.test_differential import HAVE_GXX, SHIM, wsl
    if not HAVE_GXX:
        pytest.skip("WSL g++ not available")
    for target in ("unoq", "mcu-only"):
        res = compile_text(KEYWORD_NAMES, Options.make("unoq", target))
        assert res.ok, [(d.code, d.message) for d in res.sink.items]
        d = tmp_path / target
        d.mkdir()
        for fname, body in emit(res.ir).items():
            (d / fname).write_text(body, encoding="utf-8", newline="\n")
        for f in SHIM.iterdir():
            shutil.copy(f, d / f.name)
    r = wsl('cd "$(wslpath -a "$1")" && for t in unoq mcu-only; do (cd $t && g++ -std=gnu++17 -Wall -Wextra '
            '-Werror -trigraphs -I. main.cpp -o sim) || exit 1; done', str(tmp_path))
    assert r.returncode == 0, r.stderr.decode()[-3000:]


def test_build_is_deterministic_across_hash_seeds(tmp_path):
    outs = []
    for seed in ("1", "999"):
        out = tmp_path / f"o{seed}"
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONUTF8="1")
        subprocess.run([sys.executable, "-m", "iotc", "build", "examples/greenhouse_board.iot", "--target",
                        "mcu-only", "--input", "scenario", "--scenario", "scenarios/demo_tour.csv", "-o", str(out)],
                       cwd=ROOT, check=True, env=env, capture_output=True)
        outs.append({p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()})
    assert outs[0] == outs[1]
    assert all(b"\r" not in data for data in outs[0].values())


def test_app_folder_layout(tmp_path):
    files = write(ir_of(opts=Options.make("unoq", "mcu-only")), tmp_path / "app")
    assert files == ["app.yaml", "python/main.py", "sketch/iotc_gen.h", "sketch/sketch.ino", "sketch/sketch.yaml"]
    app = (tmp_path / "app" / "app.yaml").read_text(encoding="utf-8")
    assert "icon: \U0001F331" in app and "ports:" not in app
    assert "RouterBridge" not in (tmp_path / "app" / "sketch" / "sketch.yaml").read_text()


def test_uno_sketch_folder_named_after_directory(tmp_path):
    files = write(ir_of("greenhouse_wokwi", Options.make("uno")), tmp_path / "wokwi_sketch")
    assert files == ["iotc_gen.h", "wokwi_sketch.ino"]


def test_writer_refuses_foreign_directory_and_replaces_own(tmp_path):
    foreign = tmp_path / "mine"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("keep me")
    with pytest.raises(OutputError):
        write(ir_of(opts=Options.make("unoq", "mcu-only")), foreign)
    assert (foreign / "notes.txt").read_text() == "keep me"
    own = tmp_path / "own"
    write(ir_of(opts=Options.make("unoq", "mcu-only")), own)
    (own / "stale.txt").write_text("old")
    write(ir_of(opts=Options.make("unoq", "mcu-only")), own)
    assert not (own / "stale.txt").exists()


def test_unoq_target_app_is_not_yet_available(tmp_path):
    with pytest.raises(OutputError):
        write(ir_of(), tmp_path / "x")


@pytest.mark.parametrize("stem,expected", [("My Greenhouse", "my_greenhouse"), ("con", "app_con"),
                                           ("1st", "app_1st"), ("naïve", "na_ve")])
def test_slug(stem, expected):
    assert slug(stem) == expected


def test_scenario_rows_must_be_consecutive(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("t_ms,temp,humidity\n0,25,50\n200,25,50\n")
    with pytest.raises(TraceError, match="consecutive"):
        load_scenario(p, ir_of())


def test_cli_build_errors_exit_2(tmp_path):
    env = dict(os.environ, PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-m", "iotc", "build", "examples/greenhouse_board.iot", "--target", "mcu-only",
                        "--input", "scenario", "-o", str(tmp_path / "x")], cwd=ROOT, env=env, capture_output=True)
    assert r.returncode == 2 and b"needs --scenario" in r.stderr
