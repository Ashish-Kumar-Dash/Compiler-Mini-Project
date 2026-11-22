# Board results (Arduino UNO Q)

Hand-written record of the runs on our board, host `baigan`. The board ran Arduino App CLI 0.13.0,
App Lab 0.10.0, arduino-router 0.10.0, Debian 13 and Python 3.13.14 in the App container. These
versions were frozen after Phase 0. Steps and commands are in `docs/MANUAL.md`. Rows marked
**pending** have not been run yet.

## B0 — Phase 0 probe (22 Nov 2026)

A hand-written probe App measured the platform before any code was generated. The raw output is in
`hardware/phase0/results/`, and the full table is in Architecture §19.

| Quantity | Measured |
|---|---|
| MCU pass interval with the Bridge active | median 1.15 ms, min 0.6 ms, max 2.5 ms (about 900 passes/s) |
| Bridge round trip (Linux → MCU → Linux, n = 2438) | median 7.4 ms, p95 12.2 ms, max 101.6 ms |
| `micros()` resolution | 1 µs |
| LED-matrix `draw()` | 4.6 µs average, 12 µs max |
| Burst of 40 notifies (about 2 KB) | stalls one pass by 15.5 ms; nothing lost |
| `Serial.begin` on the UNO Q | blocks 17 ms (so the generated sketch never logs over Serial) |
| Handler types | a `bool` parameter rejects the integer `1`; `u16` silently truncates 70000 to 4464 |
| `provide_safe` handlers | 2422 of 2422 ran on the main thread; 1 ran during `loop()` |
| Linux `docker pause` / `docker kill` | the MCU kept running in both cases |

## B1 — generated sketch, MCU only, compiled-in scenario

`iotc build … --target mcu-only --input scenario --scenario scenarios/demo_tour.csv`

The LEDs and the LED matrix followed the predicted 50 s timeline:
- fan ON at 3 s and OFF at 9 s (hysteresis);
- heater ON at about 17.7 s;
- fan refused at 26 s (the `!` glyph);
- the fan/heater swap at 30.1 s;
- `E` while ENSURE was forcing.

The sketch compiled in App Lab on the board.

## B2 — full UNO Q App with the web dashboard

`iotc build … --input sim --demo-controls --scenario scenarios/fallback_sweep.csv`

| Check | Result |
|---|---|
| Rules split across processors | `fan OFF · rule uncool (MCU)` and `heater ON · rule warm (Linux)` both seen |
| ENSURE | at 148.8 s, fan forced ON and heater forced OFF; 5 s later, `NOTIFY alert: Overheat` (the FOR 5s timer) |
| Pause (soft failsafe) | `Linux link DOWN` at 181.1 s, `UP` at 189.8 s |
| Bridge round trip (live, on the page) | 9 ms median, 13 ms max |
| Worst MCU pass interval | 3.9 ms against a 20 ms REALTIME deadline; 0 overruns |
| Dashboard | sliders, lamps, chart, placement and safety tables, matrix mirror, toasts all worked |
| Issue found | port 7000 not reachable over the network: the board had no Wi-Fi/LAN address. Fixed by tunnelling over USB (`adb forward tcp:7000 tcp:7000`, `scripts/open_page.ps1`) |

## B3 — failsafe on the real board

**E1 (page pause, heater ON first):**

| Board time | Line in the page log |
|---|---|
| 85.9 s | Linux rule engine paused from the page: heartbeats stop |
| 87.9 s | Linux link DOWN: FAILSAFE states applied |
| 87.9 s | heater OFF · FAILSAFE (Linux link down) |
| 88.5 s | fan ON · rule cool (MCU), while Linux was paused |
| 96.1 s | Linux link UP |
| 97.7 s | fan OFF · rule uncool (MCU) |
| 101.7 s | heater ON · rule warm (Linux) |

- **Failsafe latency 2.0 s** (predicted 1.5–2.1 s: the 2000 ms timeout counted from the last
  heartbeat).
- The MCU-placed rule kept working during the outage.
- Linux control returned after the link came back UP.

| Test | Result |
|---|---|
| E1 runs 2 and 3 (latency spread) | **pending** |
| E2 `docker pause` (frozen Linux): LED3 off, `F`, fallback sweep, board time continues after restore | **pending** |
| E3 `docker kill` (crashed Linux): same, plus restart from scratch | **pending** |

## B4 — conformance mode on the board

`iotc build … --input scenario --scenario scenarios/demo_tour.csv` (the compiler predicts 9 events
per iteration; see `stage5_conformance_prediction.txt`).

| Iteration | Page summary |
|---|---|
| 1 | **pending** |
| 2 | **pending** |
| 3 (with a pause as a control) | **pending** |

On the laptop, the same App on the virtual board (`iotc dashboard --input scenario`) showed
"Iteration 1: 9/9 predicted events matched exactly". The compiled C++ sketch, run in a closed loop
with the Linux runtime, matches the prediction in the test suite (`tests/test_conformance.py`).
