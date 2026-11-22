# iotc: an IoT Sensor DSL Compiler for the Arduino UNO Q

**Project report — Compiler Design mini project, B.Tech Year 3**

| | |
|---|---|
| Author | Ashish Kumar Dash |
| Roll number / team / guide | *[fill in]* |
| Institution | *[fill in]* |
| Date | November 2026 |
| Software | `iotc` 0.3.0 (Python 3.13); repository root contains every artifact cited here |

---

## Abstract

`iotc` compiles a small domain-specific language (DSL) for sensor rules into a complete
application for the Arduino UNO Q. The UNO Q has two processors: a real-time microcontroller
(MCU) and a Linux processor, joined by a message bridge. A program declares sensors, actuators
and rules such as `WHEN temp > 35 THEN fan ON`. It can also declare safety properties: two
actuators must never be ON together, one actuator must be ON in a condition, and a failsafe state
applies when Linux dies.

The compiler is a full pipeline: a hand-written lexer and an LL(1) recursive-descent parser with
error recovery; semantic checks for names, units, pins and thresholds; and lowering to an event
IR in which every condition is a *box* of intervals and every threshold is an exact integer test
on the 10-bit ADC code. Analyses run over the IR. They detect conflicting rules, missing
hysteresis and rules that can never fire, prove or enforce safety properties, and decide which
processor runs each rule. Two back ends generate a C++ sketch for the MCU and a Python program
for Linux, plus the Bridge glue and a live web dashboard.

Correctness is checked at three levels. A reference interpreter defines the semantics. The
generated C++ is compiled on the host and compared with the interpreter (differential testing).
On the board, a *conformance mode* has the compiler predict the exact event log, which the
board's own page then checks line by line. The project has 685 automated tests. It was
demonstrated on a real UNO Q, where it measured a 2.0 s failsafe reaction against a predicted
range of 1.5–2.1 s.

---

## Contents

1. Introduction
2. Background: the target platform
3. System overview and the five-stage design
4. Stage 1 — Language, tokens and grammar
5. Stage 2 — Semantic, unit and threshold checks
6. Stage 3 — Event IR and program analyses
7. Stage 4 — Target code generation
8. Runtime semantics
9. Stage 5 — Simulation and verification
10. Results on the board
11. Demonstration
12. Limitations and future work
13. Conclusion
14. References

Appendix A: the demo program. Appendix B: diagnostic catalogue. Appendix C: how to run.
Appendix D: repository layout.

---

## 1. Introduction

### 1.1 Problem

Small automation systems such as greenhouses, cold rooms and door alarms are usually written by
hand as C++ sketches. The rules are simple, but the hand-written code tends to fail in the same
few ways:

- **Conflicting rules.** One rule turns the fan on above 35 °C and another turns it off below 40 °C.
  Between 35 and 40 the fan chatters.
- **No hysteresis.** The ON and OFF thresholds touch, so sensor noise toggles the output.
- **Floating-point boundaries.** A comparison such as `temp > 35` is computed differently on the
  microcontroller (float32), in Python (float64) and in a simulator. Near the threshold they
  disagree.
- **Safety by convention.** "The heater and the fan must never run together" lives in a comment,
  not in the code.
- **Two processors.** On boards that pair an MCU with Linux, someone must decide which rule runs
  where, and what happens when the Linux side crashes.

### 1.2 Objective

Build a compiler, not a code template. The compiler should read a declarative rule program, reject
or warn about bad programs with precise messages, prove what can be proved statically, and
generate correct code for both processors. The generated code should agree exactly with a
reference semantics, and the agreement should be demonstrated on real hardware.

### 1.3 Contributions

1. **A small, analysable language.** Conditions are conjunctions of comparisons, so each
   condition is a box (one interval per variable). Overlap, emptiness and hysteresis gaps are
   then exact interval operations.
2. **ADC-code lowering.** Every threshold is compiled to an exact integer test on the raw 10-bit
   code, using rational arithmetic. Example: `temp > 35` becomes `code ≥ 466`. The MCU, Linux and
   the interpreter therefore cannot disagree at a boundary.
3. **Static analyses** over the event IR: conflict detection (E103), hysteresis (W104/I105/W112),
   dead rules (W101/W113), ENSURE/FAILSAFE closures (E108/E109/E116), and a sound "proven" rule
   for NEVER properties.
4. **Placement.** Each rule is assigned to the MCU or to Linux, with a stated reason. Programs
   whose deadlines the board cannot meet are rejected (P201).
5. **A failsafe protocol.** An acknowledged heartbeat lets the MCU detect a dead or deaf Linux
   side and apply the declared FAILSAFE states within a bounded time.
6. **Verification at every level.** Golden files for every stage; differential testing of the
   generated C++ against the interpreter; and a conformance mode in which the compiler's
   prediction of the board's log is checked live on the board.

---

## 2. Background: the target platform

### 2.1 Arduino UNO Q

The UNO Q combines two computers on one board:

| Part | Role in this project |
|---|---|
| STM32U585 microcontroller (Arm Cortex-M33, Arduino core on Zephyr) | Owns the pins, runs the time-critical rules, enforces the safety monitor, drives the LEDs and the 8×13 LED matrix |
| Qualcomm QRB2210 processor running Debian Linux | Runs the latency-tolerant rules (windowed averages, notifications), serves the web dashboard |
| Bridge (an RPC router over an internal UART, msgpack-encoded) | Carries sensor samples MCU → Linux and heartbeats Linux → MCU |
| Onboard RGB LEDs (LED3, LED4 on the MCU; LED1, LED2 on Linux) and the LED matrix | Actuators and status display in the board-only demo |

Applications are *Apps* managed by Arduino App Lab. An App is a folder with a sketch, a Python
program (run in a Docker container on Linux) and optional "bricks" such as `web_ui`, which serves
a page on port 7000.

### 2.2 Platform facts measured before design (Phase 0)

Before writing code generators, we ran a hand-written probe App on the board (Section 10.1). Its
measurements changed the design:

- `Serial` on the UNO Q is routed over the Bridge, and `Serial.begin` blocked for 17 ms. The
  generated MCU loop therefore **never logs over Serial**. All events travel as Bridge notifies.
- A Bridge handler with a `u16` parameter **silently truncates** 70000 to 4464, and a `bool`
  parameter rejects the integer 1. The generated Python **range-checks and coerces every argument**
  before sending.
- The Bridge round trip had a median of 7.4 ms but a maximum of 101.6 ms. Its latency is
  **unbounded**, so a rule with a deadline may not depend on Linux (error P201).
- The MCU loop runs about 900 passes per second (median interval 1.15 ms). This sets the floor for
  `REALTIME` deadlines (W121 below 5 ms).

### 2.3 Compiler-construction background

The front end follows the classical structure: a lexical analyser, a predictive (LL(1))
recursive-descent parser and a semantic analyser over a symbol table [1, 4]. The analyses are
interval analyses, a simple instance of abstract interpretation [2] in which the abstract domain
is a box of intervals. They are exact here, because conditions are conjunctions of comparisons
on independent variables. Code generation is table-driven: the back ends emit constant tables and
a fixed runtime loop, not per-rule control flow. The generated code is verified by differential
testing [3].

---

## 3. System overview and the five-stage design

### 3.1 Pipeline

```
 .iot source
     │
     ▼
 ┌────────────┐  tokens  ┌────────────┐  AST  ┌───────────────────────┐
 │ Stage 1    │ ───────▶ │ Stage 1    │ ────▶ │ Stage 2               │
 │ lexer      │          │ LL(1)      │       │ semantic checks:      │
 │ (L codes)  │          │ parser     │       │ names, kinds, units,  │
 └────────────┘          │ (P001)     │       │ pins, ranges, windows │
                         └────────────┘       │ (S codes)             │
                                              └──────────┬────────────┘
                                                         │ checked AST
                                                         ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │ Stage 3  lowering → event IR (boxes + integer code tests)          │
 │          analyses: intervals, closures, safety proofs, placement,  │
 │          resources (W/E/I 1xx, P2xx)  →  `iotc check` report       │
 └───────────────────────────────┬───────────────────────────────────┘
                                 │ IR + runtime table
                ┌────────────────┼──────────────────────┐
                ▼                ▼                      ▼
 ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────────────┐
 │ Stage 4          │ │ Stage 4          │ │ Stage 5                  │
 │ MCU back end     │ │ Linux back end   │ │ reference interpreter,   │
 │ iotc_gen.h (C++) │ │ main.py + page   │ │ virtual board, predicted │
 └──────────────────┘ └──────────────────┘ │ board log (conformance)  │
                                           └──────────────────────────┘
```

### 3.2 Mapping to the implementation design

The project follows the five-stage implementation design from the proposal. Each stage has a
module, a command that prints its output, and a checked-in artifact in `docs/evidence/`.

| Stage | What it does | Modules | Command | Evidence file |
|---|---|---|---|---|
| 1. Grammar | Lex and parse the source; recover from errors | `source.py`, `lexer.py`, `tokens.py`, `parser.py`, `ast.py` | `iotc dump FILE --stage tokens` / `--stage ast` | `stage1_source.iot`, `stage1_tokens.txt`, `stage1_ast.txt` |
| 2. Threshold/type checks | Names, kinds, units, pins, ranges, windows; ADC thresholds | `sema/` (symbols, units, pins, checker), `adc.py` | `iotc check FILE` | `stage2_check_report.txt`, `stage2_error_messages.txt` |
| 3. Event IR | Lower to boxes and code tests; analyses; placement | `lower.py`, `ir.py`, `analysis/` | `iotc dump FILE --stage ir` | `stage3_ir.txt` |
| 4. Target code | C++ sketch, Linux program, Bridge glue, dashboard, App folder | `backend/` | `iotc build FILE -o DIR` | `stage4_target/` |
| 5. Simulation | Interpreter, LED-matrix preview, virtual board, conformance prediction, tests | `interp.py`, `runtime/`, `board_model.py`, `conformance.py`, `virtual_board.py` | `iotc run`, `iotc matrix-preview`, `iotc dashboard` | `stage5_*.txt`, `board_results.md` |

The required deliverables map as follows:

| Deliverable | Where it is |
|---|---|
| Input source | `examples/` (12 valid programs, 80 error programs) |
| Tokens / AST | `docs/evidence/stage1_tokens.txt`, `stage1_ast.txt` |
| Generated target | `docs/evidence/stage4_target/` |
| Execution evidence | `docs/evidence/stage5_*`, `docs/evidence/board_results.md` |

`py -3.13 tasks.py evidence` regenerates every artifact.

### 3.3 Size

| Part | Lines |
|---|---|
| Compiler, runtime and back ends (Python, including the C++ runtime template) | about 6,900 |
| Dashboard (HTML, CSS, JavaScript) | about 670 |
| Tests (Python) plus the C++ host shim | about 3,250 |
| Generated output for the demo program | `iotc_gen.h` 640 lines, `main.py` 396 lines |

---

## 4. Stage 1 — Language, tokens and grammar

### 4.1 The language by example

```
SENSOR   temp     AT A0 ANALOG UNIT C   RANGE -40..125;
SENSOR   humidity AT A1 ANALOG UNIT PCT RANGE 0..100;
ACTUATOR fan      AT LED4B;
ACTUATOR heater   AT LED3R;

RULE cool   REALTIME 20ms: WHEN temp > 35 THEN fan ON;
RULE uncool REALTIME 20ms: WHEN temp < 30 THEN fan OFF;
RULE warm:   WHEN avg(temp, 10s) < 18 THEN heater ON;
RULE unwarm: WHEN avg(temp, 10s) > 22 THEN heater OFF;
RULE alert:  WHEN temp > 45 FOR 5s THEN NOTIFY "Overheat";
RULE dry:    WHEN humidity < 20 FOR 3s THEN NOTIFY "Air too dry";

SAFETY NEVER heater ON AND fan ON;
SAFETY ENSURE fan ON WHEN temp > 50;
FAILSAFE heater OFF;
```

- **Sensors.** An analog sensor maps its 10-bit ADC code linearly onto `RANGE` in a `UNIT`. A
  digital sensor is 0 or 1.
- **Actuators.** Each is a binary output on a pin, here the board's own RGB LED channels.
- **Rules.** `WHEN` a conjunction of comparisons holds (optionally `FOR` a duration), `THEN` set
  an actuator or send a notification. A comparison may use a raw value or a window aggregate
  `avg/min/max(sensor, duration)`. `REALTIME d` declares a deadline: the rule must be evaluated
  at least every `d`.
- **Safety.** `NEVER a s1 AND b s2` forbids a combination of states. `ENSURE a s WHEN cond`
  forces a state. `FAILSAFE a s` is the state an actuator takes when the Linux side is lost.

The language is deliberately small: no `OR`, no `NOT`, no `!=`. An `OR` is written as two rules.
This keeps every condition a box, so every analysis in Stage 3 is exact.

### 4.2 Tokens

| Token | Rule | Examples |
|---|---|---|
| Keyword | `SENSOR ACTUATOR AT ANALOG DIGITAL UNIT RANGE RULE REALTIME WHEN AND FOR THEN ON OFF NOTIFY SAFETY NEVER ENSURE FAILSAFE` | `WHEN` |
| Aggregate | `avg min max` | `avg` |
| Identifier | `[a-z][a-z0-9_]*`, at most 32 characters, no `__` | `temp` |
| Number | `-?[0-9]+(\.[0-9]+)?` (the `-` only when a digit follows) | `35`, `-40`, `22.5` |
| Duration | number glued to `ms s min h`; value in integer ms | `20ms`, `10s`, `5min` |
| Unit | `C F PCT LUX CM`; `%` is `PCT` | `C` |
| Pin | `A0…`, `D0…`, `LED3`, `LED4R/G/B` | `LED4B` |
| String | `"…"` on one line; only `\"` and `\\` escapes | `"Overheat"` |
| Operator | `> < >= <= == ..` | `>=` |
| Punctuation | `: ; ( ) ,` | `;` |
| Comment | `//` to end of line | |

**Lexer design** (`iotc/lexer.py`):

- **Source handling.** The source is read as bytes. A UTF-8 BOM is stripped, CRLF/CR become LF,
  and the bytes must decode as strict UTF-8 (L000 otherwise).
- **Words.** A word `[A-Za-z_][A-Za-z0-9_]*` is scanned maximally and then classified by case.
  All lowercase gives an identifier or aggregate. All uppercase gives a keyword, unit or pin, or
  L005 with a *did-you-mean* suggestion: the candidate within a Levenshtein distance of
  `max(1, len/3)`. Mixed case is L007.
- **Numbers.** Only ASCII digits are accepted. A `.` continues a number only when a digit follows,
  so `-40..125` lexes as `-40`, `..`, `125`. After the digits a run of letters is scanned
  maximally: `ms s min h` make a duration, and anything else is L003 with a targeted hint (`35C`:
  "units need a space"). Durations must be a whole number of milliseconds, computed exactly with
  fractions (`1.5s` = 1500 ms, `0.5ms` is L003).
- **Look-alike characters.** The Unicode minus sign, smart quotes and non-breaking spaces each
  give L001 with the ASCII character to use instead.
- **Errors.** The lexer never stops. A bad character becomes an `ERROR` token and lexing
  continues.

**Token stream** of the demo program (excerpt from `docs/evidence/stage1_tokens.txt`; each line
gives line:column range, kind, source text and value):

```
8:1-5 KEYWORD 'RULE' 'RULE'
8:6-10 IDENT 'cool' 'cool'
8:13-21 KEYWORD 'REALTIME' 'REALTIME'
8:22-26 DURATION '20ms' 20
8:26-27 PUNCT ':' ':'
8:28-32 KEYWORD 'WHEN' 'WHEN'
8:33-37 IDENT 'temp' 'temp'
8:38-39 OP '>' '>'
8:40-42 NUMBER '35' '35'
8:43-47 KEYWORD 'THEN' 'THEN'
8:48-51 IDENT 'fan' 'fan'
8:52-54 KEYWORD 'ON' 'ON'
8:54-55 PUNCT ';' ';'
10:19-22 AGG 'avg' 'avg'
10:29-32 DURATION '10s' 10000
```

### 4.3 Grammar (EBNF)

```
program   = { decl } EOF ;
decl      = sensor | actuator | rule | safety | failsafe ;
sensor    = "SENSOR" IDENT "AT" PIN
            ( "ANALOG" "UNIT" unit "RANGE" num ".." num | "DIGITAL" ) ";" ;
actuator  = "ACTUATOR" IDENT "AT" PIN ";" ;
rule      = "RULE" IDENT [ "REALTIME" DURATION ] ":" "WHEN" cond
            [ "FOR" DURATION ] "THEN" action ";" ;
cond      = cmp { "AND" cmp } ;
cmp       = value relop num [ unit ] ;
relop     = ">" | "<" | ">=" | "<=" | "==" ;
value     = IDENT | agg "(" IDENT "," DURATION ")" ;
agg       = "avg" | "min" | "max" ;
action    = IDENT state | "NOTIFY" STRING ;
state     = "ON" | "OFF" ;
safety    = "SAFETY" ( "NEVER" IDENT state "AND" IDENT state
                     | "ENSURE" IDENT state "WHEN" cond ) ";" ;
failsafe  = "FAILSAFE" IDENT state ";" ;
num       = NUMBER ;
unit      = UNIT ;
```

### 4.4 The grammar is LL(1)

At every choice point, one token of lookahead decides the alternative. The FIRST and FOLLOW sets
of the choice points are disjoint:

| Choice point | Alternatives and their FIRST tokens |
|---|---|
| `decl` | `SENSOR` · `ACTUATOR` · `RULE` · `SAFETY` · `FAILSAFE` |
| sensor kind | `ANALOG` · `DIGITAL` |
| optional `REALTIME` in `rule` | `REALTIME` vs FOLLOW = `:` |
| `{ AND cmp }` | `AND` vs FOLLOW = `FOR`, `THEN`, `;` |
| optional `FOR` | `FOR` vs FOLLOW = `THEN` |
| `value` | `IDENT` · `AGG` |
| optional unit in `cmp` | `UNIT` vs FOLLOW = `AND`, `FOR`, `THEN`, `;` |
| `action` | `IDENT` · `NOTIFY` |
| `safety` | `NEVER` · `ENSURE` |
| `state` | `ON` · `OFF` |

So the parser (`iotc/parser.py`) is a predictive recursive-descent parser: one function per
nonterminal and no backtracking. Declarations may come in any order. Names are resolved after
parsing, so forward references are allowed.

### 4.5 Error recovery

- **Panic mode.** On an unexpected token the parser reports one P001 ("expected X, found Y").
  It then skips to a synchronisation token: `;` or the start of a declaration (`SENSOR`,
  `ACTUATOR`, `RULE`, `SAFETY`, `FAILSAFE`). It always consumes at least one token, so parsing
  terminates.
- **No cascades.** A token the lexer already reported as an error does not produce a second
  P001.
- **Error cap.** After 50 errors the rest are counted, not printed ("note: N further errors
  suppressed").
- **Targeted hints** for common mistakes:
  - `WHEN 35 < temp` gets "write `temp > 35`";
  - `THEN fan ON, heater OFF` gets "write two rules";
  - a lowercase keyword gets "keywords are UPPERCASE";
  - `10 ms` gets "write `10ms`".
- **Stopping.** If any lexical or syntax error exists, compilation stops before semantic
  analysis, so the user never sees confusing semantic errors caused by a syntax error.

### 4.6 Abstract syntax tree

The AST (`iotc/ast.py`) consists of frozen dataclasses: `Program`, `SensorDecl`, `ActuatorDecl`,
`Rule`, `Cmp` (over a `SensorRef` or an `Agg`), `SetState`, `Notify`, `Never`, `Ensure` and
`Failsafe`, with `Name` for identifiers. Each node carries a
source span (line, column, end column), which every later diagnostic points back to. The printed
AST of the demo program (`stage1_ast.txt`; durations normalised to milliseconds):

```
SENSOR temp AT A0 ANALOG UNIT C RANGE -40..125 @3:1
ACTUATOR fan AT LED4B @5:1
RULE cool REALTIME 20ms: WHEN temp > 35 THEN fan ON @8:1
RULE warm: WHEN avg(temp, 10000ms) < 18 THEN heater ON @10:1
RULE alert: WHEN temp > 45 FOR 5000ms THEN NOTIFY 'Overheat' @12:1
NEVER heater ON AND fan ON @15:1
ENSURE fan ON WHEN temp > 50 @16:1
FAILSAFE heater OFF @17:1
```

### 4.7 Diagnostics

Every message has a stable code, a severity, a primary span with a caret underline, optional
notes pointing at related spans, and a hint. Diagnostics are sorted by position and can also be
emitted as JSON (`--json`). For example:

```
error[L005]: unknown word 'WHN'
  --> examples/errors/L005_unknown_word.iot:3:12
   |
 3 | RULE cool: WHN temp > 35 THEN fan ON;
   |            ^^^ did you mean WHEN?
```

Exit codes are 0 (success, warnings allowed), 1 (errors in the program), 2 (usage or I/O error)
and 3 (internal compiler error).

---

## 5. Stage 2 — Semantic, unit and threshold checks

### 5.1 Symbol table and kinds

Sensors, actuators and rules share **one namespace** (`sema/symbols.py`); a duplicate is S002.
Every use is resolved, and a misspelt name gets a suggestion:

```
error[S001]: unknown sensor 'temprature'
  --> examples/errors/S001_undeclared_name.iot:3:17
   |
 3 | RULE cool: WHEN temprature > 35 THEN fan ON;
   |                 ^^^^^^^^^^ did you mean 'temp'?
```

Kinds are checked: comparing an actuator (`WHEN fan > 35`) or setting a sensor (`THEN temp ON`)
is S003.

### 5.2 Units and dimensions

Each unit belongs to a dimension: temperature (`C`, `F`), ratio (`PCT`), illuminance (`LUX`) and
distance (`CM`) (`sema/units.py`). A threshold may carry a unit. If it has the same dimension as
the sensor, it is converted **exactly** with rational arithmetic: `95 F` against a Celsius sensor
becomes exactly `35`, and `100 F` becomes `340/9`. A different dimension is S010
(`temp > 50 PCT`), and a unit on a digital comparison is S013.

### 5.3 Pins and board profiles

A board profile (`backend/profiles.py`) lists each board's pins, analog pins, LED channels and
whether each LED is active-low, along with constants measured in Phase 0: the minimum deadline,
the ADC jitter and the Bridge budget. The pin checks (`sema/pins.py`):

| Code | Check |
|---|---|
| S020 | The pin must exist on the board. The LED pins exist only on the UNO Q. |
| S021 | A pin may be bound only once. `LED3` is an alias of `LED3R`, so binding both is a clash. |
| S022 | An `ANALOG` sensor must use an analog pin, and no sensor may use an LED pin. |
| S023 | D0/D1 (the serial pins) give a warning. |

### 5.4 Ranges, thresholds and windows

| Code | Check |
|---|---|
| S012 | An empty or inverted `RANGE`. |
| S011 | A threshold outside the range (a warning). |
| S030 | A window, `FOR` or `REALTIME` of zero. |
| S037 | A duration over 24 h. |
| S035 | A window shorter than the sample period. |
| S031 | A window shorter than 32 sample periods (it uses fewer buckets). |
| S032 | Windows that exceed the board's RAM budget. |
| S036 | A threshold so precise that the exact average test could overflow 64-bit arithmetic. |

### 5.5 Threshold lowering to ADC codes

This is the central design decision of the compiler. The MCU only ever sees an integer code
`c ∈ [0, 1023]`. For an analog sensor with range `[lo, hi]`, the code maps to the value
`x(c) = lo + c·(hi − lo)/1023`. For a threshold `T`, let `r = x⁻¹(T)`, computed as an exact
fraction. The comparisons then become integer tests:

| Source comparison | Integer test on code `c` |
|---|---|
| `x > T` | `c ≥ ⌊r⌋ + 1` |
| `x ≥ T` | `c ≥ ⌈r⌉` |
| `x < T` | `c ≤ ⌈r⌉ − 1` |
| `x ≤ T` | `c ≤ ⌊r⌋` |
| `x == T` | `c == r` if `r` is an integer, otherwise never true |

For the demo's `RANGE -40..125`, `r = (T + 40)·1023/165 = 6.2·(T + 40)` exactly. So:

- `temp > 35`: `r = 465` exactly, so the test is `c ≥ 466`. Code 466 is 35.16 °C, the first
  code above 35.
- `temp < 30`: `r = 434`, so the test is `c ≤ 433`.
- The dead band between `cool` and `uncool` is codes 434–465: **32 codes**, reported as I105.

Window averages are tested without division. For `avg(temp,10s) < 18`, `r = 359.6 = 1798/5`, so
the test is `5·sum < 1798·count`, in 64-bit integers. Every implementation (C++, Python, the
interpreter) evaluates the *same* integer test, so a boundary disagreement is impossible by
construction. `iotc check` prints every compiled threshold:

```
Code thresholds (board unoq, 10-bit ADC)
  cool      temp > 35            ⇔  code >= 466  (code 466 = 35.16 C)
  uncool    temp < 30            ⇔  code <= 433  (code 433 = 29.84 C)
  warm      avg(temp, 10s) < 18  ⇔  5·sum < 1798·count  (average code < 359.6)
  unwarm    avg(temp, 10s) > 22  ⇔  5·sum > 1922·count  (average code > 384.4)
  alert     temp > 45            ⇔  code >= 528  (code 528 = 45.16 C)
  dry       humidity < 20        ⇔  code <= 204  (code 204 = 19.94 PCT)
  ENSURE#1  temp > 50            ⇔  code >= 559  (code 559 = 50.16 C)
```

A condition whose real interval is non-empty but contains no integer code
(`temp > 35 AND temp < 35.1`) can never fire on the board. This is W113.

---

## 6. Stage 3 — Event IR and program analyses

### 6.1 The event IR

Lowering (`lower.py`) turns the checked AST into a frozen IR (`ir.py`). Each rule becomes an event
rule:

```
IRRule(id, name,
       guard  : box = {variable ↦ interval}     -- real values, used by the analyses
       tests  : tuple of integer code tests     -- used at runtime by every back end
       hold_ms, action, deadline_ms,
       placement, reasons, mask_bit)
```

A variable is either a raw sensor or a window aggregate (`avg(temp, 10s)`). Windows are shared:
`avg`, `min` and `max` over the same sensor and duration use one buffer. Safety declarations
become `IRNever`, `IREnsure` (with its closure) and `IRFailsafe`. The printed IR of the demo
program (`stage3_ir.txt`, excerpt):

```
sensors
  s0 temp AT A0 ANALOG C [-40, 125]  code map x(c) = -40 + c*165/1023, c in 0..1023
actuators
  a0 fan AT LED4B (active-low)  init OFF  failsafe -
  a1 heater AT LED3R (active-low)  init OFF  failsafe OFF
windows
  w0 temp over 10s: 32 buckets, computed on Linux
rules (event IR: WHEN box [FOR] -> action, compiled to integer code tests)
  r0 cool  on MCU  (REALTIME 20ms)
      box     temp in (35, 125]
      test    temp: code >= 466
      action  fan := ON
  r2 warm  on LINUX  (mask bit 0)
      box     avg(temp, 10s) in [-40, 18)
      test    avg(temp, 10s): window non-empty and 5*sum < 1798*count
      action  heater := ON
safety
  never#1  NOT (heater ON AND fan ON)
  ensure#1  fan ON WHEN temp in (50, 125]
      closure fan ON, heater OFF
  failsafe closure  heater OFF
  initial state     fan OFF, heater OFF
```

### 6.2 Interval analysis

Every guard is a box, so the questions an engineer asks become interval operations
(`analysis/intervals.py`):

| Question | Operation | Diagnostic |
|---|---|---|
| Can the rule ever fire? | Is the box empty? | W101; W113 if no ADC code |
| Does it always fire? | Is the box the full domain? | W102; I118 for windows |
| Can an ON rule and an OFF rule for the same actuator hold together? | Box intersection | E103 (raw values), W103 (through a window) |
| Is there hysteresis? | Gap between the boxes | W104 if they touch, I105 with the gap, W112 if the gap is below ADC jitter |
| Can the actuator turn OFF / ON at all? | Setter sets | W106, W117 |

The conflict message names the overlap and the runtime consequence:

```
error[E103]: conflict: 'cool' sets fan ON and 'uncool' sets it OFF when temp ∈ (35, 40) C
  --> examples/errors/E103_conflict.iot:4:6
   |
 4 | RULE uncool: WHEN temp < 40 THEN fan OFF;
   |      ^^^^^^ OFF wins at runtime; make the two conditions disjoint
   = note: 'cool' sets fan ON (examples/errors/E103_conflict.iot:3:6)
```

Windowed variables are over-approximated: they range over the sensor's whole domain plus
"empty", independently of the raw value. The analysis can therefore warn about a combination
that never occurs in practice, but it never misses a real overlap between boxes.

### 6.3 Placement

Placement (`analysis/placement.py`) gives each rule a processor and a printed reason:

```
for each rule:
    REALTIME d                 → must run on the MCU
    NOTIFY                     → must run on Linux   ("NOTIFY leaves the MCU")
    uses a window (policy linux) → Linux             ("window history kept on Linux")
    must-MCU and window-on-Linux → error P201
    otherwise                  → MCU ("default: no Bridge hop")
SAFETY and FAILSAFE            → always the MCU (it owns the pins)
```

P201 is an error, not a warning. The measured Bridge latency (median 7.4 ms, max 101.6 ms) has no
bound, so a deadline cannot depend on it:

```
error[P201]: REALTIME rule 'cool' uses avg(temp, 5min), which --place-windows=linux puts on Linux
 3 | RULE cool REALTIME 10ms: WHEN avg(temp, 5min) > 35 THEN fan ON;
   |                               ^^^^^^^^^^^^^^^ the Bridge has no bounded latency;
   |                                               use --place-windows=mcu or remove REALTIME
```

Linux-placed rules that set actuators get one bit each in a 32-bit **mask** (P203 beyond 32). On
the board, Linux never drives a pin itself. It only sends the mask of which of its rules currently
fire, and the MCU turns those bits into requests.

### 6.4 Safety analysis

**Closures** (`analysis/closure.py`). A NEVER pair forbids a combination. If an ENSURE forces
`fan ON`, the pair `NEVER heater ON AND fan ON` means the heater must also be forced OFF. The
closure of an assignment is computed with a worklist over the NEVER pairs:

```
closure(a := s):
    result = {a := s}; work = [a := s]
    while work: take x := v
        for each NEVER pair with side (x, v) and other side (y, w):
            if result has y := w → contradiction
            else add y := ¬w to result and work
```

The closures are used as follows:

| Code | Check |
|---|---|
| W107 | An ENSURE forces partners; the warning lists the whole closure (`fan ON, heater OFF`). |
| E116 | An ENSURE's closure contradicts itself. |
| E108 | Two ENSUREs with overlapping conditions force opposite states. |
| E109 | The FAILSAFE closure is contradictory, or the initial state violates a NEVER. |
| W110 | An ENSURE overrides a FAILSAFE. |
| W119 | A FAILSAFE disables an MCU rule while Linux is down. |

```
error[E116]: ENSURE fan ON is impossible: the NEVER pairs force heater both ON and OFF
 10 | SAFETY ENSURE fan ON WHEN temp > 50;
    | ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ remove or change one of the NEVER pairs involved
```

**NEVER proofs** (`analysis/safety.py`). A pair `(a ON, b ON)` is reported **proven** only if a
covering rule exists for every rule that can switch either side ON:

- every rule requesting `a := ON` is covered by a rule requesting `b := OFF` whose box contains
  it, which has no `FOR` and runs on the same processor;
- the same holds the other way round, for rules requesting `b := ON`.

Since OFF wins within a pass, a covered pair can never be violated. Otherwise the report says
**not proven** and gives a witness. The pair is then enforced at runtime by the MCU monitor
(Section 8.4):

```
⚠ NEVER heater ON AND fan ON    not proven: warm (heater ON on avg(temp, 10s) < 18) is not
                                covered by a fan OFF rule and cool (fan ON on temp > 35)
                                can hold at the same time → enforced by the MCU monitor
✓ ENSURE fan ON WHEN temp > 50  enforced by the MCU monitor every pass; closure: fan ON, heater OFF
✓ FAILSAFE heater OFF           initial state; applied after 2000 ms without a fresh heartbeat
```

### 6.5 Resources

The report also estimates the resources the program needs:

- window RAM on the MCU;
- Linux mask bits used, out of 32;
- Bridge traffic: about 239 B/s for the demo, 2.1 % of the 11,520 B/s link (P205 above 30 %);
- the largest Linux → MCU message (28 B, against the router's 256 B limit);
- the LED-matrix layout (I122 if it is truncated).

---

## 7. Stage 4 — Target code generation

### 7.1 Targets

| Target | Output |
|---|---|
| `--board unoq --target unoq` (default) | A full App: MCU sketch, Linux `main.py`, the runtime package, the dashboard, `app.yaml` |
| `--target mcu-only` | Every rule on the MCU; Linux idle. A backup with no Bridge dependency |
| `--board uno --target mcu-only` | A classic Arduino Uno sketch that logs over Serial (used in the Wokwi simulator) |

Input layers (`--input`):

- **`pins`** reads the real analog and digital pins.
- **`sim`** takes sensor codes from sliders on the web page. If no slider message arrives for 3 s
  (Linux dead), it falls back to a compiled-in scenario.
- **`scenario`** plays a compiled-in trace. This is what conformance mode uses.

### 7.2 The MCU sketch is table-driven

The generated `iotc_gen.h` contains constant tables and a fixed, verified pass function. Only the
rule predicates are generated as code, and they are pure integer tests:

```cpp
// ===== generated predicates: integer code tests (ADR-17) =====
static bool pred_r0() { return (code[0] >= 466); }  // rule cool
static bool pred_r1() { return (code[0] <= 433); }  // rule uncool
static bool pred_r2() { return false; }  // rule warm (evaluated on Linux)
static bool pred_e0() { return (code[0] >= 559); }  // ENSURE 1

static const uint8_t RULE_ACT[N_RULE_A]     = { 0, 0, 1, 1, 0, 0 };
static const uint8_t RULE_STATE[N_RULE_A]   = { 1, 0, 1, 0, 0, 0 };
static const uint8_t RULE_MCU[N_RULE_A]     = { 1, 1, 0, 0, 0, 0 };
static const uint8_t RULE_MASKBIT[N_RULE_A] = { 255, 255, 0, 1, 255, 255 };
static const uint32_t SAMPLE_MS = 100, FAILSAFE_TIMEOUT_MS = 2000;
```

The `sketch.ino` is three lines that call `iotc::setup()` and `iotc::loop()`. The loop never
blocks: no `delay`, no blocking Bridge call, no `Serial`.

**Names (ADR-26).** DSL names never become C++ or Python identifiers. Rules are `pred_r0`,
`pred_r1`, and so on; user names appear only inside escaped string literals. A program with
sensors named `int` and `class`, an actuator named `loop` and a rule named `delay` compiles
cleanly under `g++ -Wall -Wextra -Werror` (a test).

**Escaping** (`escape.py`). There is one module per output language: C++ string literals (octal
escapes, trigraph-safe), Python `repr`, CSV, and JSON safe to embed in HTML. The dashboard sets
all text through `textContent` under a Content-Security-Policy, so a NOTIFY text such as
`</script>` is harmless.

### 7.3 The Linux program

`main.py` is generated from a template (`backend/main_template.py`). It embeds the runtime table
as a Python literal and imports the runtime package `iotc_runtime`, a byte-for-byte copy of
`iotc/runtime/` (a test checks the copy). Its `Brain` class:

- evaluates the Linux rules on each sample and sends heartbeats;
- decodes MCU events into the canonical log;
- serves the dashboard state;
- range-checks every value before it crosses the Bridge (the Phase 0 truncation finding).

Bridge handlers only enqueue messages. All logic runs in the App's loop, inside `try/except`,
because App Lab does not restart a crashed program.

### 7.4 The Bridge contract

All message names and argument types live in one table (`backend/bridge.py`), read by both back
ends:

| Message | Direction | Arguments | Purpose |
|---|---|---|---|
| `sample` | MCU → Linux | `t:u32, code0..n:u16` | sensor codes every tick |
| `event` | MCU → Linux | `t, act:u8, state:u8, src:u8, result:u8, detail:u8` | actuator changes and refusals |
| `link` | MCU → Linux | `t, up:u8` | link UP/DOWN |
| `stats` | MCU → Linux | `t, overruns, max_pass_us, faults` | timing statistics |
| `hb_ack` | MCU → Linux | `seq:u32` | live Bridge round-trip measurement |
| `sync` | MCU → Linux | `t, iteration:u16` | scenario start (conformance) |
| `heartbeat` | Linux → MCU | `seq:u32, mask:u32, ack:u32` | liveness, Linux rule flags, last sample processed |
| `sim_set` | Linux → MCU | `sensor:u8, code:u16` | slider input |

### 7.5 Dashboard and App folder

The App writer (`backend/app_writer.py`) writes into a temporary folder and replaces the target
only if it is empty or was written by `iotc` (a marker file). A typing mistake in `-o` can
therefore never delete a user's folder.

The dashboard shows:

- sliders with threshold ticks;
- actuator lamps;
- a 60 s chart, zoomed to the thresholds;
- the compiler's placement and safety tables, with rows lighting up as rules fire;
- timing statistics and the live Bridge round trip;
- an LED-matrix mirror;
- a human-readable event log ("fan ON refused by NEVER#1 (heater is ON)");
- a demo control that pauses the Linux rule engine for 10 s.

Builds are deterministic: two builds under different Python hash seeds are byte-identical, with
LF line endings only (a test).

---

## 8. Runtime semantics

The runtime semantics are normative (Architecture §9). The MCU, the Linux program and the
interpreter implement them from the same specification, and the tests compare them.

### 8.1 The tick grid

Time is divided into ticks of `S = 100 ms`. A pass is a tick when `⌊now/S⌋` changes. The
following happen only on ticks:

- window pushes and FOR-timer updates;
- latching new input values;
- latching the Linux mask.

The board's behaviour is therefore a function of the sequence of ticks, independent of how many
extra passes run in between. A test runs the C++ with 1 and with 10 passes per tick and gets
identical logs.

### 8.2 Windows

A window of `W` ms has `B = min(32, W/S)` buckets. Tick time `τ` falls in bucket `k = ⌊τ·B/W⌋`.
A slot stores `(id, sum, count, min, max)` and is reset when a different `id` arrives, so gaps of
any length are handled. The window aggregates the buckets with ids in `(k − B, k]`. An empty
window makes every comparison on it false.

### 8.3 FOR and NOTIFY

A `FOR h` rule starts a timer when its box first holds on a tick. It fires once the box has held
continuously for `h`, and resets the first time the box fails on a tick. A NOTIFY rule fires once
per rising edge (an `armed` flag).

### 8.4 One MCU pass

1. Snapshot the values written by Bridge handlers, with interrupts disabled. Handlers can preempt
   the loop: Phase 0 observed this once in 2422 heartbeats.
2. Decide whether this pass is a tick.
3. On ticks, update the link state from the heartbeat (8.5), push windows and update FOR timers.
4. Evaluate the MCU rules into requests. If the link is UP, add the requests of the Linux rules
   whose mask bit is set.
5. Resolve per actuator: **any OFF request wins**, otherwise any ON, otherwise keep.
6. If the link is DOWN, apply the FAILSAFE closure (these values are *immune* to refusal).
7. Apply the closure of every ENSURE whose condition holds (immune, overrides FAILSAFE).
8. **NEVER fixpoint.** While some NEVER pair is violated, revert every non-immune side that
   changed in this pass back to its current value, for at most N_ACT rounds. Reverting all
   violated pairs per round (a simultaneous fixpoint) makes the result independent of the order
   of the NEVER lines. An earlier single-pass design was shown to be unsound by a counterexample.
9. Commit: write the pins (active-low aware) and log `EV` lines (`applied`, `forced`, `failsafe`
   or `refused`).
10. Stream the tick's sample to Linux, stats every second, and draw the LED matrix if the frame
    changed.

### 8.5 Heartbeat and failsafe

Linux sends `heartbeat(seq, mask, ack)` whenever its mask changes, and at least every 500 ms.
`ack` is the time of the last sample Linux processed. The MCU keeps the link UP only while both
conditions hold:

- the last heartbeat is less than `T = 2000 ms` old;
- the acknowledged sample is less than `T` old.

The second condition catches a Linux side that is alive but no longer receiving data. When the
link goes DOWN, the MCU ignores the mask and applies the FAILSAFE states. Its own rules keep
running. Linux detects an MCU reset (time going backwards) and resets its own state.

The predicted failsafe latency is between `T − H = 1.5 s` and `T + S = 2.1 s` after Linux stops.

### 8.6 The event log

One canonical format is used everywhere: board, interpreter, tests and page.

```
LK,<t>,UP|DOWN
EV,<t>,<actuator>,<ON|OFF>,<rule|ENSURE#k|FAILSAFE>,<applied|refused|forced|failsafe>,<-|never#k>
NT,<t>,<rule>,"<text>"
SY,<t>,<iteration>
```

---

## 9. Stage 5 — Simulation and verification

### 9.1 Reference interpreter

`iotc run FILE --trace T.csv` runs a sensor trace through the model of the MCU pass
(`runtime/mcu.py`) and the Linux side (`runtime/engine.py`, the same code that runs on the board).
It prints the canonical log. Linux failures can be injected with `--kill-linux-at`,
`--revive-linux-at` and `--deaf-linux-at`. The demo trace with Linux killed at 15 s
(`stage5_interpreter_log.txt`):

```
LK,100,UP
EV,3800,heater,ON,warm,applied,-
EV,15500,fan,ON,cool,refused,never#1
LK,16700,DOWN
EV,16700,fan,ON,cool,applied,-
EV,16700,heater,OFF,FAILSAFE,failsafe,-
EV,20000,fan,OFF,uncool,applied,-
LK,20100,UP
EV,20100,heater,ON,warm,applied,-
...
NT,50000,alert,"Overheat"
```

The log reads as follows:

- 15.5 s: the fan request is refused because the heater is ON.
- 16.7 s: the link goes down (heartbeat timeout). The heater is switched OFF by FAILSAFE, so the
  pending fan request can now be applied.
- 20.1 s: Linux is revived and the heater returns.

`iotc matrix-preview` renders the LED-matrix frame at any time. At 16.7 s it shows the `F`
glyph (`stage5_led_matrix.txt`).

### 9.2 Golden tests

Every stage output for the reference programs is compared byte-for-byte with a checked-in golden
file: token streams, ASTs, rendered and JSON diagnostics for every error program, `iotc check`
reports, interpreter logs and generated C++ headers (201 golden files).
`py -3.13 tasks.py golden` regenerates them for review.

### 9.3 Differential testing of the generated C++

The generated `iotc_gen.h` is compiled on the host with `g++ -std=gnu++17 -Wall -Wextra -Werror`
against a small shim of the Arduino APIs (`tests/host_shim/`). The shim feeds each trace row to the
sketch, delivers the heartbeats the interpreter's Linux model would send, and records events, pin
levels and LED-matrix frames. Every example program on every target is run on the demo traces and
on random traces. The test compares the C++ against the interpreter on the log, the pin levels
and every frame. A deliberately broken copy of the C++ runtime (OFF-wins disabled) must be
detected, which shows the harness can fail. In the interpreter, the mutants `disable-monitor` and
`single-pass-never` must change behaviour.

### 9.4 Testing the generated Linux program

The generated `main.py` is imported unchanged with stub `arduino.*` modules. It is driven on a
fake clock with the demo trace. Its heartbeats and NOTIFY lines must equal the interpreter's.
Further tests cover:

- slider clamping and exact Bridge argument types;
- pausing;
- MCU-reset detection;
- stale acknowledgements;
- the dashboard's static safety (no `innerHTML`, no remote URLs, CSP present).

### 9.5 Conformance mode: predicting the board

`iotc build FILE --input scenario --scenario tour.csv` makes the compiler run the board model in a
closed loop: the sketch's pass plus the Linux runtime, one pass per tick. It records the predicted
event log per scenario iteration, keyed by tick index from the scenario start, and bakes it into
the page. On the board, the page compares the reported lines with the prediction and shows
✓ exact, ≈ shifted, ✗ missing or not-predicted for each line. The prediction for the 50 s demo
tour (`stage5_conformance_prediction.txt`):

```
iteration 1
  tick   30  (  3.0 s)  EV,fan,ON,cool,applied,-
  tick   90  (  9.0 s)  EV,fan,OFF,uncool,applied,-
  tick  177  ( 17.7 s)  EV,heater,ON,warm,applied,-
  tick  260  ( 26.0 s)  EV,fan,ON,cool,refused,never#1
  tick  301  ( 30.1 s)  EV,fan,ON,cool,applied,-
  tick  301  ( 30.1 s)  EV,heater,OFF,unwarm,applied,-
  tick  390  ( 39.0 s)  NT,alert,"Overheat"
  tick  420  ( 42.0 s)  EV,fan,OFF,uncool,applied,-
  tick  470  ( 47.0 s)  NT,dry,"Air too dry"
```

The prediction is tested four ways. It must equal:

1. the reference interpreter on the same rows;
2. the generated `main.py` driving the board model;
3. the **compiled C++ sketch in a closed loop** with the Linux runtime: the test drives the host
   binary interactively and feeds its samples to the Linux code;
4. itself under four different simulated boot times.

### 9.6 Virtual board

`iotc dashboard FILE` runs the full generated App on the laptop. The unchanged `main.py` runs
against a Python model of the sketch, and the same dashboard is served locally, marked **VIRTUAL
BOARD**. It is a backup for the demo and a quick way to explore a program without hardware. In
conformance mode it reported "Iteration 1: 9/9 predicted events matched exactly" with real
threads and real timing.

### 9.7 Defects found by the verification

Each layer caught real defects:

| Found by | Defect | Fix |
|---|---|---|
| Design review (counterexample) | A single-pass NEVER check could leave a violation, and its result depended on the order of the NEVER lines | Simultaneous fixpoint (8.4 step 8) |
| Phase 0 on the board | `u16` handler arguments silently truncate; `Serial` blocks for 17 ms | Range checks on every argument; no Serial in the loop |
| Board run B2 | The dashboard port was unreachable: the board had no network address | USB tunnel (`adb forward`) |
| Building the closed-loop conformance test | In scenario mode, the MCU waited for the link before sending samples, and Linux waited for samples before sending heartbeats: a **deadlock** | The MCU streams samples while waiting |
| Comparing the prediction with the interpreter | Window bucket ids are absolute times, so the result depended on **when the board booted** (the heater line moved by two ticks) | Scenario starts aligned to the bucket period (2500 ms for the demo); a test checks four boot times |
| Reviewing B2 screenshots | A page opened mid-run lost the actuator history | History rows carry actuator states |

### 9.8 Test summary

| Area | Tests |
|---|---|
| Lexer, source handling | 94 |
| Parser | 59 |
| Diagnostics for every error program (`examples/errors/`, 80 programs) | 81 |
| Semantic checks, IR, lowering, ADC codes (including exhaustive checks over all 1024 codes) | 242 |
| Analyses and examples | 29 |
| Windows (property tests) | 50 |
| Interpreter | 29 |
| Back end, build, escaping | 42 |
| Generated C++ vs interpreter (differential) | 17 |
| Generated Linux program, virtual board, conformance | 27 |
| CLI, `dump`, `doctor`, Phase 0 Apps | 15 |
| **Total** | **685**, all passing |

---

## 10. Results on the board

All runs used our UNO Q (Arduino App CLI 0.13.0, App Lab 0.10.0, Python 3.13.14 in the App
container). The full record is in `docs/evidence/board_results.md`.

### 10.1 Phase 0 measurements

| Quantity | Measured |
|---|---|
| MCU pass interval with the Bridge active | median 1.15 ms, max 2.5 ms |
| Bridge round trip (n = 2438) | median 7.4 ms, p95 12.2 ms, max 101.6 ms |
| `micros()` resolution | 1 µs |
| LED-matrix `draw()` | 4.6 µs average |
| 2 KB burst of notifies | delays one pass by 15.5 ms; nothing lost |
| Linux `docker pause` / `docker kill` | the MCU keeps running |

### 10.2 Generated code on the board

| Step | Result |
|---|---|
| B1: MCU-only sketch with the compiled-in tour | The LEDs and the matrix followed the predicted timeline (fan at 3 s and 9 s, heater at about 17.7 s, refusal glyph at 26 s, swap at 30.1 s) |
| B2: full App with dashboard | Rules ran on both processors; ENSURE forced the fan ON and the heater OFF, followed by the Overheat notification 5 s later (the FOR timer); live Bridge round trip 9 ms median, 13 ms max; worst MCU pass interval 3.9 ms against a 20 ms deadline, 0 overruns |
| B3: failsafe, page pause | Paused at 85.9 s; **link DOWN and heater OFF by FAILSAFE at 87.9 s (2.0 s, predicted 1.5–2.1 s)**; the fan still switched ON by its MCU rule at 88.5 s; link UP at 96.1 s; Linux heater rule back at 101.7 s |
| B3: `docker pause` / `docker kill` of the Linux program | *[to be filled in after the run]* |
| B4: conformance on the board | *[to be filled in after the run: iteration summaries from the page]* |

---

## 11. Demonstration

The 10-minute demonstration shows the compiler first and the board last (`docs/DEMO_CARD.md`):

1. `iotc check` on the demo program: code thresholds, the hysteresis gap, placement, safety.
2. Error programs: a typo with a suggestion, a pin clash, a unit mismatch, a conflict with its
   overlap, a REALTIME rule on Linux, a contradictory ENSURE.
3. The pipeline stages: `iotc dump --stage tokens | ast | ir`.
4. The board: hysteresis on the fan, a Linux rule on the heater, the soft failsafe, a NEVER
   refusal, ENSURE, a real kill of the Linux program, and conformance.

Fallbacks, in order:

1. the live App;
2. the MCU-only build;
3. the virtual board on the laptop;
4. the Wokwi classic-Uno sketch;
5. a recorded video.

---

## 12. Limitations and future work

- **Language.** Only `AND` is supported, with no `OR`, `NOT` or `!=`. Actuators are binary (no
  PWM). These limits keep conditions as boxes; extending to unions of boxes would keep the
  analyses exact at a cost in their complexity.
- **Windowed analysis** is an over-approximation: it can warn about a conflict that real data
  never produces.
- **REALTIME** is measured and reported, not enforced. The MCU cannot preempt itself to meet a
  deadline.
- **Platform.** If the Bridge router itself restarts, the MCU stays in FAILSAFE until it is reset.
  This is documented and never shown in the demo. App Lab's Stop button resets the MCU, so the
  failsafe demo uses `docker pause`/`kill`.
- **Fuzzing.** A property-based fuzzer is specified (Architecture §10.2): random traces, safety
  invariants, shrinking, and mutants it must catch. It was not built in this iteration; the
  mutants are exercised by the interpreter and differential tests instead.
- **Board evidence.** At the time of writing, the `docker pause`/`kill` runs and the on-board
  conformance run are pending.

---

## 13. Conclusion

`iotc` shows that a small rule language can be compiled, not just templated, into correct code
for a heterogeneous two-processor board. Restricting conditions to conjunctions made every
analysis exact. Compiling thresholds to integer ADC tests removed a whole class of floating-point
disagreements. Placing the safety monitor on the processor that owns the pins made safety
independent of the Linux side. The compiler explains its decisions (thresholds, placement,
proofs) in a report a non-expert can read. Its output is verified at every level: golden files
per stage, generated C++ against an interpreter, generated Python against the same model, and
finally the board against the compiler's own prediction. On the board, the measured failsafe
reaction fell inside the predicted window.

---

## 14. References

1. A. V. Aho, M. S. Lam, R. Sethi, J. D. Ullman. *Compilers: Principles, Techniques, and Tools*,
   2nd ed. Addison-Wesley, 2006.
2. P. Cousot, R. Cousot. "Abstract interpretation: a unified lattice model for static analysis of
   programs by construction or approximation of fixpoints." *POPL*, 1977.
3. W. M. McKeeman. "Differential testing for software." *Digital Technical Journal* 10(1), 1998.
4. N. Wirth. *Compiler Construction*. Addison-Wesley, 1996.
5. Arduino. UNO Q documentation. https://docs.arduino.cc/hardware/uno-q
6. Arduino. Arduino_RouterBridge library. https://github.com/arduino-libraries/Arduino_RouterBridge
7. Arduino. ArduinoCore-zephyr. https://github.com/arduino/ArduinoCore-zephyr
8. Arduino. arduino-app-cli. https://github.com/arduino/arduino-app-cli

---

## Appendix A — The demo program

`examples/greenhouse_board.iot` (Section 4.1) uses the board's own LEDs as actuators:

| Actuator | LED channel |
|---|---|
| `fan` | LED4 blue |
| `heater` | LED3 red |

Sensor values come from sliders on the dashboard or from a compiled-in scenario. With the optional
hardware kit (potentiometers on A0/A1), the same source builds with `--input pins`, with no
compiler change.

## Appendix B — Diagnostic catalogue (summary)

Every code has an example program in `examples/errors/` and a golden rendering. The full table,
with trigger examples, is Architecture §6.

| Family | Codes |
|---|---|
| Lexer (errors) | L000 not UTF-8 · L001 bad character / look-alike · L002 unterminated string · L003 bad number or duration · L004 bad escape · L005 unknown word (did-you-mean) · L006 `!=` · L007 mixed case / bad identifier · L008 bad NOTIFY text |
| Parser | P001 expected X, found Y (with targeted hints) |
| Semantics | S001 undeclared · S002 duplicate · S003 wrong kind · S010 unit dimension · S011 threshold outside range · S012 bad range · S013 unit on digital · S020 pin missing · S021 pin twice · S022 wrong pin type · S023 serial pin · S030 zero duration · S031 short window · S032 window RAM · S033 aggregate over digital · S034 REALTIME NOTIFY · S035 window below sample · S036 overflow · S037 duration too long · S040 NEVER on one actuator · S041 two FAILSAFEs · S042 `==` on analog · S043 duplicate NEVER · S050/S051 unused |
| Interval and safety analysis | W101 never fires · W102 always fires · E103/W103 conflict · W104 no hysteresis · I105 hysteresis gap · W106 never OFF · W107 ENSURE closure · E108 ENSUREs collide · E109 failsafe/initial state · W110 ENSURE vs FAILSAFE · W111 REALTIME refused · W112 gap below jitter · W113 no ADC code · E116 contradictory ENSURE · W117 never ON · I118 window full range · W119 FAILSAFE over MCU rule · W120 FOR vs sample · W121 deadline too short · I122 matrix truncated |
| Placement and resources | P201 REALTIME on Linux · P202 ENSURE with window · P203 > 32 Linux rules · P204 > 16 sensors · P205 Bridge budget |

## Appendix C — How to run

```powershell
py -3.13 tasks.py test                                    # 685 tests
py -3.13 -m iotc check examples\greenhouse_board.iot      # Stage 2-3 report
py -3.13 -m iotc dump examples\greenhouse_board.iot --stage tokens   # also: ast, ir, table
py -3.13 -m iotc run examples\greenhouse_board.iot --trace tests\traces\demo_script.csv --kill-linux-at 15000 --revive-linux-at 20000
py -3.13 -m iotc build examples\greenhouse_board.iot --input sim --demo-controls --scenario scenarios\fallback_sweep.csv -o build\b2_board
py -3.13 tasks.py virtual                                  # the App on a virtual board, no hardware
py -3.13 tasks.py evidence                                 # regenerate docs/evidence/
py -3.13 -m iotc doctor                                    # check tools and board before a demo
```

Board steps (deploy, failsafe and conformance runs) are in `docs/MANUAL.md`.

## Appendix D — Repository layout

```
iotc/            compiler: source, lexer, parser, ast, diagnostics, sema/, adc, lower, ir,
                 analysis/, table, runtime/ (window, engine, mcu, matrix), interp, board_model,
                 conformance, virtual_board, dump, doctor, cli, backend/ (profiles, bridge,
                 cpp_core, mcu_cpp, main_template, linux_py, webpage, app_writer, web/, templates/)
examples/        12 valid programs; errors/ with one program per diagnostic code (80 files)
scenarios/       compiled-in input scenarios (demo tour, fallback sweep)
tests/           685 tests, golden files, traces, C++ host shim
docs/            REPORT.md, DEMO_CARD.md, SLIDES.md, MANUAL.md, evidence/
hardware/phase0/ probe Apps and measured results
scripts/         deploy, Linux pause/kill/restore, page tunnel, versions, evidence generator
wokwi/           classic-Uno backup wiring
Architecture.md  normative specification and design decisions
```
