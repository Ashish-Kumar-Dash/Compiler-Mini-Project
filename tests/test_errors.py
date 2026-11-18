"""One example file per diagnostic code (Architecture §0 rule 3, §13.1).

examples/errors/<CODE>_<what>.iot must report exactly the code <CODE> (possibly several times),
ignoring info-level notes, and its rendered text and JSON output must match the goldens.
Optional header comments:
    // expect: S011 W101      the exact set of non-info codes, when one finding implies another
    // options: --board uno   extra `iotc check` options
"""
import json
import shlex
from pathlib import Path

import pytest

from iotc.cli import build_parser, check_output, options_from
from iotc.diagnostics import SEVERITY

ROOT = Path(__file__).resolve().parents[1]
ERRORS = sorted((ROOT / "examples" / "errors").glob("*.iot"))
# Every stage that emits diagnostics exists; the prefix filter stays for future codes.
IMPLEMENTED_PREFIXES = ("L", "P0", "S", "P2", "W", "E", "I")
PENDING: set[str] = set()


def _code(path: Path) -> str:
    return path.stem.split("_", 1)[0]


def _header(path: Path, key: str) -> list[str]:
    for line in path.read_bytes().decode("utf-8", "replace").splitlines():
        if line.startswith(f"// {key}:"):
            return shlex.split(line.split(":", 1)[1])
    return []


@pytest.mark.parametrize("path", ERRORS, ids=lambda p: p.stem)
def test_error_example(golden, path, monkeypatch):
    code = _code(path)
    assert code in SEVERITY, f"{path.name}: unknown code {code}"
    expected = set(_header(path, "expect")) or {code}
    if not all(c.startswith(IMPLEMENTED_PREFIXES) for c in expected):
        pytest.skip(f"{sorted(expected)}: stage not implemented yet")
    monkeypatch.chdir(ROOT)
    rel = path.relative_to(ROOT).as_posix()
    opts = options_from(build_parser().parse_args(["check", rel, *_header(path, "options")]))
    text, rc = check_output(rel, options=opts)
    js, _ = check_output(rel, options=opts, as_json=True)
    found = {d["code"] for d in json.loads(js)["diagnostics"]
             if d["severity"] != "info" or d["code"] in expected}
    assert found == expected, f"{path.name}: expected {sorted(expected)}, got {sorted(found)}"
    assert rc == (1 if any(SEVERITY[c] == "error" for c in expected) else 0)
    golden(f"errors/{path.stem}.txt", text + "\n")
    golden(f"errors/{path.stem}.json", js + "\n")


def test_every_implemented_code_has_an_example():
    have = {_code(p) for p in ERRORS}
    wanted = {c for c in SEVERITY if c.startswith(IMPLEMENTED_PREFIXES)} - PENDING
    assert wanted <= have, f"missing examples for {sorted(wanted - have)}"
