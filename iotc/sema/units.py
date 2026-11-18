"""Units and exact conversion (Architecture §4.4). After sema every threshold is a Fraction in
its sensor's unit; later stages never see units."""
from __future__ import annotations

from fractions import Fraction

DIMENSION = {"C": "temperature", "F": "temperature", "PCT": "ratio", "LUX": "illuminance",
             "CM": "distance"}


def same_dimension(a: str, b: str) -> bool:
    return DIMENSION[a] == DIMENSION[b]


def convert(value: Fraction, from_unit: str, to_unit: str) -> Fraction:
    """Exact conversion between units of one dimension. Temperature maps are increasing, so
    comparison operators are kept."""
    if from_unit == to_unit:
        return value
    if (from_unit, to_unit) == ("F", "C"):
        return (value - 32) * Fraction(5, 9)
    if (from_unit, to_unit) == ("C", "F"):
        return value * Fraction(9, 5) + 32
    raise ValueError(f"cannot convert {from_unit} to {to_unit}")
