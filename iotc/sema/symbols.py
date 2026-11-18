"""Symbol table and name resolution: S001 (with did-you-mean), S002, S003 (Architecture §5.4).

Sensors, actuators and rules share one namespace."""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import ast as A
from ..diagnostics import Related, Sink
from ..lexer import did_you_mean


def suggest_name(word: str, candidates: list[str]) -> str | None:
    """did-you-mean for names (Architecture §5.4): edit distance first, then a prefix relation
    (one name a prefix of the other, >= 3 chars) or a shared prefix of >= 4 characters."""
    guess = did_you_mean(word, candidates)
    if guess is not None:
        return guess
    for c in candidates:
        short, long_ = sorted((word, c), key=len)
        if len(short) >= 3 and long_.startswith(short):
            return c
    for c in candidates:
        n = 0
        while n < min(len(word), len(c)) and word[n] == c[n]:
            n += 1
        if n >= 4:
            return c
    return None


KIND_WORD = {"sensor": "a sensor", "actuator": "an actuator", "rule": "a rule"}


@dataclass
class Symbols:
    sensors: dict[str, A.SensorDecl] = field(default_factory=dict)
    actuators: dict[str, A.ActuatorDecl] = field(default_factory=dict)
    rules: dict[str, A.Rule] = field(default_factory=dict)
    kind_of: dict[str, str] = field(default_factory=dict)
    span_of: dict[str, object] = field(default_factory=dict)


def build(program: A.Program, sink: Sink) -> Symbols:
    st = Symbols()
    decls: list[tuple[str, A.Name, object]] = (
        [("sensor", s.name, s) for s in program.sensors]
        + [("actuator", a.name, a) for a in program.actuators]
        + [("rule", r.name, r) for r in program.rules]
    )
    decls.sort(key=lambda d: (d[1].span.line, d[1].span.col))
    for kind, name, node in decls:
        if name.text in st.kind_of:
            first_kind = st.kind_of[name.text]
            first = st.span_of[name.text]
            sink.add("S002", f"'{name.text}' is already declared as {KIND_WORD[first_kind]}",
                     name.span, "sensors, actuators and rules share one namespace; rename one",
                     [Related(first, f"first declared here")])
            continue
        st.kind_of[name.text] = kind
        st.span_of[name.text] = name.span
        getattr(st, kind + "s")[name.text] = node
    return st


def resolve(st: Symbols, name: A.Name, want: str, sink: Sink) -> bool:
    """True if `name` is declared with kind `want`; otherwise report S001 or S003."""
    have = st.kind_of.get(name.text)
    if have == want:
        return True
    if have is None:
        candidates = list(getattr(st, want + "s").keys())
        guess = suggest_name(name.text, candidates)
        sink.add("S001", f"unknown {want} '{name.text}'", name.span,
                 f"did you mean '{guess}'?" if guess else f"declare it with {want.upper()} {name.text} ...")
        return False
    sink.add("S003", f"'{name.text}' is {KIND_WORD[have]}, not {KIND_WORD[want]}", name.span,
             None, [Related(st.span_of[name.text], f"'{name.text}' is declared here")])
    return False
