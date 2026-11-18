"""Escaping of user text for every generated language (Architecture §11.4).

Generated code never carries a DSL name as an identifier (§0 rule 9); names and NOTIFY text
(§4.2) appear only inside literals produced here. Every function accepts any Python ``str``
(non-ASCII, control characters, NUL, U+2028), even though valid NOTIFY text is printable ASCII.
"""
from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["cpp_str", "py_str", "csv_field", "json_for_script", "c_comment_safe"]

# Printable ASCII 0x20..0x7E that needs a simple escape in a C++ literal. `?` is escaped so
# that no `??x` trigraph can ever form (trigraphs are live under -std=c++11).
_CPP_SIMPLE = {0x5C: "\\\\", 0x22: '\\"', 0x3F: "\\?"}

# Byte -> spelling inside a C++ literal, precomputed for all 256 byte values.
_CPP_BYTE = tuple(
    _CPP_SIMPLE.get(b, chr(b)) if 0x20 <= b <= 0x7E else f"\\{b:03o}" for b in range(256)
)

_JSON_HTML = str.maketrans(
    {
        "<": "\\u003c",
        ">": "\\u003e",
        "&": "\\u0026",
        " ": "\\u2028",
        " ": "\\u2029",
    }
)

_COMMENT_NAME = re.compile(r"[a-z0-9_]+")


def cpp_str(s: str) -> str:
    """Complete C++ string literal (with quotes) whose bytes are ``s.encode('utf-8')`` (§11.4).

    Printable ASCII appears as itself except ``\\``, ``"`` and ``?`` (simple escapes); every
    other UTF-8 byte is a 3-digit octal escape. Octal escapes stop after 3 digits, so a following
    digit is never absorbed (unlike greedy ``\\x``). The result is pure ASCII.
    Raises ValueError for a lone surrogate (not encodable as UTF-8).
    """
    try:
        data = s.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"cannot encode string as UTF-8: {exc.reason}") from exc
    return '"' + "".join(_CPP_BYTE[b] for b in data) + '"'


def py_str(s: str) -> str:
    """Python string literal for ``s``: ``repr(s)`` (§11.4)."""
    return repr(s)


def csv_field(s: str) -> str:
    """RFC 4180 field: always double-quoted, inner ``"`` doubled (§9.8 NT lines, §11.4)."""
    return '"' + s.replace('"', '""') + '"'


def json_for_script(obj: Any) -> str:
    """Compact JSON safe to embed in an HTML ``<script>`` element (§11.4).

    ``<``, ``>``, ``&``, U+2028 and U+2029 are written as ``\\uXXXX``; these characters occur
    only inside JSON strings, where the escape decodes to the same text. NaN and infinities are
    rejected (ValueError) because they are not JSON.
    """
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return text.translate(_JSON_HTML)


def c_comment_safe(name: str) -> str:
    """Return ``name`` if it matches ``[a-z0-9_]+``; otherwise raise ValueError.

    Generated C++/Python comments carry only such names (§11.3): no ``*/``, no trailing ``\\``
    line splice, no newline, no user text.
    """
    if not isinstance(name, str) or _COMMENT_NAME.fullmatch(name) is None:
        raise ValueError(f"name not allowed in a generated comment: {name!r}")
    return name
