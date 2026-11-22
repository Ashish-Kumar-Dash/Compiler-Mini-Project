"""Regenerate docs/evidence/: one artifact per compiler stage for the demo program, plus
simulation evidence. Deterministic (LF, UTF-8, fixed inputs); run with `py -3.13 tasks.py evidence`.

Stage 1 grammar      -> stage1_source.iot, stage1_tokens.txt, stage1_ast.txt
Stage 2 checks       -> stage2_check_report.txt, stage2_error_messages.txt
Stage 3 event IR     -> stage3_ir.txt
Stage 4 target code  -> stage4_target/ (generated sketch, Linux program, page config)
Stage 5 simulation   -> stage5_*.txt (interpreter log, LED matrix, conformance prediction, tests)
board_results.md is written by hand from the board runs and is never overwritten.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from iotc.ast import dump as ast_dump  # noqa: E402
from iotc.backend.app_writer import write  # noqa: E402
from iotc.cli import check_output  # noqa: E402
from iotc.conformance import predict  # noqa: E402
from iotc.diagnostics import Sink  # noqa: E402
from iotc.dump import ir_text, tokens_text  # noqa: E402
from iotc.frontend import parse_file  # noqa: E402
from iotc.interp import Interpreter, canonical, load_scenario, load_trace  # noqa: E402
from iotc.lexer import lex  # noqa: E402
from iotc.options import Options  # noqa: E402
from iotc.pipeline import compile_file  # noqa: E402
from iotc.runtime.matrix import ascii_art  # noqa: E402
from iotc.source import load_source, write_text  # noqa: E402

PROGRAM = "examples/greenhouse_board.iot"
OUT = ROOT / "docs" / "evidence"
ERRORS = ["L005_unknown_word", "L003_glued_unit", "P001_missing_semicolon", "P001_reversed_comparison",
          "S001_undeclared_name", "S010_unit_dimension", "S021_pin_twice", "E103_conflict", "W104_no_hysteresis",
          "W113_no_adc_code", "P201_realtime_window_on_linux", "E116_contradictory_ensure", "E109_initial_state"]
MATRIX_AT = [(3800, "heater ON by warm (Linux rule)"), (15500, "fan refused by NEVER#1: '!' glyph"),
             (16700, "Linux killed at 15 s: link DOWN, 'F' glyph, heater OFF by FAILSAFE")]


def header(title: str, command: str) -> str:
    return f"# {title}\n# reproduce: {command}\n\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    opts = Options.make("unoq")
    write_text(OUT / "stage1_source.iot", load_source(PROGRAM).text)

    sink = Sink()
    toks = lex(load_source(PROGRAM), sink)
    write_text(OUT / "stage1_tokens.txt", header("Stage 1: token stream (line:col-endcol KIND text value)",
               f"py -3.13 -m iotc dump {PROGRAM} --stage tokens") + tokens_text(toks))
    front = parse_file(PROGRAM, Sink())
    write_text(OUT / "stage1_ast.txt", header("Stage 1: abstract syntax tree (one node per declaration, @line:col)",
               f"py -3.13 -m iotc dump {PROGRAM} --stage ast") + ast_dump(front.program))

    text, _ = check_output(PROGRAM, options=opts)
    write_text(OUT / "stage2_check_report.txt", header("Stage 2-3: iotc check (diagnostics, thresholds, placement, "
               "safety, resources)", f"py -3.13 -m iotc check {PROGRAM}") + text + "\n")
    parts = []
    for name in ERRORS:
        path = f"examples/errors/{name}.iot"
        src = load_source(path).text.rstrip("\n")
        msg, rc = check_output(path, options=opts, with_report=False)
        parts.append(f"==== {path} (exit {rc})\n---- source\n{src}\n---- compiler output\n{msg}\n")
    write_text(OUT / "stage2_error_messages.txt", header("Stage 2: selected error programs and the compiler's "
               "messages (one example per code lives in examples/errors/)",
               "py -3.13 -m iotc check examples/errors/<file>.iot") + "\n".join(parts))

    res = compile_file(PROGRAM, opts)
    write_text(OUT / "stage3_ir.txt", header("Stage 3: event IR after checks and lowering",
               f"py -3.13 -m iotc dump {PROGRAM} --stage ir") + ir_text(res.ir))

    target = OUT / "stage4_target"
    if target.exists():
        shutil.rmtree(target)
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / "greenhouse_board"
        fallback = load_scenario("scenarios/fallback_sweep.csv", res.ir)
        files = write(res.ir, app, "sim", fallback, res.report, True)
        keep = ["app.yaml", "sketch/sketch.ino", "sketch/iotc_gen.h", "sketch/sketch.yaml", "python/main.py",
                "assets/config.json"]
        for rel in keep:
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(app / rel, dst)
    listing = "\n".join(f"  {f}" for f in files)
    write_text(target / "README.md", "# Stage 4: generated UNO Q App (selected files)\n\n"
               "Reproduce:\n\n```\npy -3.13 -m iotc build examples/greenhouse_board.iot --input sim "
               "--demo-controls --scenario scenarios/fallback_sweep.csv -o build/b2_board\n```\n\n"
               "The full App folder contains:\n\n```\n" + listing + "\n```\n\n"
               "Copied here: the MCU sketch (`sketch/`), the Linux program (`python/main.py`), the App "
               "manifest and the dashboard configuration. The runtime package, page script and client "
               "libraries are omitted (they are byte copies of `iotc/runtime/` and `iotc/backend/web/`).\n")

    trace = load_trace("tests/traces/demo_script.csv", res.ir)
    it = Interpreter(res.ir, kill_at=15000, revive_at=20000, keep_frames=True)
    log = canonical(it.run(trace), it.table)
    write_text(OUT / "stage5_interpreter_log.txt", header("Stage 5: reference interpreter on the demo trace, Linux "
               "killed at 15 s and revived at 20 s", f"py -3.13 -m iotc run {PROGRAM} --trace "
               "tests/traces/demo_script.csv --kill-linux-at 15000 --revive-linux-at 20000") + "\n".join(log) + "\n")
    frames = dict(it.frames)
    parts = []
    for t, why in MATRIX_AT:
        parts.append(f"t = {t} ms: {why}\n{ascii_art(frames[t])}\n")
    write_text(OUT / "stage5_led_matrix.txt", header("Stage 5: LED-matrix frames (rows 0-3 sensor bars with "
               "threshold ticks, row 5 rules, row 7 actuators, columns 10-12 status glyph)",
               f"py -3.13 -m iotc matrix-preview {PROGRAM} --trace tests/traces/demo_script.csv "
               "--kill-linux-at 15000 --at <t>") + "\n".join(parts))

    scen = load_scenario("scenarios/demo_tour.csv", res.ir)
    p = predict(res.ir, scen)
    lines = [f"scenario rows {p['rows']} (x {p['sample_ms']} ms), aligned start every "
             f"{it.table['scenario_align_ms']} ms; iteration 2 repeats from then on: {p['steady']}", ""]
    for k, its in enumerate(p["iterations"], 1):
        lines.append(f"iteration {k}")
        lines += [f"  tick {e['tick']:4d}  ({e['tick'] * p['sample_ms'] / 1000:5.1f} s)  {e['body']}" for e in its]
    write_text(OUT / "stage5_conformance_prediction.txt", header("Stage 5: the compiler's predicted board log "
               "for the 50 s tour (conformance mode)", f"py -3.13 -m iotc build {PROGRAM} --input scenario "
               "--scenario scenarios/demo_tour.csv -o build/b4_conformance") + "\n".join(lines) + "\n")

    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
                       capture_output=True, text=True, cwd=ROOT)
    counts: dict[str, int] = {}
    for ln in r.stdout.splitlines():
        if "::" in ln:                                   # one line per test
            f = ln.split("::")[0]
            counts[f] = counts.get(f, 0) + 1
        elif ln.startswith("tests/") and ln.rsplit(": ", 1)[-1].isdigit():   # "file: count" (-qq)
            f, n = ln.rsplit(": ", 1)
            counts[f] = counts.get(f, 0) + int(n)
    total = sum(counts.values())
    rows = "\n".join(f"{n:5d}  {f}" for f, n in sorted(counts.items()))
    write_text(OUT / "stage5_test_inventory.txt", header("Stage 5: automated tests per file (collected)",
               "py -3.13 tasks.py test") + rows + f"\n{total:5d}  total\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({total} tests collected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
