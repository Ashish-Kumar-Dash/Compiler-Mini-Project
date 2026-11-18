# IoT Sensor DSL Compiler — Project Architecture

Nov 13, 2026 · @Ash

## Overview

The compiler turns one `.iot` rule file into an Arduino C++ sketch that runs unchanged in Wokwi on a simulated Arduino Uno (core). As a stretch goal it generates a complete Arduino App Lab App for the UNO Q: the microcontroller (MCU) sketch, the Linux Python program, the Bridge code between them, and a simulation page. Its novel part is **placement analysis**: the compiler decides which processor runs each rule from what the rule depends on, and rejects rules whose timing cannot be met.

**Goals**

- Cover the course outcomes: recognize tokens, build an AST, enforce domain rules, generate executable output.
- Catch rule bugs at compile time: unreachable rules, conflicts, missing hysteresis, unit and pin errors.
- Enforce declared `SAFETY` properties at runtime on the MCU, which owns every pin.
- Generate Arduino C++ on the standard API and run it in Wokwi, which meets the problem statement's Arduino C++ target with no hardware. Running on the UNO Q is a stretch goal.

| Item | Tier |
| --- | --- |
| Lexer, parser, AST, semantic checks with error codes | Core |
| Units, ranges, pin maps for the Uno and the UNO Q | Core |
| Interval analysis: unreachable, conflicts, hysteresis | Core |
| Placement and safety analysis, printed as reports | Core |
| Safety monitor for `NEVER` and `ENSURE` in the generated sketch | Core |
| `mcu-only` back end: one Arduino sketch, run in Wokwi | Core |
| Reference interpreter and trace fuzzer | Core |
| Linux Python back end and Bridge contract | Stretch |
| Sim mode: web sliders replace sensors | Stretch |
| App Lab App deployed on the UNO Q | Stretch |
| Heartbeat `FAILSAFE` | Stretch |
| `OR` / `NOT` in conditions (write two rules instead) | Out of scope |
| Static proof of `ENSURE` (enforced at runtime only) | Out of scope |
| Replaying traces on the board against the interpreter | Out of scope |
| Camera sensors, hot-reloading rules | Out of scope |
| Full dashboard | Out of scope |
| Sending `NOTIFY` off the board (the core prints it to `Serial`) | Out of scope |
| Non-binary actuators (servo angles, PWM) | Out of scope |

## The DSL

A program declares sensors and actuators, then rules, safety properties and failsafe states. This is the reference program used in tests and the demo:

```
SENSOR   temp     AT A0 ANALOG UNIT C   RANGE -40..125;
SENSOR   humidity AT A1 ANALOG UNIT PCT RANGE 0..100;
ACTUATOR fan      AT D7;
ACTUATOR heater   AT D8;

RULE cool   REALTIME 10ms: WHEN temp > 35 THEN fan ON;
RULE uncool REALTIME 10ms: WHEN temp < 30 THEN fan OFF;
RULE warm:   WHEN avg(temp, 5min) < 18 THEN heater ON;
RULE unwarm: WHEN avg(temp, 5min) > 22 THEN heater OFF;
RULE alert:  WHEN temp > 45 FOR 10s THEN NOTIFY "Overheat";

SAFETY NEVER heater ON AND fan ON;
SAFETY ENSURE fan ON WHEN temp > 50;
FAILSAFE heater OFF;   // applied if the Linux side stops
```

**Tokens**

| Token | Pattern | Examples |
| --- | --- | --- |
| Keyword (uppercase, reserved) | `SENSOR ACTUATOR AT ANALOG DIGITAL UNIT RANGE RULE REALTIME WHEN AND FOR THEN ON OFF NOTIFY SAFETY NEVER ENSURE FAILSAFE` | `WHEN` |
| Aggregate (reserved) | `avg` `min` `max` | `avg` |
| Identifier | `[a-z_][a-z0-9_]*` | `temp`, `heater` |
| Number | `-?[0-9]+(\.[0-9]+)?` | `35`, `-40`, `22.5` |
| Duration | number glued to `ms`, `s`, `min`, `h`; stored as ms | `10ms`, `5min` |
| Unit (reserved) | `C F PCT LUX CM`, `%` as alias of `PCT` | `C` |
| Pin | `A0`–`A5`, `D0`–`D13`, `LED3`, `LED4` | `A0` |
| String | `"…"`, no newlines | `"Overheat"` |
| Operator | `> < >= <= == != ..` | `>=` |
| Punctuation | `: ; ( ) ,` | `;` |
| Comment | `//` to end of line, skipped |  |

**Grammar (EBNF)**

```
program   = { decl } ;
decl      = sensor | actuator | rule | safety | failsafe ;
sensor    = "SENSOR" IDENT "AT" PIN kind "UNIT" unit "RANGE" num ".." num ";" ;
kind      = "ANALOG" | "DIGITAL" ;
actuator  = "ACTUATOR" IDENT "AT" PIN ";" ;
rule      = "RULE" IDENT [ "REALTIME" DURATION ] ":" "WHEN" cond
            [ "FOR" DURATION ] "THEN" action ";" ;
cond      = cmp { "AND" cmp } ;
cmp       = value relop num [ unit ] ;
value     = IDENT | agg "(" IDENT "," DURATION ")" ;
action    = IDENT state | "NOTIFY" STRING ;
state     = "ON" | "OFF" ;
safety    = "SAFETY" ( "NEVER" IDENT state "AND" IDENT state
                     | "ENSURE" IDENT state "WHEN" cond ) ";" ;
failsafe  = "FAILSAFE" IDENT state ";" ;
```

**Language decisions**

- **`AT` binds pins, `ON` is a state.** Using `ON` for both would make the grammar ambiguous at `fan ON`.
- **Conditions are `AND`-only.** Each condition is then one interval per variable, which keeps every analysis exact. `OR` is written as two rules.
- **Rules are level-triggered.** Every evaluation cycle, each rule whose condition holds requests its action. An actuator keeps its last state when no rule fires, which is what makes hysteresis work.
- **Conflicts resolve to `OFF`.** The compiler rejects provable conflicts; if two requests still meet at runtime, `OFF` wins.
- **`FOR d`** means the condition held continuously for `d`.
- **Units are optional on numbers** and default to the sensor's unit. `95 F` against a `C` sensor converts at compile time; a different dimension is an error.

## Compiler pipeline

The compiler is a classic three-part pipeline; the novel work sits in the middle end, where the analyses decide what runs where before any code is generated.

&#91;embedded content: compiler pipeline · front end, middle end, back ends\]

The front end checks the program, the middle end lowers it to Rule IR and analyses it, and the back ends write an Arduino sketch (core) or the App Lab App folder (stretch). The interpreter runs the same IR, so tests and fuzzing need no board.

## Front end and semantic analysis

The front end turns source text into a checked AST and rejects any program that names something undeclared, mixes units or misuses a pin. Every diagnostic carries a code, a `line:column` span and, where possible, a fix hint.

**Lexer.** Hand-written, one pass, tracks line and column. Durations are normalised to milliseconds. Errors: `L001` unknown character, `L002` unterminated string, `L003` bad duration suffix.

**Parser.** Recursive descent, one function per grammar rule, one token of lookahead. On an error it skips to the next `;` and continues, so one run reports every error. Error: `P001` expected X, found Y.

**AST nodes**

| Node | Fields |
| --- | --- |
| `Program` | sensors, actuators, rules, safeties, failsafes |
| `SensorDecl` | name, pin, kind, unit, lo, hi, span |
| `ActuatorDecl` | name, pin, span |
| `Rule` | name, deadline\_ms?, guard: list of `Cmp`, hold\_ms?, action, span |
| `Cmp` | value (`SensorRef` or `Agg`), op, number, unit? |
| `Agg` | fn (avg, min, max), sensor, window\_ms |
| `Action` | `SetState(actuator, state)` or `Notify(text)` |
| `Never` | (actuator, state), (actuator, state) |
| `Ensure` | actuator, state, guard |
| `Failsafe` | actuator, state |

**Semantic checks**

| Code | Check | Triggering example |
| --- | --- | --- |
| `S001` | Undeclared name, with a did-you-mean suggestion (edit distance ≤ 2) | `WHEN temprature > 35` |
| `S002` | Duplicate declaration | two `SENSOR temp` lines |
| `S003` | Wrong kind of name | `WHEN fan > 35`, `THEN temp ON` |
| `S010` | Unit of a different dimension | `temp > 50 PCT` |
| `S011` | Number outside the sensor's `RANGE` (warning) | `temp > 200` |
| `S012` | Empty or inverted `RANGE` | `RANGE 125..-40` |
| `S020` | Pin does not exist on the target board (Uno or UNO Q) | `AT D22` |
| `S021` | Pin bound twice | `fan` and `heater` both `AT D7` |
| `S022` | `ANALOG` sensor on a non-analog pin, or a sensor on `LED3`/`LED4` | `AT D4 ANALOG` |
| `S023` | Serial pin `D0`/`D1` used (warning) | `AT D0` |
| `S030` | Aggregation window of zero | `avg(temp, 0s)` |

**Units.** Each unit belongs to one dimension: temperature (`C`, `F`), ratio (`PCT`), illuminance (`LUX`), distance (`CM`). Every threshold is converted to the sensor's declared unit during checking, so later stages never see units.

**Diagnostic format**

```
error[S001]: unknown sensor 'temprature'
  --> greenhouse.iot:6:17
   |
 6 | RULE cool: WHEN temprature > 35 THEN fan ON;
   |                 ^^^^^^^^^^ did you mean 'temp'?
```

## Rule IR and static analyses

Every analysis runs on a flat Rule IR in which each condition is a box: one interval per input variable. That makes reachability, conflict, hysteresis and placement checks exact and cheap.

**Rule IR**

```
IRRule(name, guard: {var: Interval}, hold_ms, action, deadline_ms, placement)
var      = a sensor name, or a windowed value such as avg(temp, 300000)
Interval = (lo, hi) with open or closed ends; unmentioned vars = full sensor RANGE
```

`temp > 35` lowers to `(35, 125]`; several comparisons on one variable intersect. A windowed value gets its own variable with the sensor's range and is treated as independent of the raw reading. This over-approximates: it can raise a warning that never happens in practice, but it never misses a real one.

**Interval analysis**

| Code | Finding | Detected when |
| --- | --- | --- |
| `W101` | Rule can never fire | an interval is empty after intersecting with the sensor range |
| `W102` | Rule always fires | every interval equals the full range |
| `E103` | Conflict over an actuator | two rules set opposite states and their boxes overlap; the report prints the overlap, e.g. `temp ∈ (30, 35]` |
| `W104` | No hysteresis, so the actuator will chatter | ON threshold equals OFF threshold |
| `I105` | Hysteresis gap (info) | ON at `x > a`, OFF at `x < b`, prints the gap `a − b` |
| `W106` | Actuator switched ON but never OFF | no OFF rule and no `FAILSAFE` |

**Placement analysis.** The compiler infers each rule's effects, then places it:

| Rule uses | Runs on | Reason printed |
| --- | --- | --- |
| `REALTIME` deadline | MCU | deadline needs the processor that owns the pins |
| `SAFETY`, `FAILSAFE` | MCU, always | MCU can veto any pin write |
| `avg` / `min` / `max` window | Linux | sample history is kept on Linux |
| `FOR` duration | Linux | hold timers run with the windowed state |
| `NOTIFY` | Linux | output goes to the page and log |
| none of the above | MCU | default: no Bridge hop |

A `REALTIME` rule that needs a Linux-only feature is an error, because the time to cross the Bridge is not bounded:

```
error[P201]: REALTIME rule 'x' depends on avg(temp, 5min), which is only computed on Linux
  --> greenhouse.iot:9:31
```

Sensors are always read on the MCU. If any Linux rule uses a sensor, the MCU streams its samples to Linux at a fixed period (compiler option `--sample-ms`, default 100). The report prints the placement table:

```
rule    runs on  reason
cool    MCU      REALTIME 10ms
warm    Linux    avg() needs sample history
alert   Linux    NOTIFY, FOR
SAFETY  MCU      MCU owns the pins and can veto any write
```

Placement is computed for the UNO Q target. The core `mcu-only` build places every rule on the MCU, so `P201` is reported only with `--target unoq`; the core still prints the table as the plan for that target.

**Safety analysis**

- `NEVER a s1 AND b s2`: the compiler collects every rule that sets `a` to `s1` and every rule that sets `b` to `s2`. If every pair of boxes is disjoint, the property is reported **proven**. Otherwise it is reported **not proven, enforced at runtime**, with the overlapping region as a witness.
- `ENSURE`: always enforced at runtime by the monitor; the compiler checks names and warns if no rule ever sets that state.

```
⚠ NEVER heater ON AND fan ON   not proven: avg(temp) < 18 and temp > 35 can both hold
                               → enforced by the MCU monitor
✓ ENSURE fan ON WHEN temp > 50 enforced by the MCU monitor
```

## Back ends and runtime on the UNO Q

The core build is `--target mcu-only`, described in the last MCU bullet below. Everything else in this section describes the UNO Q App, a stretch goal.

The generated App follows one rule: **Linux proposes, the MCU decides.** The MCU owns every pin and runs the safety monitor; Linux-side rules can only ask for actuator changes through the Bridge, and the MCU never waits on Linux.

&#91;embedded content: runtime on the UNO Q · Linux side and MCU side\]

Linux rules only request writes; the MCU's monitor applies or refuses each one, and every message from the MCU to Linux is send-and-forget.

**MCU back end (`sketch/sketch.ino`)**

- Pin setup from the declarations. A generated `writePin()` helper hides the active-low onboard LEDs (`LOW` = on).
- `loop()`: read sensors (or sim values) → evaluate MCU rules in source order → pass every requested write through `monitor()` → every sample period, `notify` samples to Linux → check the heartbeat (if enabled).
- `monitor(actuator, state)`: refuses a write that would break a `NEVER` pair, forces the state an `ENSURE` requires whenever its condition holds, and reports each write or refusal to Linux.
- `REALTIME` deadlines: the sketch measures each `loop()` pass with `micros()` and counts overruns, which are reported with the samples.
- MCU-to-Linux messages use `notify` (send and forget), never `call`, so a stalled Linux side cannot block the MCU.
- **`--target mcu-only` (core).** Every rule runs on the MCU and the output is one sketch on the standard Arduino API, with no Bridge. Each window becomes a ring buffer of 32 buckets (sum, min, max, count), so `avg(temp, 5min)` fits in the Uno's 2 KB of RAM; `FOR` becomes a `millis()` timer; `NOTIFY` prints to `Serial`. The sketch runs unchanged in Wokwi.

**Linux back end (`python/main.py`)**

- Keeps a ring buffer per sensor from the streamed samples to compute `avg`, `min` and `max` windows.
- Evaluates Linux rules on each sample, runs `FOR` hold timers, and sends `NOTIFY` text to the page and log.
- Sends each Linux rule's action as a `request` to the MCU and shows whether it was applied or refused.
- In sim mode, serves the sim page through the Web UI brick and forwards slider values to the MCU.

**Bridge contract.** Both sides are generated from one table inside the compiler, so names and arguments cannot drift apart.

| Name | Direction | Kind | Payload | Purpose |
| --- | --- | --- | --- | --- |
| `sample` | MCU → Linux | notify | sensor id, value, overruns | feeds windows and the page |
| `event` | MCU → Linux | notify | actuator, state, applied or refused, rule id | page log |
| `request` | Linux → MCU | call, handled with `provide_safe` | actuator, state, rule id | Linux rule asks for a write |
| `sim_set` | Linux → MCU | call, handled with `provide_safe` | sensor id, value | sim mode only |
| `heartbeat` | Linux → MCU | call, handled with `provide_safe` | counter | failsafe watchdog |

`provide_safe` handlers run between two passes of `loop()`, so a request from Linux never interrupts the monitor partway through a check.

**Failsafe (stretch).** Linux sends a heartbeat every 500 ms. If none arrives within the timeout (compiler option, default 2 s), the MCU applies every `FAILSAFE` state and refuses Linux requests until heartbeats resume. MCU rules keep running.

**Sim mode (`--sim`).** Sensor reads come from values set on the sim page instead of pins. The page is generated from the declarations: one slider per sensor (limits from its `RANGE`), one indicator per actuator, and a log of rules firing, writes refused and `NOTIFY` messages.

**App folder.** The compiler writes a folder App Lab can open directly:

```
build/greenhouse/
  app.yaml                 # copied from a built-in Web UI example; compiler sets the name
  sketch/sketch.ino
  sketch/sketch.yaml
  python/main.py
  assets/index.html        # sim page
```

The templates in `backend/templates/` are taken from a built-in App Lab example that uses both the Bridge and the Web UI brick, so library names, imports and the `app.yaml` brick format match what App Lab expects.

## Reference interpreter and trace fuzzer

A Python interpreter executes the Rule IR with the same semantics as the generated code, so behaviour and safety can be tested on a laptop without the board.

**Interpreter semantics.** Time advances in ticks of one sample period. Each tick:

1. Read the next sensor values from the trace and update the windows, using the same 32 buckets as the `mcu-only` sketch so both lag alike.
2. Evaluate MCU rules, then Linux rules, using the same level-triggered and `OFF`-wins rules as the language spec.
3. Pass every write through the same monitor logic as the sketch (`NEVER`, `ENSURE`).
4. Record each applied or refused write.

On the board, Linux requests arrive asynchronously. The interpreter models them as landing in the same tick; this one-tick model is stated in the report so fuzz results are not overclaimed.

**Formats**

```
# trace.csv (input)
t_ms,temp,humidity
0,25.0,40
100,25.4,40

# events.csv (output)
t_ms,actuator,state,rule,result
4200,fan,ON,cool,applied
```

**Trace fuzzer (`iotc fuzz`)**

- Generates random traces inside each sensor's `RANGE`: random walks, sudden spikes, and plateaus just above and below every threshold in the program.
- Runs N traces (default 10,000, fixed `--seed` for reproducibility) and checks invariants on every tick:
  - no `NEVER` pair is ever both in its forbidden states;
  - every `ENSURE` state holds within one tick of its condition becoming true;
  - no actuator toggles more than a set number of times in a window (catches chatter that hysteresis analysis should have flagged).
- On a violation, shrinks the trace by deleting segments while it still fails, then saves it to `tests/traces/` as a regression test.
- Self-check: running with the monitor disabled must produce a violation, which proves the fuzzer can find a real bug.

## Repository layout and CLI

The compiler is one Python package, `iotc`, with one module per pipeline stage so each stage can be tested on its own.

```
iot-dsl/
  iotc/
    tokens.py  lexer.py
    ast.py     parser.py
    sema/      symbols.py  units.py  pins.py  checker.py
    ir.py      lower.py              # AST → Rule IR
    analysis/  intervals.py  placement.py  safety.py  report.py
    backend/   mcu_cpp.py  linux_py.py  bridge.py  simpage.py  app_writer.py
               templates/            # from a built-in App Lab example
    interp.py  fuzz.py
    diagnostics.py  cli.py
  examples/    greenhouse.iot  conflicts.iot  errors/*.iot
  tests/       test_lexer.py  test_parser.py  test_sema.py  test_analysis.py
               test_backend.py  test_interp.py  golden/  traces/
  README.md
```

| Command | What it does |
| --- | --- |
| `iotc check file.iot` | Front end and analyses; prints diagnostics, placement table and safety report |
| `iotc build file.iot --target unoq -o build/app [--sim]` | Writes the App Lab App folder (stretch) |
| `iotc build file.iot -o out --target mcu-only` | Core build: a plain Arduino sketch that runs in Wokwi |
| `iotc run file.iot --trace trace.csv` | Runs the interpreter and prints the event log |
| `iotc fuzz file.iot -n 10000 --seed 42` | Fuzzes `SAFETY` properties with random traces |

Every command takes `--board uno` (default) or `--board unoq`, which picks the pin map used by the `S02x` checks.

Exit code is 0 for success or warnings only, 1 if any error was reported.

## Testing strategy

Every stage is tested without hardware; Wokwi runs the generated sketch, and the board is only needed for the stretch smoke test.

| Layer | Tests | Pass condition |
| --- | --- | --- |
| Lexer | Token stream for each example file | Matches golden token list |
| Parser | AST snapshot per example; one file per syntax error | AST matches; each error gives the expected code and `line:col` |
| Semantic checks | One `.iot` file per `S0xx` code in `examples/errors/` | Exactly that code, at that span |
| Analyses | Small programs for unreachable, always-true, conflict, zero hysteresis, gap, `NEVER` proven and not proven, `P201` | Expected findings, including the printed overlap region |
| Back ends | Generated `sketch.ino` and `main.py` for each example | Diff-equal to golden files; `mcu-only` output compiles with `arduino-cli` |
| Interpreter | Hand-written traces with expected event logs | Event log matches |
| Fuzzer | Seeded run on `greenhouse.iot`; run with monitor disabled | 0 violations; disabled monitor is caught and the trace is shrunk |
| Wokwi | `mcu-only` sketch of `greenhouse.iot` in Wokwi on an Arduino Uno | Potentiometer above 35 turns the `D7` LED on, below 30 turns it off; a `NEVER` refusal appears on the serial monitor |
| Board (stretch) | `--target unoq --sim` build deployed to the UNO Q | Same checks, with the refusal shown in the page log |

## Hardware mapping

The core demo runs in Wokwi on a simulated Arduino Uno: potentiometers stand in for the sensors and LEDs for the actuators. The same pins work on the UNO Q for the stretch demo.

| DSL name | Pin | Wokwi Uno (core) | UNO Q (stretch) |
| --- | --- | --- | --- |
| `fan` | `D7` | LED with a 220 Ω resistor to GND | LED on `D7`, or rebind to onboard `LED3` (active-low) |
| `heater` | `D8` | LED with a 220 Ω resistor to GND | LED on `D8`, or rebind to onboard `LED4` (active-low) |
| `temp` | `A0` | Potentiometer | Potentiometer or sim slider |
| `humidity` | `A1` | Potentiometer | Sim slider |

- Analog reads scale linearly from the ADC range to the sensor's `RANGE`: 0–1023 on the Uno (10-bit). Check the UNO Q's resolution with a test sketch.
- Each LED's colour channel pins are the offsets from `LED_BUILTIN` confirmed with the LED test sketch; the compiler keeps them in `pins.py`.
- **Optional potentiometer** on `A0` (outer legs to 3.3 V and GND, middle leg to `A0`) gives a physical "temperature" that keeps working when the Python side is stopped.
- Real loads such as a fan go through a relay or transistor module, never straight from a pin.
- The Linux-side high-speed header pins run at 1.8 V and are not used.

## Milestones, risks and fallbacks

The build order puts everything that can be tested on a laptop first, so the project is complete and gradable before any hardware work starts. Milestones 1–5 are the core; 6 and 7 are stretch goals.

1. **Language and front end.** Exit: every example parses; every `L`, `P` and `S` code has a test that passes.
2. **Rule IR, interval analysis, report.** Exit: `conflicts.iot` reports `E103` with its overlap region; hysteresis gap and `W104` both shown.
3. **Placement and safety analysis.** Exit: placement table for `greenhouse.iot`; `P201` on the bad example; one proven and one unproven `NEVER`.
4. **Interpreter and fuzzer.** Exit: golden traces pass; fuzzer catches the disabled-monitor bug and shrinks the trace.
5. **`mcu-only` back end in Wokwi.** Exit: the Wokwi test in the testing table passes.
6. **Stretch: UNO Q App.** Linux back end, Bridge contract, sim mode and App folder. Exit: the board test in the testing table passes.
7. **Heartbeat failsafe (stretch).** Exit: stopping `main.py` turns the heater off within the timeout while the `cool` rule still responds to the potentiometer.

| Risk | Effect | Mitigation or fallback |
| --- | --- | --- |
| Bridge API names or `app.yaml` format differ from assumptions | Generated App does not start | Build templates from a built-in App Lab example that uses Bridge and Web UI before writing the back ends |
| Bridge latency unknown | Placement reasons lack a measured figure | Measure `call` and `notify` round trips on the board in milestone 5; print the figure in the report |
| App Lab deploys slowly or hangs | Slow iteration on the board | Copy the App folder over SSH and start it with `arduino-app-cli`; restart App Lab when it hangs |
| Unclear whether App Lab restarts a stopped `main.py` | Failsafe demo may not behave as planned | Check before milestone 6 |
| ADC resolution differs from the assumed scaling | Potentiometer readings map to wrong temperatures | Print raw values with a test sketch; scaling is one constant in `pins.py` |
| Live demo fails | Nothing to show on the board | `--target mcu-only` sketch in Wokwi, plus a recorded video of the full demo |
| Bucketed windows lag the exact value by up to one bucket | A window rule fires a few seconds late | The interpreter uses the same buckets; the lag is stated in the report |
| No internet in the demo room | Wokwi does not load | Recorded video; the sketch also runs on any physical Uno |

## Demo script

The demo shows the compiler's analyses first and the board last, so the strongest compiler work is seen even if the hardware misbehaves. Steps 1–6 are the core demo; steps 7–10 need the UNO Q stretch build.

1. Show `greenhouse.iot` and run `iotc check`: walk through the placement table and the safety report.
2. Run the invalid programs: a typo (`S001` with its suggestion), a pin clash (`S021`), a unit mismatch (`S010`), and a placement error (`P201` with `--target unoq`).
3. Run `iotc fuzz`: 10,000 traces, 0 violations. Rerun with the monitor disabled to show a violation and its shrunk trace.
4. Run `iotc build greenhouse.iot --target mcu-only`, paste the sketch into Wokwi (Arduino Uno, potentiometers on `A0` and `A1`, LEDs on `D7` and `D8`) and start it.
5. Turn the `A0` potentiometer: the `D7` (fan) LED turns on above 35 and stays on until the value drops below 30, so the hysteresis is visible.
6. With a demo copy that uses a 10 s window, hold the potentiometer low until the `D8` (heater) LED turns on, then turn it above 35: the serial monitor shows the fan write refused by `NEVER`. Turn it past 50 and the `ENSURE` forces the fan on and the heater off.
7. Run `iotc build --target unoq --sim`, open the folder in App Lab and click Run.
8. Drag the temperature slider: LED3 (fan) turns on above 35 and stays on until the value drops below 30, so the hysteresis is visible.
9. Push the program into the heater-and-fan overlap: the MCU refuses the heater write and the page log shows the refusal. Use a demo copy of the file with a 10 s window instead of 5 min so this happens in seconds.
10. Optional: stop `main.py`. The heater drops to its `FAILSAFE` state, and the `cool` rule still responds to the potentiometer, because sim sliders stop with the Python side.
