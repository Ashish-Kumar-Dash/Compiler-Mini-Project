"""Lexer tests: every edge case listed in Architecture §4.2, plus token-stream goldens."""
from pathlib import Path

import pytest

from iotc.diagnostics import Sink
from iotc.lexer import did_you_mean, lex
from iotc.source import decode_source
from iotc.tokens import K

ROOT = Path(__file__).resolve().parents[1]


def run(text: str):
    sink = Sink()
    toks = lex(decode_source("t.iot", text.encode("utf-8")), sink)
    return toks, sink


def kinds(text: str) -> list[tuple[str, str]]:
    toks, sink = run(text)
    assert not sink.items, [d.message for d in sink.items]
    return [(t.kind.value, t.text) for t in toks if t.kind is not K.EOF]


def codes(text: str) -> list[tuple[str, int]]:
    _, sink = run(text)
    return [(d.code, d.span.col) for d in sink.sorted()]


@pytest.mark.parametrize("text,expected", [
    ("RANGE -40..125", [("KEYWORD", "RANGE"), ("NUMBER", "-40"), ("OP", ".."), ("NUMBER", "125")]),
    ("0..100", [("NUMBER", "0"), ("OP", ".."), ("NUMBER", "100")]),
    ("-40..-10", [("NUMBER", "-40"), ("OP", ".."), ("NUMBER", "-10")]),
    ("1.5..2", [("NUMBER", "1.5"), ("OP", ".."), ("NUMBER", "2")]),
    ("5min", [("DURATION", "5min")]),
    ("min(", [("AGG", "min"), ("PUNCT", "(")]),
    ("5 min", [("NUMBER", "5"), ("AGG", "min")]),
    ("10 ms", [("NUMBER", "10"), ("IDENT", "ms")]),
    ("50%", [("NUMBER", "50"), ("UNIT", "%")]),
    ("50 %", [("NUMBER", "50"), ("UNIT", "%")]),
    (">= <= == > <", [("OP", ">="), ("OP", "<="), ("OP", "=="), ("OP", ">"), ("OP", "<")]),
    ('"http://x" // c', [("STRING", '"http://x"')]),
    ("// comment at EOF", []),
    ("LED3 LED4B A5 D13 D22", [("PIN", "LED3"), ("PIN", "LED4B"), ("PIN", "A5"), ("PIN", "D13"), ("PIN", "D22")]),
    ("when", [("IDENT", "when")]),
    ("a0 c", [("IDENT", "a0"), ("IDENT", "c")]),
    ("01 007.50", [("NUMBER", "01"), ("NUMBER", "007.50")]),
    ("-0", [("NUMBER", "-0")]),
])
def test_token_streams(text, expected):
    assert kinds(text) == expected


@pytest.mark.parametrize("text,ms", [
    ("10ms", 10), ("1.5s", 1500), ("5min", 300_000), ("2h", 7_200_000), ("0.001s", 1),
    ("0.25min", 15_000), ("1.5min", 90_000), ("0s", 0),
])
def test_duration_values(text, ms):
    toks, sink = run(text)
    assert not sink.items
    assert toks[0].kind is K.DURATION and toks[0].value == ms


@pytest.mark.parametrize("text,expected", [
    ("temp # 3", [("L001", 6)]),
    (".5", [("L001", 1)]),
    ("5.", [("L001", 1)]),
    ("1...2", [("L001", 4)]),
    ("- 5", [("L001", 1)]),
    ("--5", [("L001", 1)]),
    ("-x", [("L001", 1)]),
    ("=", [("L001", 1)]),
    ("=>", [("L001", 1)]),
    ("=<", [("L001", 1)]),
    ("> =", [("L001", 3)]),
    ("/* x */", [("L001", 1)]),
    ("−40", [("L001", 1)]),
    ("“Hot”", [("L001", 1)]),
    ("a b", [("L001", 2)]),
    ("３５", [("L001", 1)]),
    ("٣٥", [("L001", 1)]),
    ("température", [("L001", 1)]),
    ("x\x00y", [("L001", 2)]),
    ('"Hot', [("L002", 1)]),
    ('"Hot\nWHEN', [("L002", 1)]),
    ("10sec", [("L003", 1)]),
    ("35C", [("L003", 1)]),
    ("0.5ms", [("L003", 1)]),
    ("1.0001s", [("L003", 1)]),
    ("-5s", [("L003", 1)]),
    ("5m", [("L003", 1)]),
    ("5mins", [("L003", 1)]),
    ("5h2", [("L003", 1)]),
    ("5MS", [("L003", 1)]),
    ("1e3", [("L003", 1)]),
    ("0x10", [("L003", 1)]),
    ("1_000", [("L003", 1)]),
    ("1234567890", [("L003", 1)]),
    ('"a\\nb"', [("L004", 3)]),
    ('"a\\qb"', [("L004", 3)]),
    ('"a\tb"', [("L004", 3)]),
    ("WHN", [("L005", 1)]),
    ("OR", [("L005", 1)]),
    ("LED3_R", [("L005", 1)]),
    ("!=", [("L006", 1)]),
    ("Temp", [("L007", 1)]),
    ("_x", [("L007", 1)]),
    ("a__b", [("L007", 1)]),
    ("a" * 33, [("L007", 1)]),
    ('""', [("L008", 1)]),
    ('"' + "x" * 65 + '"', [("L008", 1)]),
    ('"héllo"', [("L008", 1)]),
])
def test_lexer_errors(text, expected):
    assert codes(text) == expected


def test_valid_escapes_and_lengths():
    toks, sink = run('"a\\"b\\\\c"')
    assert not sink.items and toks[0].value == 'a"b\\c'
    toks, sink = run('"' + "x" * 64 + '"')
    assert not sink.items


@pytest.mark.parametrize("word,hint", [("WHN", "WHEN"), ("SENSR", "SENSOR"), ("ACTUATR", "ACTUATOR"),
                                       ("FAILSAF", "FAILSAFE"), ("ENSUR", "ENSURE")])
def test_did_you_mean_keywords(word, hint):
    _, sink = run(word)
    assert sink.items[0].hint == f"did you mean {hint}?"


def test_did_you_mean_threshold():
    assert did_you_mean("a", ["b"]) == "b"          # max(1, len/3) = 1
    assert did_you_mean("xyzzy", ["temp"]) is None


def test_lexer_continues_after_errors():
    toks, sink = run("temp # 3 ; RULE")
    assert [t.kind for t in toks] == [K.IDENT, K.ERROR, K.NUMBER, K.PUNCT, K.KEYWORD, K.EOF]
    assert len(sink.items) == 1


def test_positions_with_tabs_and_crlf():
    src = decode_source("t.iot", b"\tSENSOR\r\n  temp")
    toks = lex(src, Sink())
    assert (toks[0].span.line, toks[0].span.col) == (1, 2)
    assert (toks[1].span.line, toks[1].span.col) == (2, 3)


def test_not_utf8_reports_l000_once():
    sink = Sink()
    lex(decode_source("t.iot", b"SENSOR t AT A0 DIGITAL; // caf\xe9\n"), sink)
    assert [d.code for d in sink.items] == ["L000"]


@pytest.mark.parametrize("name", ["greenhouse", "greenhouse_demo", "greenhouse_board"])
def test_token_stream_golden(golden, name):
    sink = Sink()
    src = decode_source(f"examples/{name}.iot", (ROOT / "examples" / f"{name}.iot").read_bytes())
    toks = lex(src, sink)
    assert not sink.items
    lines = [f"{t.span.line}:{t.span.col}-{t.span.end_col} {t.kind.value} {t.text!r} {t.value!r}"
             for t in toks]
    golden(f"lexer/{name}.tokens", "\n".join(lines) + "\n")
