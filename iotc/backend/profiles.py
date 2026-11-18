"""Board profiles (Architecture §11.1, ADR-16). Every board-specific constant lives here, so a
Phase 0 measurement that differs from the research is a one-line fix."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    fqbn: str
    analog_pins: tuple[str, ...]        # may host ANALOG sensors
    digital_pins: tuple[str, ...]       # D pins (DIGITAL sensors, actuators)
    led_pins: tuple[str, ...]           # onboard LED channels (actuators only), canonical names
    serial_pins: tuple[str, ...]        # S023
    adc_max: int                        # A: codes are 0..A
    jitter_codes: int                   # J (W112: dead band < 2J+1 codes)
    window_ram_budget: int | None       # bytes for MCU windows (S032); None = not a constraint
    slot_bytes: int                     # bytes per window bucket on the MCU
    min_deadline_ms: int                # W121
    has_bridge: bool
    has_matrix: bool
    active_low_leds: bool

    @property
    def all_pins(self) -> tuple[str, ...]:
        return self.analog_pins + self.digital_pins + self.led_pins

    def has_pin(self, pin: str) -> bool:
        return pin in self.all_pins

    def is_led(self, pin: str) -> bool:
        return pin in self.led_pins

    def active_low(self, pin: str) -> bool:
        return self.active_low_leds and self.is_led(pin)

    def cpp_pin(self, pin: str) -> str:
        """C++ expression for a canonical pin name."""
        if pin.startswith("LED"):
            return f"LED{pin[3:-1]}_{pin[-1]}"      # LED3R -> LED3_R
        if pin.startswith("D"):
            return pin[1:]                           # D7 -> 7
        return pin                                   # A0 -> A0


ANALOG = tuple(f"A{i}" for i in range(6))
DIGITAL = tuple(f"D{i}" for i in range(14))

UNO = Profile(
    name="uno", fqbn="arduino:avr:uno", analog_pins=ANALOG, digital_pins=DIGITAL, led_pins=(),
    serial_pins=("D0", "D1"), adc_max=1023, jitter_codes=2, window_ram_budget=1024,
    slot_bytes=16, min_deadline_ms=1, has_bridge=False, has_matrix=False, active_low_leds=False,
)

UNOQ = Profile(
    name="unoq", fqbn="arduino:zephyr:unoq", analog_pins=ANALOG, digital_pins=DIGITAL,
    led_pins=("LED3R", "LED3G", "LED3B", "LED4R", "LED4G", "LED4B"), serial_pins=("D0", "D1"),
    adc_max=1023, jitter_codes=2, window_ram_budget=None, slot_bytes=16, min_deadline_ms=5,
    has_bridge=True, has_matrix=True, active_low_leds=True,
)

PROFILES = {"uno": UNO, "unoq": UNOQ}


def get_profile(name: str) -> Profile:
    return PROFILES[name]
