"""Semantic-check edge cases (Architecture §4.4, §5.4, §6) beyond the one-file-per-code corpus."""
from fractions import Fraction as F

import pytest

from iotc.cli import main
from iotc.ir import Var
from iotc.options import Options
from iotc.pipeline import compile_text
from iotc.sema.symbols import suggest_name

BASE = "SENSOR temp AT A0 ANALOG UNIT C RANGE -40..125;\nACTUATOR fan AT D7;\n"


def codes(text, opts=None):
    return [d.code for d in compile_text(text, opts).sink.sorted() if d.severity != "info"]


@pytest.mark.parametrize("word,cands,expected", [
    ("temprature", ["temp"], "temp"),
    ("tmp", ["temp"], "temp"),
    ("heatr", ["heater", "fan"], "heater"),
    ("xyz", ["temp", "fan"], None),
    ("a", ["b"], "b"),
])
def test_suggest_name(word, cands, expected):
    assert suggest_name(word, cands) == expected


def test_suggestions_only_of_expected_kind():
    res = compile_text(BASE + "RULE r: WHEN fna > 5 THEN fan ON;\n")
    (d,) = [d for d in res.sink.items if d.code == "S001"]
    assert d.hint != "did you mean 'fan'?"      # fan is an actuator, not a sensor


def test_rule_name_clashes_with_actuator():
    assert "S002" in codes(BASE + "RULE fan: WHEN temp > 5 THEN fan ON;\n")


def test_forward_references_resolve():
    text = "RULE r: WHEN temp > 35 THEN fan ON;\nRULE s: WHEN temp < 30 THEN fan OFF;\n" + BASE
    assert codes(text) == []


def test_fahrenheit_95_is_exactly_35():
    res = compile_text(BASE + "RULE r: WHEN temp > 95 F THEN fan ON;\nRULE s: WHEN temp < 30 THEN fan OFF;\n")
    (cmp,) = res.checked.program.rules[0].guard
    assert res.checked.thresholds[cmp] == F(35)


def test_celsius_threshold_on_fahrenheit_sensor():
    res = compile_text("SENSOR t AT A0 ANALOG UNIT F RANGE 0..200;\nACTUATOR fan AT D7;\n"
                       "RULE r: WHEN t > 35 C THEN fan ON;\nRULE s: WHEN t < 80 THEN fan OFF;\n")
    assert res.checked.thresholds[res.checked.program.rules[0].guard[0]] == F(95)


def test_digital_threshold_outside_domain():
    assert "S011" in codes("SENSOR door AT D2 DIGITAL;\nACTUATOR fan AT D7;\n"
                           "RULE r: WHEN door == 2 THEN fan ON;\nRULE s: WHEN door == 0 THEN fan OFF;\n")


def test_keyword_like_names_are_accepted():
    text = ("SENSOR int AT A0 ANALOG UNIT C RANGE 0..100;\nSENSOR class AT A1 ANALOG UNIT C RANGE 0..100;\n"
            "ACTUATOR loop AT D7;\nACTUATOR setup AT D8;\n"
            "RULE delay: WHEN int > 50 THEN loop ON;\nRULE lambda: WHEN int < 40 THEN loop OFF;\n"
            "RULE millis: WHEN class > 50 THEN setup ON;\nRULE pass: WHEN class < 40 THEN setup OFF;\n")
    res = compile_text(text)
    assert res.ir is not None and [d.code for d in res.sink.items if d.severity != "info"] == []


def test_sema_errors_stop_before_lowering():
    res = compile_text(BASE + "RULE r: WHEN temprature > 5 THEN fan ON;\n")
    assert res.ir is None


def test_empty_program_lowers():
    res = compile_text("// nothing\n")
    assert res.ir is not None and res.ir.rules == () and res.ir.actuators == ()


def test_window_keys_are_shared():
    res = compile_text(BASE + "RULE a: WHEN avg(temp, 10s) > 35 THEN fan ON;\n"
                       "RULE b: WHEN max(temp, 10000ms) < 30 THEN fan OFF;\n")
    assert len(res.ir.windows) == 1
    assert {v for r in res.ir.rules for v in r.guard} == {Var("temp", "avg", 10000), Var("temp", "max", 10000)}


def test_uno_board_rejects_unoq_target():
    with pytest.raises(ValueError):
        Options.make("uno", "unoq")
    assert main(["check", "examples/greenhouse.iot", "--board", "uno", "--target", "unoq"]) == 2


def test_uno_default_target_is_mcu_only():
    assert Options.make("uno").target == "mcu-only"


def test_led_pins_on_uno_are_s020():
    assert codes(BASE.replace("D7", "LED4B") + "RULE r: WHEN temp > 35 THEN fan ON;\n"
                 "RULE s: WHEN temp < 30 THEN fan OFF;\n", Options.make("uno")) == ["S020"]
