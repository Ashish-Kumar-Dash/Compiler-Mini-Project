"""Analyses (Architecture §8): NEVER proofs, closures, interference, hysteresis numbers."""
from iotc.options import Options
from iotc.pipeline import compile_text

T = "SENSOR temp AT A0 ANALOG UNIT C RANGE -40..125;\n"
FH = "ACTUATOR fan AT D7;\nACTUATOR heater AT D8;\n"


def run(text, opts=None):
    return compile_text(text, opts or Options())


def codes(res, include_info=False):
    return sorted(d.code for d in res.sink.items if include_info or d.severity != "info")


def never_line(res):
    return next(s for s in res.report.safety if s.head.startswith("NEVER"))


def test_never_proven_when_covered():
    res = run(T + FH + "RULE cool: WHEN temp > 35 THEN fan ON;\nRULE uncool: WHEN temp < 30 THEN fan OFF;\n"
              "RULE warm: WHEN temp < 10 THEN heater ON;\nRULE unwarm: WHEN temp > 15 THEN heater OFF;\n"
              "SAFETY NEVER heater ON AND fan ON;\n")
    assert never_line(res).status == "proven"


def test_never_latching_not_proven():
    """Request boxes are disjoint, but heater keeps its state: the v2 rule would wrongly prove it."""
    res = run(T + FH + "RULE warm: WHEN temp < 18 THEN heater ON;\nRULE unwarm: WHEN temp > 22 FOR 5s THEN heater OFF;\n"
              "RULE cool: WHEN temp > 35 THEN fan ON;\nRULE uncool: WHEN temp < 30 THEN fan OFF;\n"
              "SAFETY NEVER heater ON AND fan ON;\n")
    line = never_line(res)
    assert line.status == "enforced"
    assert "cool" in " ".join(line.detail)            # cool (fan ON) is not covered: unwarm has FOR


def test_never_with_off_side_never_proven():
    res = run(T + "ACTUATOR pump AT D7;\nACTUATOR valve AT D8;\n"
              "RULE p1: WHEN temp > 35 THEN pump ON;\nRULE p0: WHEN temp < 30 THEN pump OFF;\n"
              "RULE v1: WHEN temp < 10 THEN valve ON;\nRULE v0: WHEN temp > 15 THEN valve OFF;\n"
              "SAFETY NEVER pump ON AND valve OFF;\nFAILSAFE valve ON;\n")
    assert never_line(res).status == "enforced"


def test_covering_rule_must_have_same_placement():
    res = run(T + FH + "RULE cool: WHEN temp > 35 THEN fan ON;\nRULE uncool: WHEN temp < 30 THEN fan OFF;\n"
              "RULE warm: WHEN avg(temp, 10s) < 10 THEN heater ON;\nRULE unwarm: WHEN temp > 15 THEN heater OFF;\n"
              "SAFETY NEVER heater ON AND fan ON;\n")
    assert never_line(res).status == "enforced"     # warm is on Linux, uncool on the MCU


def test_transitive_closure_reported():
    res = run(T + FH + "ACTUATOR vent AT D9;\n"
              "RULE a: WHEN temp > 35 THEN fan ON;\nRULE b: WHEN temp < 30 THEN fan OFF;\n"
              "RULE c: WHEN temp < 10 THEN heater ON;\nRULE d: WHEN temp > 15 THEN heater OFF;\n"
              "RULE e: WHEN temp > 25 THEN vent ON;\nRULE f: WHEN temp < 20 THEN vent OFF;\n"
              "SAFETY NEVER fan ON AND heater ON;\nSAFETY NEVER heater OFF AND vent OFF;\n"
              "SAFETY ENSURE fan ON WHEN temp > 50;\n")
    w107 = [d for d in res.sink.items if d.code == "W107"]
    assert len(w107) == 1 and "heater OFF, vent ON" in w107[0].message


def test_e108_through_partners():
    res = run(T + "SENSOR hum AT A1 ANALOG UNIT PCT RANGE 0..100;\n" + FH +
              "RULE a: WHEN temp > 35 THEN fan ON;\nRULE b: WHEN temp < 30 THEN fan OFF;\n"
              "RULE c: WHEN temp < 10 THEN heater ON;\nRULE d: WHEN temp > 15 THEN heater OFF;\n"
              "SAFETY NEVER heater ON AND fan ON;\nSAFETY ENSURE fan ON WHEN temp > 50;\n"
              "SAFETY ENSURE heater ON WHEN hum < 10;\n")
    assert "E108" in codes(res)


def test_e108_not_reported_for_disjoint_conditions():
    res = run(T + FH +
              "RULE a: WHEN temp > 35 THEN fan ON;\nRULE b: WHEN temp < 30 THEN fan OFF;\n"
              "RULE c: WHEN temp < 10 THEN heater ON;\nRULE d: WHEN temp > 15 THEN heater OFF;\n"
              "SAFETY NEVER heater ON AND fan ON;\nSAFETY ENSURE fan ON WHEN temp > 50;\n"
              "SAFETY ENSURE heater ON WHEN temp < -30;\n")
    assert "E108" not in codes(res)


def test_w111_lists_every_reason():
    res = run(T + FH + "RULE cool REALTIME 10ms: WHEN temp > 35 THEN fan ON;\n"
              "RULE uncool REALTIME 10ms: WHEN temp < 30 THEN fan OFF;\n"
              "RULE warm: WHEN temp < 10 THEN heater ON;\nRULE unwarm: WHEN temp > 15 THEN heater OFF;\n"
              "SAFETY NEVER heater ON AND fan ON;\nSAFETY ENSURE fan ON WHEN temp > 50;\nFAILSAFE fan ON;\n")
    msgs = {d.message for d in res.sink.items if d.code == "W111"}
    assert any("'cool'" in m and "refused by NEVER" in m for m in msgs)
    assert any("'uncool'" in m and "ENSURE#1" in m and "FAILSAFE" in m for m in msgs)


def test_failsafe_only_matters_on_unoq_target():
    text = (T + "ACTUATOR fan AT D7;\nRULE cool: WHEN temp > 35 THEN fan ON;\n"
            "RULE uncool: WHEN temp < 30 THEN fan OFF;\nFAILSAFE fan OFF;\n")
    assert "W119" in codes(run(text))
    assert "W119" not in codes(run(text, Options.make("unoq", "mcu-only")))


def test_hysteresis_dead_band_counts_codes():
    res = run(T + "ACTUATOR fan AT D7;\nRULE cool: WHEN temp > 35 THEN fan ON;\nRULE uncool: WHEN temp < 30 THEN fan OFF;\n")
    (i105,) = [d for d in res.sink.items if d.code == "I105"]
    assert i105.data == {"gap": "5", "codes": 32}       # codes 434..465


def test_digital_pairs_skip_hysteresis():
    res = run("SENSOR door AT D2 DIGITAL;\nACTUATOR lamp AT D7;\n"
              "RULE on: WHEN door == 1 THEN lamp ON;\nRULE off: WHEN door == 0 THEN lamp OFF;\n")
    assert codes(res, include_info=True) == []


def test_w104_touch_point():
    res = run(T + "ACTUATOR fan AT D7;\nRULE cool: WHEN temp > 35 THEN fan ON;\nRULE uncool: WHEN temp <= 35 THEN fan OFF;\n")
    (w,) = [d for d in res.sink.items if d.code == "W104"]
    assert "temp = 35" in w.message


def test_conflict_overlap_and_related_spans():
    res = run(T + "ACTUATOR fan AT D7;\nRULE cool: WHEN temp > 35 THEN fan ON;\nRULE uncool: WHEN temp < 40 THEN fan OFF;\n")
    (e,) = [d for d in res.sink.items if d.code == "E103"]
    assert e.data["overlap"] == "temp ∈ (35, 40) C"
    assert [r.span.line for r in e.related] == [3, 4]
    assert not res.ok and res.report is not None


def test_report_json_shape():
    res = run(T + "ACTUATOR fan AT D7;\nRULE cool: WHEN temp > 35 THEN fan ON;\nRULE uncool: WHEN temp < 30 THEN fan OFF;\n")
    js = res.report.json()
    assert set(js) == {"thresholds", "placement", "safety", "resources"}
    assert js["thresholds"][0]["code"].startswith("code >= 466")
