"""One example file per diagnostic code (Architecture §0 rule 3, §13.1).

examples/errors/<CODE>_<what>.iot must report exactly the diagnostic code <CODE> (possibly several
times), and its rendered text and JSON output must match the goldens.
"""
import json
from pathlib import Path

import pytest

from iotc.cli import check_output
from iotc.diagnostics import SEVERITY

ROOT = Path(__file__).resolve().parents[1]
ERRORS = sorted((ROOT / "examples" / "errors").glob("*.iot"))
# Codes whose stage exists today; files for later stages are skipped until that stage lands.
IMPLEMENTED_PREFIXES = ("L", "P0")


def _code(path: Path) -> str:
    return path.stem.split("_", 1)[0]


@pytest.mark.parametrize("path", ERRORS, ids=lambda p: p.stem)
def test_error_example(golden, path, monkeypatch):
    code = _code(path)
    assert code in SEVERITY, f"{path.name}: unknown code {code}"
    if not code.startswith(IMPLEMENTED_PREFIXES):
        pytest.skip(f"{code} stage not implemented yet")
    monkeypatch.chdir(ROOT)
    rel = path.relative_to(ROOT).as_posix()
    text, rc = check_output(rel)
    js, _ = check_output(rel, as_json=True)
    found = {d["code"] for d in json.loads(js)["diagnostics"]}
    assert found == {code}, f"{path.name}: expected only {code}, got {sorted(found)}"
    assert rc == (1 if SEVERITY[code] == "error" else 0)
    golden(f"errors/{path.stem}.txt", text + "\n")
    golden(f"errors/{path.stem}.json", js + "\n")


def test_every_lexer_and_parser_code_has_an_example():
    have = {_code(p) for p in ERRORS}
    wanted = {c for c in SEVERITY if c.startswith(IMPLEMENTED_PREFIXES)}
    assert wanted <= have, f"missing examples for {sorted(wanted - have)}"
