"""The compilation pipeline (Architecture §5–§8): parse -> sema -> lower -> analyses -> report."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .analysis import intervals, resources, safety
from .analysis.common import Ctx
from .analysis.report import Report, placement_rows, threshold_rows
from .diagnostics import Sink
from .frontend import FrontResult, parse_source
from .ir import IRProgram
from .lower import lower
from .options import Options
from .sema.checker import Checked, check
from .source import Source, decode_source, load_source


@dataclass
class Compiled:
    front: FrontResult
    checked: Checked | None
    ir: IRProgram | None
    options: Options
    report: Report | None = None

    @property
    def ok(self) -> bool:
        """True if the program can be built (no errors; warnings allowed unless --werror)."""
        return self.ir is not None and not self.sink.has_errors()

    @property
    def sink(self) -> Sink:
        return self.front.sink

    @property
    def source(self) -> Source:
        return self.front.source


def compile_source(source: Source, options: Options, sink: Sink | None = None) -> Compiled:
    front = parse_source(source, sink)
    sink = front.sink
    if front.program is None:
        return Compiled(front, None, None, options)
    checked = check(front.program, options, sink)
    if sink.error_count:
        return Compiled(front, checked, None, options)
    ir = lower(checked, sink, source.name, source.sha256)
    if sink.error_count:
        return Compiled(front, checked, None, options)
    ctx = Ctx(ir, checked, options, sink)
    intervals.run(ctx)
    safety_lines = safety.run(ctx)
    res = resources.run(ctx)
    report = Report(threshold_rows(ctx), placement_rows(ir), safety_lines, res, options.target, options.board)
    return Compiled(front, checked, ir, options, report)


def compile_file(path: str | Path, options: Options, sink: Sink | None = None) -> Compiled:
    return compile_source(load_source(path), options, sink)


def compile_text(text: str, options: Options | None = None, name: str = "input.iot") -> Compiled:
    return compile_source(decode_source(name, text.encode("utf-8")), options or Options())
