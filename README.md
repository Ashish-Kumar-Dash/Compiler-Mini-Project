# iotc — IoT Sensor DSL Compiler for the Arduino UNO Q

`iotc` compiles one `.iot` rule file into a complete Arduino UNO Q App: an MCU sketch
(STM32U585), a Linux Python program (QRB2210), the Bridge glue between them and a live web
dashboard. Before generating code, it analyses the rules: it finds rules that can never fire,
conflicts and missing hysteresis, proves or enforces safety properties, and decides which
processor runs each rule.

```
SENSOR temp AT A0 ANALOG UNIT C RANGE -40..125;
ACTUATOR fan AT LED4B;
RULE cool REALTIME 20ms: WHEN temp > 35 THEN fan ON;     // compiled to: code >= 466
SAFETY ENSURE fan ON WHEN temp > 50;
```

The design and the normative semantics are in **[Architecture.md](Architecture.md)** (v3).

## Status

| Stage | State |
|---|---|
| Architecture v3, repo skeleton, frozen IR, Phase 0 probe Apps | done |
| Front end: lexer, parser, diagnostics (every L/P code) | done |
| Semantic checks (every S code), ADC-code lowering, placement | done |
| Analyses: intervals, safety proofs, resources, `iotc check` report | done |
| Reference interpreter, shared runtime, LED-matrix renderer, `iotc run` | done |
| MCU back end (C++ sketch), `iotc build`, differential test vs interpreter | done |
| Linux back end, dashboard, fuzzer | in progress (Architecture §15) |

Try it:

```powershell
py -3.13 -m iotc check examples/greenhouse_board.iot
py -3.13 -m iotc run examples/greenhouse_board.iot --trace tests/traces/demo_script.csv --kill-linux-at 15000 --revive-linux-at 20000
py -3.13 -m iotc matrix-preview examples/greenhouse_board.iot --trace tests/traces/demo_script.csv --kill-linux-at 15000 --at 16700
```

## Quick start (development)

Requires Python ≥ 3.11 (3.13 recommended) and pytest.

```powershell
py -3.13 tasks.py test        # run the test suite
py -3.13 tasks.py             # list all tasks
```

Optional tools: WSL with `g++` (differential tests), `arduino-cli` (compile checks), and `adb`
(`winget install Google.PlatformTools`, used to deploy to the board).

## Board (Phase 0)

See [docs/phase0_checklist.md](docs/phase0_checklist.md). In short:

```powershell
scripts\versions.ps1
scripts\pull_templates.ps1
scripts\deploy.ps1 hardware\phase0\probe
```

Never use App Lab's Stop button to demonstrate "Linux died": it resets the MCU. Use
`scripts\linux_pause.ps1` / `scripts\linux_kill.ps1`, and restore with `scripts\linux_restore.ps1`.
