"""Pin checks against the board profile: S020, S021, S022, S023 (Architecture §5.4, §11.1)."""
from __future__ import annotations

from .. import ast as A
from ..backend.profiles import Profile
from ..diagnostics import Related, Sink


def check_pins(program: A.Program, profile: Profile, sink: Sink) -> None:
    used: dict[str, A.Name] = {}
    entries: list[tuple[A.Name, str, A.Name]] = (
        [(s.pin, "sensor", s.name) for s in program.sensors]
        + [(a.pin, "actuator", a.name) for a in program.actuators]
    )
    entries.sort(key=lambda e: (e[0].span.line, e[0].span.col))
    kinds = {s.name.text: s.kind for s in program.sensors}
    for pin, role, owner in entries:
        p = pin.text
        if not profile.has_pin(p):
            if p.startswith("LED") and not profile.led_pins:
                hint = f"the {profile.name} board has no onboard RGB LEDs; use a D pin"
            elif p.startswith("A"):
                hint = f"the {profile.name} board has analog pins A0-A5"
            elif p.startswith("D"):
                hint = f"the {profile.name} board has digital pins D0-D13"
            else:
                leds = ", ".join(profile.led_pins) if profile.led_pins else "none"
                hint = f"onboard LED channels on {profile.name}: {leds}"
            sink.add("S020", f"pin {p} does not exist on board {profile.name}", pin.span, hint)
            continue
        if role == "sensor":
            if profile.is_led(p):
                sink.add("S022", f"sensor '{owner.text}' cannot use LED pin {p}", pin.span,
                         "onboard LEDs are outputs; use an A or D pin")
                continue
            if kinds.get(owner.text) == "ANALOG" and p not in profile.analog_pins:
                sink.add("S022", f"ANALOG sensor '{owner.text}' needs an analog pin, not {p}",
                         pin.span, "use A0-A5, or declare the sensor DIGITAL")
                continue
        if p in profile.serial_pins:
            sink.add("S023", f"pin {p} is a serial pin", pin.span,
                     "D0/D1 carry Serial1 and the Zephyr console on the UNO Q; prefer D2-D13"
                     if profile.name == "unoq" else "D0/D1 carry Serial; prefer D2-D13")
        if p in used:
            first = used[p]
            alias = " (LED3 is LED3R, LED4 is LED4R)" if p.startswith("LED") else ""
            sink.add("S021", f"pin {p} is already used{alias}", pin.span, "give each declaration its own pin",
                     [Related(first.span, f"{p} first bound here")])
            continue
        used[p] = pin
