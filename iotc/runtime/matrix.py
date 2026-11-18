"""LED-matrix status frame (Architecture §11.8). NORMATIVE; the generated C++ mirrors it.

frame() returns 104 brightness values (row-major, 8 rows x 13 columns, 0..7).
layout (from the compiler, iotc/table.py):
  {"bars": [{"zlo", "zhi", "ticks": [code, ...]}],   # one per shown sensor (<= 4)
   "rules": n_shown_rules (<= 10), "acts": n_shown_actuators (<= 5)}
Each bar shows the zoomed code range [zlo, zhi] (around the program's thresholds) on columns
0..9, so hysteresis gaps are visible; ticks are dim (2), the bar 5, its tip 7.
"""
from __future__ import annotations

ROWS, COLS = 8, 13
GLYPHS = {
    "F": ("111", "100", "110", "100", "100"),
    "E": ("111", "100", "110", "100", "111"),
    "!": ("010", "010", "010", "000", "010"),
    "X": ("101", "101", "010", "101", "101"),
}


def column(code: int, zlo: int, zhi: int) -> int:
    """Column 0..9 of a code inside the zoomed range."""
    if code <= zlo:
        return 0
    if code >= zhi:
        return 9
    return min(9, (code - zlo) * 10 // (zhi - zlo + 1))


def frame(layout: dict, codes: list[int], rule_state: list[int], cur: list[int], status: dict,
          tick_parity: int) -> list[int]:
    """rule_state[i]: 0 idle, 1 firing, 2 FOR running. status: failsafe, ensure, refused,
    bridge_failed, beat (bools)."""
    px = [0] * (ROWS * COLS)

    def put(r: int, c: int, v: int) -> None:
        px[r * COLS + c] = v

    bars = layout["bars"]
    rows_per_bar = 2 if len(bars) <= 2 else 1
    for i, bar in enumerate(bars):
        zlo, zhi = bar["zlo"], bar["zhi"]
        top = i * rows_per_bar
        for k in bar["ticks"]:
            for r in range(top, top + rows_per_bar):
                put(r, column(k, zlo, zhi), 2)
        if codes[i] >= zlo:
            tip = column(codes[i], zlo, zhi)
            for c in range(tip + 1):
                for r in range(top, top + rows_per_bar):
                    put(r, c, 7 if c == tip else 5)
    for i in range(layout["rules"]):
        s = rule_state[i]
        v = 7 if s == 1 else (7 if tick_parity else 1) if s == 2 else 1
        put(5, i, v)
    for i in range(layout["acts"]):
        v = 7 if cur[i] else 1
        put(7, 2 * i, v)
        put(7, 2 * i + 1, v)
    glyph = None
    if status.get("bridge_failed"):
        glyph = "X"
    elif status.get("failsafe"):
        glyph = "F"
    elif status.get("ensure"):
        glyph = "E"
    elif status.get("refused"):
        glyph = "!"
    if glyph:
        for r, row in enumerate(GLYPHS[glyph]):
            for c, bit in enumerate(row):
                if bit == "1":
                    put(1 + r, 10 + c, 7)
    if status.get("beat"):
        put(7, 12, 7)
    return px


def ascii_art(px: list[int]) -> str:
    """Text rendering for goldens and `iotc matrix-preview` (' ' off, '.' dim, 'o' mid, '#' bright)."""
    chars = " ..ooo##"
    lines = []
    for r in range(ROWS):
        lines.append("|" + "".join(chars[px[r * COLS + c]] for c in range(COLS)) + "|")
    return "\n".join(lines)
