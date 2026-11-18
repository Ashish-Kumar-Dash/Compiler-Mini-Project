"""Generate demo_script.csv: the board demo of Architecture §17 as a trace for greenhouse_board.iot.

Run with --kill-linux-at 15000 --revive-linux-at 20000. Segments (temp C, humidity %):
  0-2 s    25, 50   link comes up
  2-15 s   10, 50   avg(temp,10s) drops below 18 -> Linux rule warm: heater ON
  15-20 s  Linux killed; from 15.5 s temp 40 -> fan ON refused (heater incumbent) until the
           FAILSAFE turns the heater OFF ~2 s after the last heartbeat, then fan ON applied
  20 s     Linux revived (fresh state); temp back to 10 -> fan OFF, warm fires again later
  33 s     temp 40 -> fan ON refused by NEVER; unwarm later turns the heater OFF -> fan ON
  45 s     temp 55 -> ENSURE forces fan ON / heater OFF; alert NOTIFY after 5 s
  52 s     humidity 15 for 4 s -> dry NOTIFY after 3 s
"""
from pathlib import Path


def temp(t: int) -> float:
    if t < 2000:
        return 25
    if t < 15500:
        return 10
    if t < 20000:
        return 40
    if t < 33000:
        return 10
    if t < 45000:
        return 40
    return 55


def hum(t: int) -> float:
    return 15 if 52000 <= t < 56000 else 50


rows = ["t_ms,temp,humidity"] + [f"{t},{temp(t)},{hum(t)}" for t in range(0, 60000, 100)]
Path(__file__).with_name("demo_script.csv").write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
