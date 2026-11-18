"""Hand-built IR for examples/greenhouse_board.iot (Architecture §17), board unoq, target unoq.

This is ground truth written independently of the front end, so that the runtime, interpreter
and back ends can be built and tested before (and later checked against) lowering. Numbers:
RANGE -40..125 with A = 1023 gives r = 6.2*(T + 40) exactly; humidity 0..100 gives r = 10.23*T.
"""
from fractions import Fraction as F

from iotc.ir import (
    OFF,
    ON,
    AvgTest,
    Bound,
    CodeRange,
    Interval,
    IRActuator,
    IREnsure,
    IRFailsafe,
    IRNever,
    IRProgram,
    IRRule,
    IRSensor,
    IRWindow,
    Notify,
    SetState,
    Var,
)

TEMP = Var("temp")
HUM = Var("humidity")
AVG_TEMP = Var("temp", "avg", 10_000)


def _iv(lo, lo_c, hi, hi_c):
    return Interval(Bound(F(lo), lo_c), Bound(F(hi), hi_c))


def build(board: str = "unoq", target: str = "unoq") -> IRProgram:
    sensors = (
        IRSensor(0, "temp", "A0", "ANALOG", "C", F(-40), F(125), 1023),
        IRSensor(1, "humidity", "A1", "ANALOG", "PCT", F(0), F(100), 1023),
    )
    actuators = (
        IRActuator(0, "fan", "LED4B", True, OFF, None),
        IRActuator(1, "heater", "LED3R", True, OFF, OFF),
    )
    linux = target == "unoq"
    windows = (IRWindow(0, 0, 10_000, 32, on_mcu=not linux, on_linux=linux),)
    place_w = "LINUX" if linux else "MCU"
    win_reason = ("avg(temp, 10s): window history kept on Linux",) if linux else ("--target mcu-only",)
    notify_place = "LINUX" if linux else "MCU"
    notify_reason = ("NOTIFY leaves the MCU",) if linux else ("--target mcu-only",)
    rules = (
        IRRule(0, "cool", {TEMP: _iv(35, False, 125, True)}, (CodeRange(TEMP, 466, None),),
               None, SetState(0, ON), 20, "MCU", ("REALTIME 20ms",), None),
        IRRule(1, "uncool", {TEMP: _iv(-40, True, 30, False)}, (CodeRange(TEMP, None, 433),),
               None, SetState(0, OFF), 20, "MCU", ("REALTIME 20ms",), None),
        # avg(temp,10s) < 18  <=>  avg_code < 359.6 = 1798/5  <=>  5*sum < 1798*count
        IRRule(2, "warm", {AVG_TEMP: _iv(-40, True, 18, False)}, (AvgTest(AVG_TEMP, "<", 1798, 5),),
               None, SetState(1, ON), None, place_w, win_reason, 0 if linux else None),
        # avg(temp,10s) > 22  <=>  avg_code > 384.4 = 1922/5
        IRRule(3, "unwarm", {AVG_TEMP: _iv(22, False, 125, True)}, (AvgTest(AVG_TEMP, ">", 1922, 5),),
               None, SetState(1, OFF), None, place_w, win_reason, 1 if linux else None),
        # temp > 45  <=>  code >= 528 (r = 527 exactly)
        IRRule(4, "alert", {TEMP: _iv(45, False, 125, True)}, (CodeRange(TEMP, 528, None),),
               5000, Notify("Overheat"), None, notify_place, notify_reason, None),
        # humidity < 20  <=>  code <= 204 (r = 204.6)
        IRRule(5, "dry", {HUM: _iv(0, True, 20, False)}, (CodeRange(HUM, None, 204),),
               3000, Notify("Air too dry"), None, notify_place, notify_reason, None),
    )
    nevers = (IRNever(0, 1, ON, 0, ON),)  # heater ON AND fan ON
    ensures = (
        # temp > 50 <=> code >= 559 (r = 558 exactly); closure: fan ON forces heater OFF
        IREnsure(0, 0, ON, {TEMP: _iv(50, False, 125, True)}, (CodeRange(TEMP, 559, None),),
                 ((0, ON), (1, OFF))),
    )
    failsafes = (IRFailsafe(1, OFF),)
    return IRProgram(
        name="examples/greenhouse_board.iot",
        source_sha="0" * 64,
        board=board,
        target=target,
        sensors=sensors,
        actuators=actuators,
        windows=windows,
        rules=rules,
        nevers=nevers,
        ensures=ensures,
        failsafes=failsafes,
        fs_closure=((1, OFF),),
    )
