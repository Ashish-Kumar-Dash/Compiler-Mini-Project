"""Diagnostics: catalog, collection, text rendering and JSON (Architecture §5.5, §6)."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

from .source import Source, Span

# code -> severity. The catalog is NORMATIVE (Architecture §6); every code needs a test.
SEVERITY: dict[str, str] = {
    "L000": "error", "L001": "error", "L002": "error", "L003": "error", "L004": "error",
    "L005": "error", "L006": "error", "L007": "error", "L008": "error",
    "P001": "error",
    "S001": "error", "S002": "error", "S003": "error", "S010": "error", "S011": "warning",
    "S012": "error", "S013": "error", "S020": "error", "S021": "error", "S022": "error",
    "S023": "warning", "S030": "error", "S031": "warning", "S032": "error", "S033": "warning",
    "S034": "error", "S035": "error", "S036": "error", "S037": "error", "S040": "error",
    "S041": "error", "S042": "warning", "S043": "warning", "S050": "warning", "S051": "warning",
    "W101": "warning", "W102": "warning", "E103": "error", "W103": "warning", "W104": "warning",
    "I105": "info", "W106": "warning", "W107": "warning", "E108": "error", "E109": "error",
    "W110": "warning", "W111": "warning", "W112": "warning", "W113": "warning", "E116": "error",
    "W117": "warning", "I118": "info", "W119": "warning", "W120": "warning", "W121": "warning",
    "I122": "info",
    "P201": "error", "P202": "error", "P203": "error", "P204": "error", "P205": "warning",
}

MAX_ERRORS = 50

ASCII_MAP = {"✓": "[ok]", "⚠": "[!]", "∈": "in", "−": "-", "→": "->", "∅": "{}", "⇔": "<=>",
             "≥": ">=", "≤": "<=", "…": "...", "—": "-", "×": "x", "·": "*"}


@dataclass(frozen=True)
class Related:
    span: Span
    message: str


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    span: Span | None
    hint: str | None = None
    related: tuple[Related, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    severity: str = ""

    def __post_init__(self) -> None:
        if self.code not in SEVERITY:
            raise ValueError(f"unknown diagnostic code {self.code}")
        if not self.severity:
            object.__setattr__(self, "severity", SEVERITY[self.code])

    def sort_key(self) -> tuple:
        s = self.span
        return (s.line if s else 0, s.col if s else 0, self.code, self.message)


class Sink:
    """Collects diagnostics; errors beyond MAX_ERRORS are counted, not stored."""

    def __init__(self, werror: bool = False) -> None:
        self.items: list[Diagnostic] = []
        self.suppressed = 0
        self.werror = werror

    def add(self, code: str, message: str, span: Span | None, hint: str | None = None,
            related: Iterable[Related] = (), **data: Any) -> Diagnostic:
        d = Diagnostic(code, message, span, hint, tuple(related), dict(data))
        if d.severity == "error" and self.error_count >= MAX_ERRORS:
            self.suppressed += 1
        else:
            self.items.append(d)
        return d

    @property
    def error_count(self) -> int:
        return sum(1 for d in self.items if d.severity == "error")

    def has_errors(self) -> bool:
        if self.werror:
            return any(d.severity in ("error", "warning") for d in self.items) or self.suppressed > 0
        return self.error_count > 0 or self.suppressed > 0

    def codes(self, prefix: str = "") -> list[str]:
        return [d.code for d in self.sorted() if d.code.startswith(prefix)]

    def sorted(self) -> list[Diagnostic]:
        return sorted(self.items, key=Diagnostic.sort_key)


# ---------------------------------------------------------------------------------------------
# Rendering


def _width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _pad_for(prefix: str) -> str:
    """Whitespace that lines up under `prefix` in a terminal: tabs copied, wide chars doubled."""
    out = []
    for ch in prefix:
        out.append("\t" if ch == "\t" else " " * _width(ch))
    return "".join(out)


_COLORS = {"error": "\x1b[1;31m", "warning": "\x1b[1;33m", "info": "\x1b[1;36m"}
_BOLD, _BLUE, _RESET = "\x1b[1m", "\x1b[1;34m", "\x1b[0m"


def render_one(d: Diagnostic, source: Source, color: bool = False) -> str:
    sev_c = _COLORS.get(d.severity, "") if color else ""
    bold = _BOLD if color else ""
    blue = _BLUE if color else ""
    reset = _RESET if color else ""
    head = f"{sev_c}{d.severity}[{d.code}]{reset}{bold}: {d.message}{reset}"
    if d.span is None:
        lines = [head]
        if d.hint:
            lines.append(f"  = hint: {d.hint}")
        return "\n".join(lines)
    s = d.span
    w = len(str(s.line))
    arrow = " " * (w + 1)
    gutter = " " * (w + 2)
    text = source.line_text(s.line)
    start = max(0, min(s.col - 1, len(text)))
    if s.end_line == s.line:
        end = max(start + 1, min(s.end_col - 1, len(text)))
    else:
        end = max(start + 1, len(text))
    caret_n = sum(_width(c) for c in text[start:end]) or 1
    carets = "^" * caret_n
    lines = [
        head,
        f"{arrow}{blue}-->{reset} {source.name}:{s.line}:{s.col}",
        f"{gutter}{blue}|{reset}",
        f"{blue}{str(s.line).rjust(w + 1)} |{reset} {text}",
        f"{gutter}{blue}|{reset} {_pad_for(text[:start])}{sev_c}{carets}{reset}"
        + (f" {sev_c}{d.hint}{reset}" if d.hint else ""),
    ]
    for r in d.related:
        lines.append(f"{gutter}{blue}={reset} note: {r.message} ({source.name}:{r.span.line}:{r.span.col})")
    return "\n".join(lines)


def render(diags: Iterable[Diagnostic], source: Source, color: bool = False,
           ascii_only: bool = False, suppressed: int = 0) -> str:
    parts = [render_one(d, source, color) for d in sorted(diags, key=Diagnostic.sort_key)]
    if suppressed:
        parts.append(f"note: {suppressed} further error{'s' if suppressed != 1 else ''} suppressed")
    text = "\n\n".join(parts)
    return to_ascii(text) if ascii_only else text


def summary_line(diags: Iterable[Diagnostic], suppressed: int = 0) -> str:
    items = list(diags)
    e = sum(1 for d in items if d.severity == "error") + suppressed
    w = sum(1 for d in items if d.severity == "warning")
    i = sum(1 for d in items if d.severity == "info")

    def n(k: int, word: str) -> str:
        return f"{k} {word}{'s' if k != 1 else ''}"

    return f"{n(e, 'error')}, {n(w, 'warning')}, {n(i, 'note')}"


def to_ascii(text: str) -> str:
    for k, v in ASCII_MAP.items():
        text = text.replace(k, v)
    return text.encode("ascii", errors="replace").decode("ascii")


def to_json(diags: Iterable[Diagnostic], source: Source, reports: dict | None = None,
            suppressed: int = 0) -> dict:
    out = []
    for d in sorted(diags, key=Diagnostic.sort_key):
        s = d.span
        out.append({
            "code": d.code,
            "severity": d.severity,
            "message": d.message,
            "line": s.line if s else None,
            "col": s.col if s else None,
            "end_line": s.end_line if s else None,
            "end_col": s.end_col if s else None,
            "hint": d.hint,
            "related": [{"line": r.span.line, "col": r.span.col, "end_line": r.span.end_line,
                         "end_col": r.span.end_col, "message": r.message} for r in d.related],
            "data": d.data,
        })
    return {"version": 1, "file": source.name, "diagnostics": out,
            "suppressed_errors": suppressed, "reports": reports or {}}
