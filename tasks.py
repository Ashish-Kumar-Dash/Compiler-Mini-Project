"""Project task runner (stdlib only). Usage: py -3.13 tasks.py <task> [args...]

Tasks
  test      run the full test suite (fast layers; skips g++/arduino-cli tests if tools are absent)
  golden    rewrite golden files from current output (review the diff afterwards!)
  fuzz      10,000-trace fuzz of the demo program + mutant self-checks
  diff      differential tests (host C++ vs interpreter; needs g++ via WSL or native)
  cosim     co-simulation of the host unoq sketch + generated main.py
  arduino   arduino-cli compile checks for every example (needs arduino-cli + cores)
  demo      build the board demo App into build/greenhouse_board
  virtual   run the demo App on a virtual board on this laptop (no board needed)
  probe     print how to deploy the Phase 0 probe App
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEMO = ROOT / "examples" / "greenhouse_board.iot"


def _need_python() -> None:
    if sys.version_info < (3, 11):
        print("iotc needs Python >= 3.11 (try: py -3.13 tasks.py ...)", file=sys.stderr)
        sys.exit(2)


def _run(*cmd: str, env: dict[str, str] | None = None) -> int:
    print("$", " ".join(cmd), flush=True)
    full_env = dict(os.environ)
    full_env.setdefault("PYTHONUTF8", "1")
    if env:
        full_env.update(env)
    return subprocess.call(list(cmd), cwd=ROOT, env=full_env)


def _iotc(*args: str) -> int:
    return _run(sys.executable, "-m", "iotc", *args)


def task_test(args: list[str]) -> int:
    return _run(sys.executable, "-m", "pytest", *args)


def task_golden(args: list[str]) -> int:
    return _run(sys.executable, "-m", "pytest", "--update-golden", *args)


def task_fuzz(args: list[str]) -> int:
    rc = _iotc("fuzz", str(DEMO), "-n", "10000", "--seed", "42", *args)
    for mutant in ("disable-monitor", "single-pass-never", "fs-over-ensure"):
        rc |= _iotc("fuzz", str(DEMO), "-n", "2000", "--seed", "42", "--mutant", mutant,
                    "--expect-violation")
    return rc


def task_diff(args: list[str]) -> int:
    return _run(sys.executable, "-m", "pytest", "-m", "diff", "-p", "no:cacheprovider", *args)


def task_cosim(args: list[str]) -> int:
    return _run(sys.executable, "-m", "pytest", "-m", "cosim", *args)


def task_arduino(args: list[str]) -> int:
    if shutil.which("arduino-cli") is None:
        print("arduino-cli not found: winget install ArduinoSA.CLI, then\n"
              "  arduino-cli core update-index\n"
              "  arduino-cli core install arduino:zephyr arduino:avr", file=sys.stderr)
        return 2
    return _run(sys.executable, "-m", "pytest", "-m", "arduino", *args)


def task_demo(args: list[str]) -> int:
    return _iotc("build", str(DEMO), "--board", "unoq", "--input", "sim", "--demo-controls",
                 "--scenario", str(ROOT / "scenarios" / "fallback_sweep.csv"),
                 "-o", str(ROOT / "build" / "greenhouse_board"), *args)


def task_virtual(args: list[str]) -> int:
    return _iotc("dashboard", str(DEMO), "--scenario", str(ROOT / "scenarios" / "fallback_sweep.csv"), *args)


def task_probe(args: list[str]) -> int:
    print("Phase 0 probe: see docs/phase0_checklist.md\n"
          "  powershell -ExecutionPolicy Bypass -File scripts\\deploy.ps1 hardware\\phase0\\probe")
    return 0


TASKS = {name[5:]: fn for name, fn in globals().items() if name.startswith("task_")}


def main(argv: list[str]) -> int:
    _need_python()
    if not argv or argv[0] not in TASKS:
        print(__doc__)
        return 2
    return TASKS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
