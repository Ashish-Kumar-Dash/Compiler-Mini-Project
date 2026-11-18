"""CLI contract: exit codes, JSON shape, ASCII fallback, rendering (Architecture §5.5)."""
import json
import os
import subprocess
import sys
from pathlib import Path

from iotc import cli
from iotc.diagnostics import render
from iotc.frontend import parse_text

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args, env_extra=None):
    env = dict(os.environ, PYTHONUTF8="1")
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([sys.executable, "-m", "iotc", *args], cwd=ROOT, capture_output=True,
                       env=env)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def test_exit_codes():
    assert run_cli("check", "examples/greenhouse_board.iot")[0] == 0
    assert run_cli("check", "examples/errors/L001_unknown_char.iot")[0] == 1
    assert run_cli("check", "examples/does_not_exist.iot")[0] == 2
    assert run_cli("frobnicate")[0] == 2


def test_internal_error_exit_3(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(cli, "check_output", boom)
    assert cli.main(["check", str(ROOT / "examples" / "greenhouse_board.iot")]) == 3
    assert "internal compiler error" in capsys.readouterr().err


def test_json_shape():
    rc, out, _ = run_cli("check", "--json", "examples/errors/P001_missing_colon.iot")
    data = json.loads(out)
    assert data["version"] == 1 and rc == 1
    (d,) = data["diagnostics"]
    assert set(d) == {"code", "severity", "message", "line", "col", "end_line", "end_col", "hint",
                      "related", "data"}
    assert d["end_col"] > d["col"]


def test_ascii_fallback_when_stdout_is_cp1252():
    rc, out, _ = run_cli("check", "examples/errors/L001_confusable_minus.iot",
                         env_extra={"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"})
    assert rc == 1 and "error[L001]" in out


def test_render_tab_and_wide_characters(golden):
    res = parse_text("\tRULE r: WHEN t 温 > 5 THEN f ON;\n", name="wide.iot")
    golden("diagnostics/tab_wide.txt", render(res.sink.items, res.source) + "\n")


def test_render_is_deterministic():
    res = parse_text("RULE r: WHEN t # 5 THEN f ON;\nWHN\n")
    assert render(res.sink.items, res.source) == render(list(reversed(res.sink.items)), res.source)
