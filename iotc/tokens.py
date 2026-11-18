"""Token kinds and the fixed vocabulary of the language (Architecture §4.2)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .source import Span


class K(Enum):
    KEYWORD = "KEYWORD"
    AGG = "AGG"
    IDENT = "IDENT"
    NUMBER = "NUMBER"
    DURATION = "DURATION"
    UNIT = "UNIT"
    PIN = "PIN"
    STRING = "STRING"
    OP = "OP"
    PUNCT = "PUNCT"
    ERROR = "ERROR"
    EOF = "EOF"


@dataclass(frozen=True)
class Token:
    kind: K
    text: str          # exact source text
    value: Any         # NUMBER: source text (exact); DURATION: int ms; UNIT/PIN: canonical name
    span: Span

    def is_kw(self, *words: str) -> bool:
        return self.kind is K.KEYWORD and self.text in words

    def is_op(self, *ops: str) -> bool:
        return self.kind in (K.OP, K.PUNCT) and self.text in ops


KEYWORDS = (
    "SENSOR", "ACTUATOR", "AT", "ANALOG", "DIGITAL", "UNIT", "RANGE", "RULE", "REALTIME",
    "WHEN", "AND", "FOR", "THEN", "ON", "OFF", "NOTIFY", "SAFETY", "NEVER", "ENSURE", "FAILSAFE",
)
KEYWORD_SET = frozenset(KEYWORDS)
DECL_START = frozenset({"SENSOR", "ACTUATOR", "RULE", "SAFETY", "FAILSAFE"})
AGGREGATES = frozenset({"avg", "min", "max"})
UNITS = ("C", "F", "PCT", "LUX", "CM")
UNIT_SET = frozenset(UNITS)
DURATION_MS = {"ms": 1, "s": 1000, "min": 60_000, "h": 3_600_000}
RELOPS = (">", "<", ">=", "<=", "==")
PUNCTUATION = frozenset(":;(),")

PIN_RE = re.compile(r"^(?:A[0-9]+|D[0-9]+|LED[0-9]+[RGB]?)$")
PIN_EXAMPLES = tuple([f"A{i}" for i in range(6)] + [f"D{i}" for i in range(14)]
                     + ["LED3", "LED4", "LED3R", "LED3G", "LED3B", "LED4R", "LED4G", "LED4B"])


def canonical_pin(name: str) -> str:
    """LED3 is an alias of LED3R (red channel); other pins are unchanged."""
    if re.fullmatch(r"LED[0-9]+", name):
        return name + "R"
    return name


def describe(tok: Token) -> str:
    """Human description of a token for 'expected X, found Y' messages."""
    k = tok.kind
    if k is K.EOF:
        return "end of file"
    if k is K.IDENT:
        return f"identifier `{tok.text}`"
    if k is K.NUMBER:
        return f"number `{tok.text}`"
    if k is K.DURATION:
        return f"duration `{tok.text}`"
    if k is K.UNIT:
        return f"unit `{tok.text}`"
    if k is K.PIN:
        return f"pin `{tok.text}`"
    if k is K.STRING:
        return "a string"
    if k is K.AGG:
        return f"aggregate `{tok.text}`"
    return f"`{tok.text}`"
