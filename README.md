# iotc — IoT Sensor DSL Compiler for the Arduino UNO Q

Compiler Design mini project

`iotc` compiles one `.iot` rule file into a complete Arduino UNO Q App:
- an MCU sketch (STM32U585);
- a Linux Python program (QRB2210);
- the Bridge glue between them;
- a live web dashboard.

Before it generates any code, it analyses the rules. It finds rules that can never fire,
conflicting rules and missing hysteresis. It proves or enforces safety properties, and it decides
which processor runs each rule.

```
SENSOR temp AT A0 ANALOG UNIT C RANGE -40..125;
ACTUATOR fan AT LED4B;
RULE cool REALTIME 20ms: WHEN temp > 35 THEN fan ON;     // compiled to: code >= 466
SAFETY ENSURE fan ON WHEN temp > 50;
```

## Documents

| Document | Contents |
|---|---|
| **[docs/REPORT.md](docs/REPORT.md)** | Project report: architecture, language, grammar, the five stages, verification, board results |
| [docs/evidence/](docs/evidence/) | One artifact per stage: source, tokens, AST, check report, error messages, IR, generated code, simulation logs, conformance prediction, test inventory, board results |
| [docs/DEMO_CARD.md](docs/DEMO_CARD.md) | The 10-minute demo, step by step, with fallbacks and likely questions |
| [docs/SLIDES.md](docs/SLIDES.md) | Slide content for the presentation |
| [docs/MANUAL.md](docs/MANUAL.md) | How to run everything, including the board steps |
| [Architecture.md](Architecture.md) | Normative specification: language, diagnostics, IR, runtime semantics, design decisions |

## Implementation design → where to look

| Stage | Command | Evidence |
|---|---|---|
| 1. Grammar | `py -3.13 -m iotc dump examples/greenhouse_board.iot --stage tokens` (also `--stage ast`) | `docs/evidence/stage1_*` |
| 2. Threshold/type checks | `py -3.13 -m iotc check examples/greenhouse_board.iot` | `docs/evidence/stage2_*` |
| 3. Event IR | `py -3.13 -m iotc dump examples/greenhouse_board.iot --stage ir` | `docs/evidence/stage3_ir.txt` |
| 4. Target code | `py -3.13 -m iotc build examples/greenhouse_board.iot --input sim --demo-controls --scenario scenarios/fallback_sweep.csv -o build/b2_board` | `docs/evidence/stage4_target/` |
| 5. Simulation | `py -3.13 -m iotc run examples/greenhouse_board.iot --trace tests/traces/demo_script.csv --kill-linux-at 15000 --revive-linux-at 20000` | `docs/evidence/stage5_*`, `board_results.md` |

## Quick start

You need Python 3.11 or later (3.13 recommended) and pytest.

```powershell
py -3.13 tasks.py test        # 685 tests
py -3.13 tasks.py virtual     # the full App and dashboard on a virtual board, no hardware needed
py -3.13 tasks.py evidence    # regenerate docs/evidence/
py -3.13 tasks.py             # list all tasks
```

Optional tools:
- WSL with `g++`: the differential and conformance tests of the generated C++.
- `node`: the page-script syntax check.
- `adb` (`winget install Google.PlatformTools`): deploying to the board.

`py -3.13 -m iotc doctor` checks them all, as well as the board.

## On the board

`docs/MANUAL.md` covers deployment, the dashboard over USB (`scripts\open_page.ps1`), the
failsafe tests and conformance mode.

Never use App Lab's Stop button to show "Linux died": it resets the MCU. Use
`scripts\linux_pause.ps1` or `scripts\linux_kill.ps1`, and restore with
`scripts\linux_restore.ps1`.

## Status

Feature-complete. Board steps B0–B2 and the first B3 failsafe run are done: the measured failsafe
was 2.0 s against a prediction of 1.5–2.1 s. The rest of B3 (`docker pause`/`kill`) and the
on-board conformance run (B4) are pending. See `docs/evidence/board_results.md`.
