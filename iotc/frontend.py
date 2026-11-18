"""Front-end driver: source -> tokens -> AST (Architecture §5). Sema and analyses hook in later."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import ast as A
from .diagnostics import Sink
from .lexer import lex
from .parser import parse
from .source import Source, decode_source, load_source
from .tokens import Token


@dataclass
class FrontResult:
    source: Source
    sink: Sink
    tokens: list[Token]
    program: A.Program | None        # None if any L or P error (compilation stops, §5.2)


def parse_source(source: Source, sink: Sink | None = None) -> FrontResult:
    sink = sink or Sink()
    tokens = lex(source, sink)
    program = parse(tokens, sink)
    stop = any(d.severity == "error" and d.code[0] in "LP" and d.code < "P2" for d in sink.items) \
        or sink.suppressed > 0
    return FrontResult(source, sink, tokens, None if stop else program)


def parse_file(path: str | Path, sink: Sink | None = None) -> FrontResult:
    return parse_source(load_source(path), sink)


def parse_text(text: str, name: str = "input.iot", sink: Sink | None = None) -> FrontResult:
    return parse_source(decode_source(name, text.encode("utf-8")), sink)
