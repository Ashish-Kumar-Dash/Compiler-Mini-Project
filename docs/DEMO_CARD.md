# Demo card: iotc on the Arduino UNO Q (about 10 minutes)

Compiler first, board last. For each step: **do** (what you do), **see** (what should happen),
**wait** (the most you should wait), **say** (the talking point) and **if not** (the fallback).

## Before the audience arrives (15 min before)

1. Power the board from the 5 V / 3 A supply and connect the data USB-C cable to the laptop.
   Wait for the boot to finish (about 1 min).
2. Run `py -3.13 -m iotc doctor`. Every line should be ✓ (arduino-cli may be ⚠). If "App Lab CLI"
   says the version changed, App Lab has updated itself: run B2 from `docs/MANUAL.md` once before
   the demo.
3. Build and deploy the demo App, then open the page:
   ```powershell
   py -3.13 tasks.py backups
   scripts\deploy.ps1 build\b2_board -NoFollow
   scripts\open_page.ps1
   ```
   The page is `http://localhost:7000`, tunnelled over USB, so no Wi-Fi is needed. Check that the
   pill says **Linux link UP**.
4. Open a PowerShell window in the repo with a large font. Keep `docs\evidence\` open in the
   editor as a backup for steps 1–3.
5. Set the temp slider to 25 C and humidity to 50 %. Both LEDs should be off.

## Part 1 — the compiler (about 4 min)

| # | Do | See | Say |
|---|---|---|---|
| 1 | `py -3.13 -m iotc dump examples\greenhouse_board.iot --stage tokens` (scroll), then `--stage ast` | the token stream with line:col spans; one AST line per declaration | "Stage 1: hand-written lexer, LL(1) recursive-descent parser. One token of lookahead decides every choice." |
| 2 | `py -3.13 -m iotc check examples\greenhouse_board.iot` | the code thresholds (`temp > 35 ⇔ code ≥ 466`), I105 hysteresis gap of 32 codes, the placement table, the safety report (⚠ NEVER not proven, ✓ ENSURE closure) | "Stage 2–3: every threshold becomes an exact integer test on the ADC code, so the MCU, Linux and the simulator can never disagree at the boundary. The compiler decides where each rule runs, and says why." |
| 3 | `py -3.13 -m iotc check examples\errors\S001_undeclared_name.iot`, then `E103_conflict.iot`, `P201_realtime_window_on_linux.iot`, `E116_contradictory_ensure.iot` | did-you-mean `temp`; the conflict with its exact overlap `(35, 40) C`; REALTIME on Linux rejected; contradictory ENSURE | "Bad programs are rejected with the exact reason, pointing at the source. P201 exists because we measured the Bridge: 7 ms median but 102 ms worst case." |
| 4 | `py -3.13 -m iotc dump examples\greenhouse_board.iot --stage ir` | boxes, integer tests (`5*sum < 1798*count` for the average), placement, closures | "Stage 3: the event IR. Every condition is a box of intervals, which is why the analyses are exact." |

**If not:** show the same outputs from `docs\evidence\stage1_*.txt`, `stage2_*.txt` and
`stage3_ir.txt`.

## Part 2 — the board (about 6 min)

Watch three things: LED4 **blue** is the fan, LED3 **red** is the heater, and the 8×13 LED matrix
shows sensor bars, rule pixels, actuator blocks and a status glyph. Point at the page and the
board in turn.

| # | Do | See | Wait | Say |
|---|---|---|---|---|
| 5 | **Hysteresis.** temp slider to 40, then 32, then 28 | at 40 the fan (blue) goes ON; at 32 it stays ON; at 28 it goes OFF. The matrix bar moves between the two threshold ticks | 1 s | "Two rules, a 5 °C dead band, checked by the compiler (I105). These rules run on the microcontroller (REALTIME 20 ms)." |
| 6 | **Linux rule.** temp slider to 10 | after the 10 s average drops: `heater ON · rule warm (Linux)`, LED3 red, the `warm` row lights in purple | 12 s | "The averaging rule runs on Linux. Linux never touches a pin: it sends a bit mask, and the MCU acts." |
| 7 | **Soft failsafe.** with the heater ON, press **Pause Linux rule engine (10 s)**; then move temp to 40 | about 2 s later: `Linux link DOWN`, `heater OFF · FAILSAFE`, matrix `F`; at 40 the fan still turns ON. After 10 s, `Linux link UP` | 3 s, then 10 s | "Heartbeats stopped. The MCU applied the declared FAILSAFE within the 2 s timeout (measured 2.0 s), and its own rules kept working." |
| 8 | **NEVER refusal.** temp to 10, wait for the heater, then temp to 40 | `fan ON refused by NEVER#1 (heater is ON)`, matrix `!`. About 10 s later `unwarm` turns the heater OFF and the fan comes ON | 12 s | "The compiler could not prove this NEVER statically, so the MCU monitor enforces it every pass. The incumbent wins." |
| 9 | **ENSURE.** temp to 55 | fan forced ON, heater forced OFF, matrix `E`; after 5 s the purple "Overheat" toast | 6 s | "ENSURE forces the fan, and the closure computed by the compiler also forces the heater OFF, because of NEVER." |
| 10 | **Real kill.** temp back to 10 and wait for the heater, then `scripts\linux_kill.ps1` | the page freezes with the red banner; on the board LED3 goes off within about 2 s, matrix `F`; about 3 s later the matrix bar sweeps by itself and the fan LED follows it | 6 s | "This is a real crash of the Linux program. The microcontroller keeps running its rules on its own fallback input. Placement, visible in hardware." |
| 11 | `scripts\linux_restore.ps1`, then `scripts\open_page.ps1` and F5 | `Linux link UP`; the header's board time kept counting, so the MCU was not reset | 15 s | "Restored without resetting the microcontroller." |
| 12 | **Conformance** (pre-recorded screenshot, or `build\b4_conformance` if time allows) | "Iteration 1: 9/9 predicted events matched exactly" | — | "The compiler predicted the board's exact log for a 50 s scenario, tick by tick, and the board's page checked it." |

## Fallback ladder

1. **The page doesn't open:** run `scripts\open_page.ps1` again, then
   `adb forward tcp:7000 tcp:7000`.
2. **The App is stuck:** `adb shell arduino-app-cli app stop user:iotc_b2_board`, then deploy again
   (about 1 min).
3. **The board fails:** run `py -3.13 tasks.py virtual`. This is the same App and page on a
   virtual board, marked VIRTUAL BOARD. Steps 5–9 work the same.
4. **The laptop UI fails:** deploy `build\backup_mcu_only`. The LEDs and the matrix play the 50 s
   tour by themselves (timeline in `docs/MANUAL.md` Part C).
5. **Last resort:** the Wokwi sketch (`wokwi/README.md`) or the recorded video.

**Never press App Lab's Stop button during the failsafe part:** it resets the microcontroller.

## Likely questions (short answers)

- **Why is this a compiler and not a template?** Analyses over the whole program (intervals,
  closures, placement, proofs) decide what code is generated and where. Invalid programs are
  rejected, and thresholds are compiled to exact integer tests.
- **Why AND only?** Every condition is then a box, so overlap, emptiness and gaps are exact. An OR
  is two rules.
- **Why integer ADC codes?** The board only ever sees 1024 values. Comparing codes makes every
  implementation agree exactly. Floats disagree at the boundary.
- **How do you know the generated code is right?** Golden files per stage, the generated C++
  compiled and compared with the interpreter on random traces, the generated Python against the
  same model, and conformance on the board.
- **What if Linux is alive but stops getting data?** The heartbeat acknowledges the last sample.
  A stale acknowledgement also takes the link down.
- **Can Linux switch the heater on while the fan is on?** No. Linux only sends rule flags; the MCU
  owns the pins and the safety monitor.
