"""Differential test (Architecture §13.3): the generated C++ sketch, compiled with WSL g++ against
the host shim, must reproduce the reference interpreter exactly:
  * the canonical event log (LK, EV, NT, SY),
  * the actuator pin levels after every row (incl. active-low LEDs),
  * every LED-matrix frame (mode A),
and mode B (10 passes per row) must give the same event log as mode A.
IOTC_DIFF_TRACES sets random traces per program (default 25; `py tasks.py diff` uses 200).
"""
from __future__ import annotations

import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from iotc.backend.mcu_cpp import emit
from iotc.interp import Interpreter, canonical, parse_trace
from iotc.ir import AvgTest, CodeRange
from iotc.options import Options
from iotc.pipeline import compile_file

pytestmark = pytest.mark.diff
ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "tests" / "host_shim"
N_TRACES = int(os.environ.get("IOTC_DIFF_TRACES", "25"))
RESULTS = ("applied", "refused", "forced", "failsafe")


def _wsl_gxx() -> bool:
    if shutil.which("wsl") is None:
        return False
    try:
        r = subprocess.run(["wsl", "-e", "sh", "-c", "command -v g++"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


HAVE_GXX = _wsl_gxx()


def wsl(script: str, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["wsl", "-e", "sh", "-c", script, "sh", *args], capture_output=True,
                          timeout=timeout)


# -- programs ----------------------------------------------------------------------------------

def _programs():
    progs = []
    for p in sorted((ROOT / "examples").glob("*.iot")):
        if p.stem == "greenhouse_wokwi":
            progs.append((p.stem, Options.make("uno")))
        elif p.stem == "place_windows_mcu":
            progs.append((p.stem, Options.make("unoq", place_windows="mcu")))
        else:
            progs.append((p.stem, Options.make("unoq")))
    progs.append(("greenhouse_board", Options.make("unoq", "mcu-only")))
    return progs


PROGRAMS = _programs()


def _tag(name: str, o: Options) -> str:
    return f"{name}.{o.board}.{o.target}.{o.place_windows}"


# -- traces ------------------------------------------------------------------------------------

def _threshold_codes(ir) -> dict[int, list[int]]:
    out: dict[int, set[int]] = {s.id: set() for s in ir.sensors}
    for t in [t for r in ir.rules for t in r.tests] + [t for e in ir.ensures for t in e.tests]:
        sid = ir.sensor(t.var.sensor).id
        if isinstance(t, CodeRange) and not t.never:
            out[sid].update(v for v in (t.lo, t.hi) if v is not None)
        elif isinstance(t, AvgTest):
            out[sid].add(t.p // t.q)
    return {k: sorted(v) for k, v in out.items()}


def random_trace(ir, seed: int) -> tuple[str, dict]:
    """Random walk that keeps returning to threshold codes (k-1, k, k+1), with plateaus and spikes;
    for unoq, random Linux kill / revive / deafness."""
    r = random.Random(seed)
    th = _threshold_codes(ir)
    S = ir.sample_ms
    rows = r.randint(40, 260)
    cur = []
    for s in ir.sensors:
        if s.kind == "DIGITAL":
            cur.append(r.randint(0, 1))
        else:
            cur.append(r.choice(th[s.id]) if th[s.id] else r.randint(0, s.adc_max))
    lines = ["t_ms," + ",".join(f"{s.name}:raw" if s.kind == "ANALOG" else s.name for s in ir.sensors)]
    for i in range(rows):
        for j, s in enumerate(ir.sensors):
            if s.kind == "DIGITAL":
                if r.random() < 0.08:
                    cur[j] = 1 - cur[j]
                continue
            x = r.random()
            if x < 0.15 and th[s.id]:
                cur[j] = r.choice(th[s.id]) + r.choice((-1, 0, 1))
            elif x < 0.20:
                cur[j] = r.randint(0, s.adc_max)
            elif x < 0.85:
                cur[j] += r.randint(-12, 12)
            cur[j] = max(0, min(s.adc_max, cur[j]))
        lines.append(f"{i * S}," + ",".join(str(c) for c in cur))
    kw: dict = {}
    if ir.target == "unoq":
        x = r.random()
        if x < 0.35:
            kw["kill_at"] = r.randrange(0, rows) * S
            if r.random() < 0.6:
                kw["revive_at"] = kw["kill_at"] + r.randrange(1, 60) * S
        elif x < 0.45:
            kw["deaf_at"] = r.randrange(0, rows) * S
    return "\n".join(lines) + "\n", kw


# -- running both sides ------------------------------------------------------------------------

def driver_input(trace, interp: Interpreter, passes: int) -> str:
    hb = dict(interp.heartbeats)
    out = [f"M {passes}"]
    for t, codes in trace.rows:
        if t in hb:
            out.append("H {} {} {}".format(*hb[t]))
        out.append(f"R {t} " + " ".join(str(c) for c in codes))
    return "\n".join(out) + "\n"


def parse_output(text: str, table: dict, ir) -> tuple[list[tuple], list[list[int]], list[list[int]]]:
    events, frames, levels = [], [], []
    frame = [0] * 104
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        k = parts[0]
        if k == "E":
            t, a, s, src, res, det = map(int, parts[1:])
            if src == 0xFF:
                name = "FAILSAFE"
            elif src >= 0xF0:
                name = f"ENSURE#{src - 0xF0 + 1}"
            else:
                name = table["rules"][src]["name"]
            events.append(("EV", t, a, s, name, RESULTS[res], "-" if det == 0xFF else f"never#{det + 1}"))
        elif k == "L":
            events.append(("LK", int(parts[1]), parts[2] == "1"))
        elif k == "N":
            events.append(("NT", int(parts[1]), int(parts[2])))
        elif k == "S":
            events.append(("SY", int(parts[1]), int(parts[2])))
        elif k == "F":
            frame = [int(ch) for ch in parts[1]]
        elif k == "O":
            levels.append([int(x) for x in parts[1:]])
        elif k == "R":
            frames.append(list(frame))
    return events, frames, levels


@pytest.fixture(scope="module")
def build_dir(tmp_path_factory):
    if not HAVE_GXX:
        pytest.skip("WSL g++ not available")
    d = tmp_path_factory.mktemp("diff")
    for name, opts in PROGRAMS:
        res = compile_file(ROOT / "examples" / f"{name}.iot", opts)
        assert res.ok, name
        pd = d / _tag(name, opts)
        pd.mkdir()
        for fname, text in emit(res.ir).items():
            (pd / fname).write_text(text, encoding="utf-8", newline="\n")
        for f in SHIM.iterdir():
            shutil.copy(f, pd / f.name)
    r = wsl('cd "$(wslpath -a "$1")" && for p in */; do (cd "$p" && g++ -std=gnu++17 -O0 -Wall -Wextra -Werror -I. '
            'main.cpp -o sim) || exit 1; done', str(d))
    assert r.returncode == 0, r.stderr.decode()[-4000:]
    return d


def _cases(name, opts, ir):
    cases = []
    if name == "greenhouse_board":
        demo = (ROOT / "tests" / "traces" / "demo_script.csv").read_text()
        cases.append(("demo", demo, {"kill_at": 15000, "revive_at": 20000} if opts.target == "unoq" else {}))
        cases.append(("hyst", (ROOT / "tests" / "traces" / "hysteresis.csv").read_text(), {}))
    for i in range(N_TRACES):
        text, kw = random_trace(ir, hash((name, opts.target, i)) & 0xFFFFFFFF)
        cases.append((f"rand{i}", text, kw))
    return cases


@pytest.mark.parametrize("name,opts", PROGRAMS, ids=[_tag(n, o) for n, o in PROGRAMS])
def test_cpp_matches_interpreter(build_dir, name, opts):
    ir = compile_file(ROOT / "examples" / f"{name}.iot", opts).ir
    pd = build_dir / _tag(name, opts)
    jobs = []
    for cid, text, kw in _cases(name, opts, ir):
        trace = parse_trace(text, ir)
        it = Interpreter(ir, keep_frames=True, **kw)
        events = it.run(trace)
        expected = canonical(events, it.table)
        linux_nt = [e for e in events if e[0] == "NT"] if ir.target == "unoq" else []
        for passes in (1, 10) if cid in ("demo", "rand0", "rand1") else (1,):
            inp = pd / f"{cid}.{passes}.in"
            inp.write_text(driver_input(trace, it, passes), encoding="utf-8", newline="\n")
            jobs.append((cid, passes, inp, expected, linux_nt, it))
    r = wsl('cd "$(wslpath -a "$1")" && for f in *.in; do ./sim < "$f" > "${f%.in}.out" || exit 1; done', str(pd))
    assert r.returncode == 0, r.stderr.decode()[-2000:]
    for cid, passes, inp, expected, linux_nt, it in jobs:
        out = inp.with_suffix(".out").read_text(encoding="utf-8")
        events, frames, levels = parse_output(out, it.table, ir)
        got = canonical(events + linux_nt, it.table)
        assert got == expected, f"{name}/{cid} passes={passes}: first difference at " \
            f"{next((i, g, e) for i, (g, e) in enumerate(zip(got + ['<end>'] * 9, expected + ['<end>'] * 9)) if g != e)}"
        assert len(levels) == len(it.states)
        for (t, cur), lv in zip(it.states, levels):
            want = [int(bool(c) != bool(a.active_low)) for c, a in zip(cur, ir.actuators)]
            assert lv == want, f"{name}/{cid} pin levels at t={t}: {lv} != {want}"
        if passes == 1:
            for (t, px), fr in zip(it.frames, frames):
                assert fr == px, f"{name}/{cid} matrix frame differs at t={t}"


def _run_one(build_dir: Path, name: str, opts: Options, text: str, kw: dict, tag: str = "single"):
    """Run one trace through an already-built program; return (ir, interpreter, raw output)."""
    ir = compile_file(ROOT / "examples" / f"{name}.iot", opts).ir
    pd = build_dir / _tag(name, opts)
    trace = parse_trace(text, ir)
    it = Interpreter(ir, keep_frames=True, **kw)
    it.events = it.run(trace)
    inp = pd / f"{tag}.in"
    inp.write_text(driver_input(trace, it, 1), encoding="utf-8", newline="\n")
    r = wsl('cd "$(wslpath -a "$1")" && ./sim < "$2" > "$3"', str(pd), inp.name, inp.with_suffix(".out").name)
    assert r.returncode == 0, r.stderr.decode()
    return ir, it, inp.with_suffix(".out").read_text(encoding="utf-8")


def test_bridge_messages_use_contract_arity(build_dir):
    """Every notify the unoq sketch sends has the arity bridge.py declares (§11.5)."""
    from iotc.backend import bridge
    demo = (ROOT / "tests" / "traces" / "demo_script.csv").read_text()
    ir, _, out = _run_one(build_dir, "greenhouse_board", Options.make("unoq"), demo,
                          {"kill_at": 15000, "revive_at": 20000}, "arity")
    seen = set()
    for line in out.splitlines():
        if not line.startswith("B "):
            continue
        _, name, *args = line.split()
        want = 1 + len(ir.sensors) if name == "sample" else len(bridge.by_name(name).args)
        assert len(args) == want, line
        seen.add(name)
    assert {"sample", "event", "link", "stats", "hb_ack"} <= seen


def test_uno_serial_log_matches_canonical_lines(build_dir):
    """On the classic Uno the sketch prints the canonical EV/NT lines itself (Wokwi backup)."""
    demo = (ROOT / "tests" / "traces" / "demo_script.csv").read_text()
    ir, it, out = _run_one(build_dir, "greenhouse_wokwi", Options.make("uno"), demo, {}, "serial")
    serial = [line[2:] for line in out.splitlines() if line.startswith("P ")]
    assert serial and serial == canonical(it.events, it.table)


GOOD = ("if (reqMin[a][OFF] != NONE8) nxt[a] = OFF;\n"
        "    else if (reqMin[a][ON] != NONE8) nxt[a] = ON;")
BAD = ("if (reqMin[a][ON] != NONE8) nxt[a] = ON;\n"
       "    else if (reqMin[a][OFF] != NONE8) nxt[a] = OFF;")


def test_differential_detects_a_core_bug(build_dir, tmp_path, monkeypatch):
    """Mutation check of the harness: break OFF-wins in the C++ core; the logs must differ."""
    from iotc.backend import cpp_core, mcu_cpp
    assert GOOD in cpp_core.CORE_PASS
    monkeypatch.setattr(mcu_cpp, "CORE_PASS", cpp_core.CORE_PASS.replace(GOOD, BAD))
    opts = Options.make("unoq", "mcu-only")
    # warm (heater ON on avg(temp) < 18) and hot (heater OFF on temp > 40) collide on a jump
    ir = compile_file(ROOT / "examples" / "errors" / "W103_window_conflict.iot", opts).ir
    for fname, text in mcu_cpp.emit(ir).items():
        (tmp_path / fname).write_text(text, encoding="utf-8", newline="\n")
    for f in SHIM.iterdir():
        shutil.copy(f, tmp_path / f.name)
    rows = "".join(f"{i * 100},{0 if i < 20 else 45}\n" for i in range(25))
    trace = parse_trace("t_ms,temp\n" + rows, ir)
    it = Interpreter(ir)
    expected = canonical(it.run(trace), it.table)
    (tmp_path / "t.in").write_text(driver_input(trace, it, 1), encoding="utf-8", newline="\n")
    r = wsl('cd "$(wslpath -a "$1")" && g++ -std=gnu++17 -O0 -I. main.cpp -o sim && ./sim < t.in > t.out',
            str(tmp_path))
    assert r.returncode == 0, r.stderr.decode()
    events, _, _ = parse_output((tmp_path / "t.out").read_text(), it.table, ir)
    assert canonical(events, it.table) != expected


def test_scenario_mode_matches_interpreter(build_dir, tmp_path):
    """--input scenario on the MCU alone (board step B1) plays the compiled-in rows exactly like
    the interpreter run of the same rows from a scenario start (§9.9)."""
    from iotc.interp import load_scenario
    opts = Options.make("unoq", "mcu-only")
    ir = compile_file(ROOT / "examples" / "greenhouse_board.iot", opts).ir
    rows = load_scenario(ROOT / "scenarios" / "demo_tour.csv", ir)
    for fname, text in emit(ir, "scenario", rows).items():
        (tmp_path / fname).write_text(text, encoding="utf-8", newline="\n")
    for f in SHIM.iterdir():
        shutil.copy(f, tmp_path / f.name)
    trace = parse_trace("t_ms," + ",".join(f"{s.name}:raw" for s in ir.sensors) + "\n"
                        + "".join(f"{i * ir.sample_ms}," + ",".join(map(str, r)) + "\n" for i, r in enumerate(rows)), ir)
    it = Interpreter(ir, link_up_at_start=True, keep_frames=True)
    expected = canonical(it.run(trace), it.table)
    (tmp_path / "t.in").write_text(driver_input(trace, it, 1), encoding="utf-8", newline="\n")
    r = wsl('cd "$(wslpath -a "$1")" && g++ -std=gnu++17 -O0 -Wall -Wextra -Werror -I. main.cpp -o sim '
            '&& ./sim < t.in > t.out', str(tmp_path))
    assert r.returncode == 0, r.stderr.decode()
    events, frames, _ = parse_output((tmp_path / "t.out").read_text(), it.table, ir)
    got = [line for line in canonical(events, it.table) if not line.startswith("SY,") or line.endswith(",1")]
    assert got == expected
    assert len(expected) > 8                       # the tour exercises the rules
    for (t, px), fr in zip(it.frames, frames):
        assert fr == px, f"frame differs at t={t}"
