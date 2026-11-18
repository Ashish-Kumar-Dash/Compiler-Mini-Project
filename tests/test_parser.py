"""Parser tests: AST snapshots and panic-mode recovery (Architecture §4.3, §5.2)."""
import random
import time
from pathlib import Path

import pytest

from iotc import ast as A
from iotc.diagnostics import MAX_ERRORS, render
from iotc.frontend import parse_file, parse_text

ROOT = Path(__file__).resolve().parents[1]
VALID = ["greenhouse", "greenhouse_demo", "greenhouse_board"]


@pytest.mark.parametrize("name", VALID)
def test_ast_golden(golden, name):
    res = parse_file(ROOT / "examples" / f"{name}.iot")
    assert res.program is not None and not res.sink.items
    golden(f"parser/{name}.ast", A.dump(res.program))


def test_ast_details():
    p = parse_text('SENSOR door AT D2 DIGITAL;\nACTUATOR fan AT LED3;\n'
                   'RULE r REALTIME 10ms: WHEN avg(t, 1.5s) >= 22.5 F AND door == 1 FOR 2s THEN fan OFF;\n'
                   'SAFETY ENSURE fan ON WHEN t > 50 AND h < 10 %;\n').program
    assert p is not None
    assert p.sensors[0].kind == "DIGITAL" and p.sensors[0].unit is None
    assert p.actuators[0].pin.text == "LED3R"          # LED3 is an alias of the red channel
    r = p.rules[0]
    assert (r.deadline_ms, r.hold_ms) == (10, 2000)
    agg = r.guard[0].value
    assert isinstance(agg, A.Agg) and (agg.fn, agg.window_ms) == ("avg", 1500)
    assert r.guard[0].number.text == "22.5" and r.guard[0].unit.text == "F"
    ens = p.safeties[0]
    assert isinstance(ens, A.Ensure) and ens.guard[1].unit.text == "PCT"


def test_forward_references_parse():
    res = parse_text("RULE r: WHEN t > 5 THEN f ON;\nSENSOR t AT A0 ANALOG UNIT C RANGE 0..10;\nACTUATOR f AT D7;")
    assert res.program is not None and not res.sink.items


def test_empty_program():
    res = parse_text("// nothing here\n")
    assert res.program is not None and res.program.rules == ()


def codes(text):
    return [(d.code, d.span.line, d.span.col) for d in parse_text(text).sink.sorted()]


def test_missing_semicolon_gives_exactly_one_error():
    text = ("SENSOR temp AT A0 ANALOG UNIT C RANGE -40..125;\nACTUATOR fan AT D7;\n"
            "RULE a: WHEN temp > 35 THEN fan ON\nRULE b: WHEN temp < 30 THEN fan OFF;\n")
    assert codes(text) == [("P001", 3, 35)]


def test_broken_declaration_does_not_cascade():
    text = "SENSOR temp AT A0 ANALOG UNIT C RANGE 0..;\nRULE a: WHEN temp > 5 THEN f ON;\nRULE b: WHEN temp > 6 THEN f ON;\n"
    assert codes(text) == [("P001", 1, 42)]


def test_lexer_error_inside_rule_suppresses_p001():
    assert codes("RULE r: WHEN t > 5 # THEN f ON;\nSENSOR t AT A0 DIGITAL;") == [("L001", 1, 20)]


def test_eof_inside_rule():
    res = parse_text("RULE r: WHEN t >")
    (d,) = res.sink.items
    assert d.code == "P001" and "end of file" in d.message


def test_error_cap_and_termination():
    text = "\n".join(["@@ ## RULE ; ) ( SENSOR"] * 200)
    t0 = time.perf_counter()
    res = parse_text(text)
    assert time.perf_counter() - t0 < 2.0
    assert res.sink.error_count == MAX_ERRORS and res.sink.suppressed > 0
    out = render(res.sink.items, res.source, suppressed=res.sink.suppressed)
    assert out.endswith("further errors suppressed")


@pytest.mark.parametrize("seed", range(30))
def test_random_token_soup_terminates(seed):
    r = random.Random(seed)
    words = ["SENSOR", "ACTUATOR", "RULE", "SAFETY", "FAILSAFE", "NEVER", "ENSURE", "WHEN", "THEN",
             "AND", "FOR", "AT", "ON", "OFF", "temp", "fan", "35", "-4", "10ms", ";", ":", "(", ")",
             ",", ">", "..", "avg", "A0", "D7", '"x"', "NOTIFY", "UNIT", "C", "RANGE", "#", "REALTIME"]
    text = " ".join(r.choice(words) for _ in range(400))
    res = parse_text(text)  # must not raise or hang
    assert res.sink.error_count <= MAX_ERRORS


@pytest.mark.parametrize("text,hint_part", [
    ("RULE cool WHEN t > 5 THEN f ON;", "write RULE cool: WHEN"),
    ("RULE r: when t > 5 THEN f ON;", "keywords are UPPERCASE: write WHEN"),
    ("SENSOR t AT a0 DIGITAL;", "pins are UPPERCASE: write A0"),
    ("SENSOR t AT A0 ANALOG UNIT c RANGE 0..1;", "units are UPPERCASE: write C"),
    ("RULE r REALTIME 10 ms: WHEN t > 5 THEN f ON;", "write 10ms without a space"),
    ("RULE r: WHEN avg(t, 5 min) > 5 THEN f ON;", "write 5min without a space"),
    ("RULE r: WHEN avg(t, 10) > 5 THEN f ON;", "add a unit"),
    ("RULE r: WHEN 35 < temp THEN f ON;", "write the sensor first: temp > 35"),
    ("RULE r: WHEN t > 5 THEN f ON, g OFF;", "write two rules"),
    ("SAFETY ENSURE f ON WHEN t > 5 FOR 2s;", "FOR is not allowed in ENSURE"),
    ("SENSOR d AT D2 DIGITAL UNIT C;", "DIGITAL sensors take no UNIT or RANGE"),
    ("SENSOR t AT A0 ANALOG RANGE 0..1 UNIT C;", "write UNIT before RANGE"),
    ("SENSOR t AT A0 ANALOG UNIT C RANGE 0 1;", "ranges are written lo..hi"),
    ("RULE r: WHEN t > 5 THEN f high;", "actuator states are ON or OFF"),
    ("RULE r: WHEN t > 5 THEN NOTIFY hot;", 'write NOTIFY "message"'),
    ("sensor t AT A0 DIGITAL;", "keywords are UPPERCASE: write SENSOR"),
    ("SENSOR avg AT A0 DIGITAL;", "reserved"),
])
def test_parser_hints(text, hint_part):
    diags = parse_text(text).sink.sorted()
    assert diags and diags[0].code == "P001", [d.message for d in diags]
    assert hint_part in (diags[0].hint or ""), diags[0].hint


def test_program_is_none_after_syntax_errors():
    assert parse_text("SENSOR t AT A0 DIGITAL").program is None
