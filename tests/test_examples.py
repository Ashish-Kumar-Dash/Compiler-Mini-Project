"""Every valid example compiles without errors; its full `iotc check` report is a golden
(Architecture §8, §13.1)."""
from pathlib import Path

import pytest

from iotc.cli import check_output
from iotc.options import Options

ROOT = Path(__file__).resolve().parents[1]
CASES = [(p.stem, Options.make("unoq")) for p in sorted((ROOT / "examples").glob("*.iot"))
         if p.stem not in ("place_windows_mcu", "greenhouse_wokwi")]
CASES += [
    ("greenhouse_board", Options.make("unoq", "mcu-only")),
    ("place_windows_mcu", Options.make("unoq", place_windows="mcu")),
    ("greenhouse_wokwi", Options.make("uno")),
]


def _tag(name: str, opts: Options) -> str:
    extra = "" if (opts.board, opts.target, opts.place_windows) == ("unoq", "unoq", "linux") else \
        f".{opts.board}.{opts.target}.{opts.place_windows}"
    return name + extra


@pytest.mark.parametrize("name,opts", CASES, ids=[_tag(n, o) for n, o in CASES])
def test_example_report(golden, monkeypatch, name, opts):
    monkeypatch.chdir(ROOT)
    text, rc = check_output(f"examples/{name}.iot", options=opts)
    assert rc == 0, text
    golden(f"check/{_tag(name, opts)}.txt", text + "\n")
    js, _ = check_output(f"examples/{name}.iot", options=opts, as_json=True)
    golden(f"check/{_tag(name, opts)}.json", js + "\n")


def test_place_windows_policy_changes_placement():
    text, rc = check_output(str(ROOT / "examples" / "place_windows_mcu.iot"))
    assert rc == 1 and "P201" in text


def test_at_least_twelve_valid_examples():
    assert len([p for p in (ROOT / "examples").glob("*.iot")]) >= 12
