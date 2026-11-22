"""Linux back end: emits python/main.py and the python/iotc_runtime/ package (Architecture §11.6).

The Linux rules run on the shared, table-driven runtime (iotc/runtime, byte-copied to the board),
so the board executes exactly the code the interpreter and the differential test exercise; the
generated main.py holds the program table, the Bridge/WebUI wiring and the page state.
"""
from __future__ import annotations

import pprint
from pathlib import Path

from .. import __version__
from ..ir import IRProgram
from ..table import build_table
from . import bridge
from .main_template import TEMPLATE

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtime"
RUNTIME_FILES = ("__init__.py", "window.py", "engine.py", "matrix.py")


def sim_init(ir: IRProgram, scenario: list[list[int]] | None) -> list[int]:
    """Initial sensor codes, identical to the sketch's SENS_INIT (mcu_cpp.py)."""
    if scenario:
        return list(scenario[0])
    return [s.adc_max // 2 if s.kind == "ANALOG" else 0 for s in ir.sensors]


def emit_main(ir: IRProgram, input_mode: str, scenario: list[list[int]] | None,
              demo_controls: bool) -> str:
    table = build_table(ir)
    table["sim_init"] = sim_init(ir, scenario)
    names = {m.name: m.name for m in bridge.MESSAGES}
    text = TEMPLATE
    for key, value in (
        ("@@VERSION@@", __version__),
        ("@@PROGRAM@@", ir.name.replace('"', "_")),
        ("@@SHA@@", ir.source_sha[:16]),
        ("@@TABLE@@", pprint.pformat(table, width=100, sort_dicts=False)),
        ("@@INPUT@@", repr(input_mode)),
        ("@@DEMO@@", repr(bool(demo_controls))),
        ("@@NAMES@@", repr(names)),
    ):
        text = text.replace(key, value)
    assert "@@" not in text
    return text


def runtime_files() -> dict[str, bytes]:
    """python/iotc_runtime/<file>: byte copies of iotc/runtime (stdlib only)."""
    return {f"python/iotc_runtime/{name}": (RUNTIME_DIR / name).read_bytes() for name in RUNTIME_FILES}
