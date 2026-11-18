# IoT Sensor DSL Compiler (`iotc`) — Architecture and Steering Guide

Version 3 · 17 Nov 2026 · supersedes v2 (17 Nov 2026) and "Project Architecture" (13 Nov 2026)

This file is the single source of truth for the project. It is written for two readers: the
team, and any coding agent working in this repository. Sections marked **NORMATIVE** define
behaviour that code and tests must match exactly. Items marked **[BOARD]** were confirmed by
reading Arduino's source code but still need one confirmation on our board (Phase 0, §15.1);
record the result in §19.

**What changed in v3 (summary).** The demo is board-only: no external parts, sensors come from
web sliders, actuators are the onboard RGB LEDs, the LED matrix shows live status. All analog
comparisons are compiled to integer ADC-code tests (ADR-17). Time-driven state advances on a
sample-tick grid (ADR-18). The NEVER monitor is a simultaneous fixpoint, and ENSURE/FAILSAFE use
static closures (ADR-19/20), because the v2 single pass was unsound. The heartbeat carries an
acknowledgement and the link state is latched (ADR-21). Logging never happens in the MCU loop on
the UNO Q (ADR-22). The facts about the board (FQBN, App Lab Stop resets the MCU, port 7000, …)
were researched from Arduino's sources; see §19.

---

## 0. Rules for agents working in this repo

Read §1, §3, §9 and §19 before writing code. Then follow these rules.

1. **§9 (runtime semantics) is the contract.** The interpreter (`interp.py` + `runtime/`), the
   MCU back end (`backend/mcu_cpp.py`) and the Linux back end (`backend/linux_py.py`) must all
   implement §9 exactly. A change to any of them means a change to §9, to all three
   implementations, and to the tests, in the same commit.
2. **Bridge names and argument types exist in one place only:** the table in
   `backend/bridge.py` (§11.5). Never type a Bridge function name as a string literal anywhere
   else. Both the C++ and the Python emitters read that table.
3. **Every diagnostic has a code from §6**, a test file under `examples/errors/` or
   `tests/analysis_cases/`, and an expected-output golden file. No code, no test → don't add it.
4. **Never hand-edit golden files.** Regenerate with `py tasks.py golden` (= `pytest
   --update-golden`), then read the diff and explain it in the commit message.
5. **Don't build on a [BOARD] item** without isolating it behind the board profile (§11.1) so
   the fix is one constant.
6. **Dependencies:** Python ≥ 3.11 standard library only for `iotc` itself (development uses
   3.13). Tests use `pytest`; `hypothesis` is optional. Host-side C++ tests use WSL `g++`
   (§13.3). Generated `main.py` uses only the stdlib plus `arduino.app_utils` and
   `arduino.app_bricks` (Python 3.13 on the board).
7. **Determinism:** compiler output must be byte-identical across runs and machines: LF line
   endings only, UTF-8, no timestamps, no absolute paths, no set iteration (iterate source order
   or `sorted()`), all files written through `iotc.source.write_text`. Header comments carry the
   compiler version and the SHA-256 of the *normalised* source (BOM stripped, LF endings).
8. **Exact arithmetic:** use `fractions.Fraction` for every threshold, range and interval bound
   in the middle end. Generated code compares **integers only** (ADR-17). Floats appear only for
   display (page, reports).
9. **Never emit a DSL name as a C++ or Python identifier.** Generated code addresses sensors,
   actuators and rules by index; names appear only inside escaped string literals (§11.4).
10. **Small steps.** One pipeline stage or one diagnostic per change, with its tests. Run
    `py tasks.py test` before declaring anything done.
11. **When in doubt, prefer the simpler language.** Rejecting a construct with a clear error and
    a hint is always acceptable; silently over-approximating is not.

---

## 1. Mission, deliverables, definition of done

**Mission.** Compile one `.iot` rule file into code that runs on the Arduino UNO Q: an MCU
sketch (STM32U585), a Linux Python program (QRB2210), the Bridge glue between them and a live web
dashboard, after static analyses that find rule bugs, prove or enforce safety properties, and
decide **which processor runs each rule**.

**Primary deliverable: live hardware demonstration on the Arduino UNO Q, board only** (no
external components). Everything else supports it.

| # | Deliverable | Done when |
|---|---|---|
| D0 | This document (v3) | Committed before any code |
| D1 | Phase 0 probe App + checklist (`hardware/phase0/`) | Run on the board; §19 filled; templates copied |
| D2 | Front end with error codes | Every `L`, `P0`, `S` code in §6 has an example, a rendered golden and a JSON golden |
| D3 | Middle end: ADC lowering, interval, placement, safety analyses | `iotc check examples/greenhouse.iot --board unoq` matches its golden |
| D4 | Reference interpreter + shared runtime | Hand-trace goldens pass for both targets |
| D5 | MCU back end (`unoq`, `mcu-only`; inputs `pins`/`sim`/`scenario`; LED matrix) | Host g++ compile + `arduino-cli compile` pass; differential test = interpreter |
| D6 | Linux back end, dashboard, App writer | `py_compile`; co-simulation = interpreter; App runs on the board |
| D7 | Board demo features | Demo script §17 passes on the board three times |
| D8 | Evidence: fuzzer, differential test, co-simulation, conformance mode | 10,000 traces, 0 violations; every mutant caught; diff = 0; board conformance N/N |
| D9 | Tooling: `tasks.py`, `iotc deploy`, Linux kill/pause/restore scripts, preflight | One command builds, deploys and starts the App |
| D10 | Demo kit: demo card, rehearsal log, recorded video, backup builds | Video recorded before demo day |
| D11 | Report chapters + slide content | Semantics chapter from §9; evidence from §19 and the fuzzer |

Course requirements covered: token recognition (lexer), AST construction (parser), domain rules
(semantic checks + analyses), executable output (two back ends). `examples/` far exceeds the
brief's minimum of 10 valid and 5 invalid programs (§13.1).

---

## 2. Scope

| Item | Tier |
|---|---|
| Lexer, parser, AST, semantic checks with codes and hints | Core |
| Units, ranges, pin maps for `uno` and `unoq` boards (incl. onboard RGB channels) | Core |
| ADC-code lowering; interval analysis (unreachable, always-true, conflicts, hysteresis) | Core |
| Placement and safety analysis (closures, NEVER proofs), printed as reports | Core |
| MCU safety monitor (`NEVER` fixpoint, `ENSURE`, `FAILSAFE`) in generated code | Core |
| `--target unoq`: MCU sketch + `main.py` + Bridge + App folder | Core |
| Heartbeat failsafe with acknowledgement and latched link state | Core |
| Input layer: `--input sim` (web sliders) with compiled-in fallback scenario | Core (board-only demo) |
| LED-matrix status display, generated from the program | Core (board-only demo) |
| Web dashboard: live values, charts, actuator lamps, placement/safety panels, event log, stats | Core |
| Demo controls (pause Linux rule engine), deploy/kill/restore scripts | Core |
| Reference interpreter, trace fuzzer with shrinking and mutants | Core |
| Differential test (host C++ vs interpreter), co-simulation (host C++ + generated `main.py`) | Core |
| Conformance mode: predicted log vs board log on the page | Should-have |
| `--target mcu-only`: single sketch for Wokwi (`uno`) or the UNO Q MCU alone | Should-have (backup) |
| `--place-windows=mcu` placement policy flag | Should-have (cheap, good for viva) |
| Live recompile on stage | Stretch |
| Physical kit (pot, LDR, button, buzzer) on `--input pins` | Stretch (no compiler change needed) |
| `OR` / `NOT`, `!=` | Out (write two rules) |
| Static proof of `ENSURE` | Out (runtime only) |
| PWM, servos, I2C/Modulino sensors, active-low external loads, cameras, hot reload | Out |
| Sending `NOTIFY` off the board (email, cloud, Telegram) | Out |

---

## 3. Key design decisions (ADR summary)

| ID | Decision | Why |
|---|---|---|
| ADR-1 | **The UNO Q App is core; the demo is board-only.** Wokwi and an `mcu-only` scenario build are backups. | The hardware demo is the primary deliverable; zero parts means zero wiring risk. |
| ADR-2 | **Phase 0 first:** a hand-written probe App answers every remaining [BOARD] question before the back ends are finished; templates are copied from the board. | Removes hardware risk in week 1. |
| ADR-3 | **Linux never requests pin writes. It only sets firing flags for rules the compiler placed on Linux.** Their actions are compiled into the MCU sketch. | The MCU stays the only writer; messages stay tiny. |
| ADR-4 | **All Bridge traffic is `notify` in both directions.** No `call` in generated code. | Sketch-side `call` has no timeout (source-confirmed). |
| ADR-5 | **Heartbeat carries the full Linux firing mask** plus an acknowledgement (ADR-21), sent on every mask change and every 500 ms. | One message does liveness and state resync. |
| ADR-6 | **NEVER policy: the incumbent wins.** A change that would complete a forbidden pair is refused; if both sides change in the same pass, both are refused. | Deterministic and order-independent. |
| ADR-7 | **Priority: `ENSURE` > `FAILSAFE` > `NEVER` > rules; within rules, `OFF` wins.** ENSURE and FAILSAFE apply whole closures (ADR-20). | One total order; no tie left to chance. |
| ADR-8 | **`ENSURE` conditions use raw sensor values only** (P202 otherwise). | Safety runs on the MCU in the same pass. |
| ADR-9 | **Placement:** must-MCU = `REALTIME`, `SAFETY`, `FAILSAFE`; must-Linux = `NOTIFY`; windows follow the policy (`--place-windows linux` default, or `mcu`); `FOR` follows the rule; default MCU. | Placement is a policy the compiler applies and reports. |
| ADR-10 | **`NOTIFY` is edge-triggered** with an `armed` flag (§9.6). Other actions are level-triggered. | A level notify would spam. |
| ADR-11 | **`!=` is removed**; `==` stays (warning on analog). | `x != c` is not a box. |
| ADR-12 | **`DIGITAL` sensors take no `UNIT`/`RANGE`**; domain {0, 1}. | |
| ADR-13 | **Conflicts through windowed variables are warnings (W103); raw-only conflicts are errors (E103).** | Windows are modelled as independent (over-approximation). |
| ADR-14 | **Windows use ≤ 32 buckets keyed by bucket id, the same integer algorithm everywhere** (§9.5). | Exact agreement across C++, Python and the interpreter. |
| ADR-15 | **One canonical event log** (§9.8) for the interpreter, the host shim, the board and the page. | Mechanical diffing. |
| ADR-16 | **Generated code goes through a board profile** (`uno`, `unoq`). | Boards differ in logging, ADC, LEDs, Bridge. |
| ADR-17 | **ADC-code lowering.** Every comparison on a sensor compiles to an integer test on its code `c`; window averages compare `q·sum OP p·count`. Analyses stay over the reals. | float32 (MCU), float64 (Python) and Fraction (analysis) disagree at boundaries; integers cannot. |
| ADR-18 | **Sample-tick grid.** Window pushes, FOR timers, NOTIFY edges, input latching (sim/scenario), Linux-mask latching, link-state changes and refusal-latch clearing happen only on tick passes (`⌊now/S⌋` changes). | Board behaviour becomes a function of the tick sequence: exact differential tests and conformance. |
| ADR-19 | **NEVER enforcement is a simultaneous fixpoint** (§9.3 step 9). | The v2 single pass can leave violations and depends on pair order (counterexamples in §18). |
| ADR-20 | **ENSURE and FAILSAFE apply static closures** over the NEVER pairs; contradictions are compile errors (E116, E108, E109). | Forcing only the immediate partner is unsound for chains. |
| ADR-21 | **Heartbeat v3 = `heartbeat(seq, mask, ack_t)`; link state is latched and evaluated on ticks.** DOWN if the heartbeat or the acknowledged sample is older than the timeout. | Detects Linux alive but deaf; immune to `millis()` wrap. |
| ADR-22 | **No `Serial`/`Monitor` in the MCU loop on `unoq`.** Events go to Linux by `notify`; `main.py` prints them. | On the UNO Q, `Serial` is the RouterBridge Monitor, which makes blocking RPC calls. |
| ADR-23 | **Handlers are preemption-safe.** MCU Bridge handlers only copy arguments into globals inside `noInterrupts()`; `loop()` snapshots them under the same lock. | With current RPClite, `provide_safe` handlers may run on the bridge thread and preempt `loop()`. |
| ADR-24 | **Input layer outside §9:** `--input pins|sim|scenario` chooses where codes come from; §9 only consumes codes. | Board-only demo, conformance mode and the physical kit share one runtime. |
| ADR-25 | **Never use App Lab Stop or `arduino-app-cli app stop/start` to demonstrate Linux death.** Use the page's pause control or `docker kill`/`pause` scripts; restore with `docker start`/`unpause`. | App Lab Stop resets the MCU (source-confirmed). |
| ADR-26 | **Emitters never use DSL names as identifiers** (index-based names). | DSL names like `int`, `loop`, `lambda` would break generated code. |

---

## 4. The language (NORMATIVE)

### 4.1 Reference programs

```
// examples/greenhouse.iot
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
FAILSAFE heater OFF;
```

The board demo uses `examples/greenhouse_board.iot` (§17): onboard LEDs as actuators, 10 s
windows, `REALTIME 20ms`, and a `dry` NOTIFY rule so that `humidity` is used.

### 4.2 Tokens

| Token | Rule | Examples |
|---|---|---|
| Keyword | `SENSOR ACTUATOR AT ANALOG DIGITAL UNIT RANGE RULE REALTIME WHEN AND FOR THEN ON OFF NOTIFY SAFETY NEVER ENSURE FAILSAFE` | `WHEN` |
| Aggregate | `avg min max` (reserved) | `avg` |
| Identifier | `[a-z][a-z0-9_]*`, no `__`, at most 32 characters, not an aggregate | `temp` |
| Number | `[0-9]+(\.[0-9]+)?`, optionally preceded by `-` immediately followed by a digit | `35`, `-40`, `22.5` |
| Duration | Number immediately followed by `ms`, `s`, `min`, `h`; value in integer ms | `10ms`, `1.5s`, `5min` |
| Unit | `C F PCT LUX CM`; `%` is an alias of `PCT` (also glued: `50%`) | `C` |
| Pin | Uppercase word matching `A[0-9]+`, `D[0-9]+`, `LED[0-9]+`, `LED[0-9]+[RGB]` | `A0`, `LED3`, `LED4B` |
| String | `"…"` on one line; escapes `\"` and `\\` only | `"Overheat"` |
| Operator | `> < >= <= == ..` | `>=` |
| Punctuation | `: ; ( ) ,` | `;` |
| Comment | `//` to end of line | |

Which pins exist is a board property (§11.1); a well-formed pin that the board lacks is S020.
`LED3` is an alias of `LED3R` and `LED4` of `LED4R`.

**Source handling.** Files are read as bytes. A UTF-8 BOM is stripped; CRLF and lone CR become
LF; the result must decode as strict UTF-8 (L000 otherwise). Columns are 1-based Unicode code
points; a tab counts as one column.

**Word lexing.** A word is `[A-Za-z_][A-Za-z0-9_]*`, scanned maximally, then classified:
- all lowercase (digits/underscores allowed): aggregate if `avg|min|max`, else identifier,
  subject to the identifier rule (L007 for a leading `_`, a `__`, or > 32 characters);
- all uppercase: keyword, unit or pin; anything else is L005 with a did-you-mean hint
  (`WHN` → `WHEN`), candidates = keywords ∪ units ∪ pins, Levenshtein ≤ max(1, len/3);
- mixed case: L007 "identifiers are lowercase, keywords are UPPERCASE".

**Number lexing.** ASCII digits only (never `str.isdigit`). A `-` starts a number only when the
next character is a digit; otherwise L001. A `.` continues a number only when a digit follows
(so `-40..-10` is `-40`, `..`, `-10`). After the digits, a maximal letter run is scanned: `ms`,
`s`, `min`, `h` make a duration; any other letters are L003 (hint for `5m`, `5mins`: "write
`5min`"; for a unit letter such as `35C`: "units need a space: `35 C`"). More than 9 integer
digits is L003. `1e3`, `0x10`, `1_000` are L003. `.5` is L001 (hint "write 0.5"); `5.` is L001
(hint "write 5 or 5.0"). A glued `%` is the PCT unit.

**Durations.** The value must be an integer number of ms after exact conversion (`0.5ms` and
`1.0001s` are L003; `0.001s` = 1 ms). A `-` before a duration is L003 ("durations must be
positive"). Zero is S030; more than 24 h is S037.

**Operators.** Longest match: `..`, `>=`, `<=`, `==`, `!=` (L006 "write two rules"), `>`, `<`.
A lone `=` is L001 (hint "use =="); `=>` and `=<` are L001 (hints `>=`, `<=`); `/*` is L001
(hint "only // comments").

**Strings.** On one line; a newline or EOF before the closing quote is L002 at the opening quote.
Escapes other than `\"` and `\\`, and control characters, are L004. NOTIFY text must be printable
ASCII (0x20–0x7E) and 1–64 characters long (L008).

**Confusables.** U+2212 MINUS SIGN, U+2013 EN DASH, U+201C/U+201D quotes, U+00A0 NBSP, U+FF1E
and full-width or other non-ASCII digits are L001 with a hint naming the ASCII character.

**Errors and recovery.** The lexer never stops: an erroneous character or token becomes an
`ERROR` token and lexing continues (after an unterminated string, at the next line).

Lexer edge cases that must have tests: every example in this section, plus `5min` vs `min(` vs
`5 min` (NUMBER then AGG → P001 hint "write `5min`"), `10 ms` (P001 "expected duration", hint
"write `10ms`"), `Temp` (L007), lowercase keywords (`when` → P001 with hint "keywords are
UPPERCASE: WHEN"), lowercase pins/units (`a0`, `c`), `//` inside a string, a comment at EOF
without a newline.

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

LL(1): every alternative is decided by one token. Declarations may appear in any order; names are
resolved after parsing (forward references are allowed). Targeted P001 hints: `WHEN 35 < temp`
("write `temp > 35`"), `THEN fan ON, heater OFF` ("write two rules"), `FOR` inside ENSURE ("FOR is
not allowed in ENSURE"), `UNIT`/`RANGE` on a DIGITAL sensor ("DIGITAL sensors take no UNIT or
RANGE"), `RANGE` before `UNIT` (canonical order shown).

### 4.4 Static meaning

- `AT` binds a pin; `ON`/`OFF` are states.
- Conditions are `AND`-only. Each condition lowers to a **box**: one interval per variable.
- A number's unit is optional; default is the sensor's unit. A unit of the same dimension is
  converted exactly at compile time (`C = (F − 32) × 5/9`, `F = C × 9/5 + 32`); a different
  dimension is S010. Units on a `DIGITAL` comparison are S013. Dimensions: temperature (`C`,
  `F`), ratio (`PCT`), illuminance (`LUX`), distance (`CM`).
- `FOR d`: the condition must have held continuously for `d` (measured on ticks, §9.6).
- `REALTIME d`: the MCU pass interval must not exceed `d` (measured, §9.7). Only allowed on
  rules that set an actuator (S034).
- Names share one namespace (sensors, actuators, rules); duplicates are S002.

---

## 5. Front end

### 5.1 Source and lexer (`source.py`, `lexer.py`)
`source.py` loads bytes, normalises (§4.2), computes the normalised SHA-256 and provides
`write_text(path, s)` (UTF-8, LF, asserts no `\r`, exactly one trailing newline). The lexer is
hand-written, single pass, and produces `Token(kind, text, value, span)`; numbers keep their
source text so the middle end builds exact `Fraction`s from it; durations carry integer ms.

### 5.2 Parser (`parser.py`)
Recursive descent, one function per nonterminal, one-token lookahead. Error recovery is panic
mode:
- `ERROR` tokens from the lexer are skipped silently (their L-diagnostic already exists) and put
  the parser into panic mode without a new P001.
- On a syntax error: record one P001 "expected X, found Y" (token descriptions such as
  "identifier `when`", "end of file"), then skip to the sync set {`;` (consumed), `SENSOR`,
  `ACTUATOR`, `RULE`, `SAFETY`, `FAILSAFE`, EOF}. If no token was consumed since the previous
  error, consume one token first (progress guarantee).
- At most 50 errors are reported, then one note "N further errors suppressed".
- If any L or P error exists, compilation stops after parsing (no sema cascades).

### 5.3 AST (`ast.py`) — frozen dataclasses

| Node | Fields |
|---|---|
| `Program` | sensors, actuators, rules, safeties, failsafes (tuples, source order), source |
| `SensorDecl` | name, pin, kind (`ANALOG`/`DIGITAL`), unit?, lo?, hi? (source text), span |
| `ActuatorDecl` | name, pin, span |
| `Rule` | name, deadline_ms?, guard: tuple[`Cmp`], hold_ms?, action, span |
| `Cmp` | value (`SensorRef` \| `Agg`), op, number (source text), unit?, span |
| `SensorRef` | name, span |
| `Agg` | fn, sensor, window_ms, span |
| `SetState` / `Notify` | actuator, state, span / text, span |
| `Never` | (actuator, state, span), (actuator, state, span), span |
| `Ensure` | actuator, state, guard, span |
| `Failsafe` | actuator, state, span |

### 5.4 Semantic checker (`sema/`)
Order: symbol table (`symbols.py`) → name resolution → units (`units.py`) → pins (`pins.py`, using
the board's pin map) → per-construct checks (`checker.py`). After this stage, all thresholds are
`Fraction`s in the sensor's unit and later stages never see units. Did-you-mean for S001 only
considers names of the expected kind, distance ≤ max(1, len/3) capped at 2, ties broken by source
order.

### 5.5 Diagnostics (`diagnostics.py`)

```
error[S001]: unknown sensor 'temprature'
  --> greenhouse.iot:6:17
   |
 6 | RULE cool: WHEN temprature > 35 THEN fan ON;
   |                 ^^^^^^^^^^ did you mean 'temp'?
```

- Severities: `error`, `warning`, `info`. Diagnostics are sorted by (line, col, code).
- The caret line copies tabs from the source and widens East-Asian wide characters to 2 columns;
  multi-line spans underline to end of line. Analysis findings can carry `related` spans (e.g.
  E103 points at both rules).
- `--json` prints one object:
  `{"version": 1, "file": …, "diagnostics": [{code, severity, message, line, col, end_line,
  end_col, hint, related: [{line, col, end_line, end_col, message}], data: {…}}], "reports":
  {…}}`. `end_col` is exclusive; `hint` is a string or null; `data` holds machine values such as
  `overlap`, `gap`, `codes`.
- stdout and stderr are reconfigured to UTF-8; `--ascii` (automatic when the stream cannot encode
  UTF-8) maps `✓ ⚠ ∈ − →` to `[ok] [!] in - ->`. ANSI colour only on a TTY (`--color
  auto|always|never`).
- Exit codes: 0 no errors (warnings allowed), 1 any error, 2 usage/IO error, 3 internal compiler
  error (message "internal compiler error, please report"; traceback with `--debug`).
  `--werror` turns warnings into errors.

---

## 6. Diagnostic catalog (NORMATIVE)

Prefixes: `L` lexer, `P0` parser, `S` semantics, `W/E/I 1xx` interval and safety analysis, `P2`
placement and resources.

| Code | Sev | Meaning | Trigger example |
|---|---|---|---|
| L000 | E | File is not valid UTF-8 | cp1252 byte in a comment |
| L001 | E | Unknown or misplaced character (incl. confusables, `.5`, `5.`, `=`, `/*`, lone `-`) | `temp # 3` |
| L002 | E | Unterminated string | `NOTIFY "Hot` |
| L003 | E | Bad number or duration (suffix, glued unit, fractional ms, negative duration, exponent/hex/separator, > 9 digits) | `10sec`, `35C`, `0.5ms`, `-5s` |
| L004 | E | Bad string escape or control character | `"a\nb"` |
| L005 | E | Unknown uppercase word (did-you-mean) | `WHN` |
| L006 | E | `!=` not supported | `temp != 30` |
| L007 | E | Mixed-case word or invalid identifier (`_x`, `a__b`, > 32 chars) | `Temp` |
| L008 | E | NOTIFY text not printable ASCII, empty, or > 64 characters | `NOTIFY "héllo"` |
| P001 | E | Expected X, found Y (with hint) | missing `:` |
| S001 | E | Undeclared name (did-you-mean) | `temprature` |
| S002 | E | Duplicate declaration (one namespace) | two `temp`; rule named `fan` |
| S003 | E | Wrong kind of name | `WHEN fan > 35`, `THEN temp ON` |
| S010 | E | Unit of a different dimension | `temp > 50 PCT` |
| S011 | W | Threshold outside the sensor's `RANGE` | `temp > 200` |
| S012 | E | Empty or inverted `RANGE` | `RANGE 125..-40`, `RANGE 5..5` |
| S013 | E | Unit on a `DIGITAL` comparison | `door == 1 C` |
| S020 | E | Pin does not exist on the board | `AT D22`; `AT LED3` with `--board uno` |
| S021 | E | Pin bound twice (`LED3` ≡ `LED3R`) | fan and heater `AT D7` |
| S022 | E | `ANALOG` sensor on a non-analog pin, or any sensor on an LED pin | `AT D4 ANALOG` |
| S023 | W | Serial pin `D0`/`D1` (also the Zephyr console on UNO Q) | `AT D0` |
| S030 | E | Duration of zero (window, FOR, REALTIME) | `avg(temp, 0s)` |
| S031 | W | Window shorter than 32 sample periods; fewer buckets used | `avg(temp, 1s)` at 100 ms |
| S032 | E | Window buffers exceed the board's RAM budget | many windows on `uno` |
| S033 | W | Aggregate over a `DIGITAL` sensor | `avg(door, 10s)` |
| S034 | E | `REALTIME` on a `NOTIFY` rule | |
| S035 | E | Window shorter than the sample period | `avg(temp, 50ms)` at 100 ms |
| S036 | E | Exact window comparison would overflow 64-bit arithmetic | 9-decimal threshold on a 24 h window |
| S037 | E | Duration longer than 24 h | `avg(temp, 9999h)` |
| S040 | E | `NEVER` pair names the same actuator twice | `NEVER fan ON AND fan OFF` |
| S041 | E | Two `FAILSAFE` lines for one actuator | |
| S042 | W | `==` on an `ANALOG` sensor | `temp == 30` |
| S043 | W | Duplicate `NEVER` pair (same pair, either order) | |
| S050 | W | Sensor declared but never used | |
| S051 | W | Actuator declared but never referenced | |
| W101 | W | Rule can never fire (empty box) | `temp > 40 AND temp < 30` |
| W102 | W | Rule always fires (raw-only box = full domain) | `temp >= -40` |
| E103 | E | Conflict: opposite states, boxes overlap, raw variables only; prints overlap and hint | `temp>35→ON`, `temp<40→OFF` |
| W103 | W | Possible conflict through a windowed variable; prints overlap | `avg(temp)<18→ON`, `temp>40→OFF` |
| W104 | W | No hysteresis: ON and OFF regions touch | `>35 ON`, `<=35 OFF` |
| I105 | I | Hysteresis gap, in units and in ADC codes | `>35 ON`, `<30 OFF` → 5 C (32 codes) |
| W106 | W | Actuator can be set ON but never OFF | |
| W107 | W | `ENSURE` forces NEVER partners (lists the whole closure) | ENSURE fan ON vs NEVER heater ON AND fan ON |
| E108 | E | Two `ENSURE`s with overlapping conditions force opposite states (closures) | |
| E109 | E | `FAILSAFE` closure contradictory, or the initial state violates a `NEVER` | `NEVER pump OFF AND valve OFF` with no FAILSAFE |
| W110 | W | `ENSURE` closure contradicts the `FAILSAFE` closure (ENSURE wins) | |
| W111 | W | A `REALTIME` rule's write can be refused by `NEVER` or overridden by ENSURE/FAILSAFE | `cool` vs NEVER heater/fan |
| W112 | W | Hysteresis dead band narrower than ADC jitter (`2J+1` codes) | `>35 ON`, `<34.9 OFF` |
| W113 | W | Box contains no ADC code: can never fire on the board | `temp > 35 AND temp < 35.1` |
| E116 | E | `ENSURE` closure contradicts itself | `NEVER fan ON AND heater ON; NEVER fan ON AND heater OFF; ENSURE fan ON …` |
| W117 | W | Actuator can never turn ON (only OFF setters, initial state OFF) | |
| I118 | I | Windowed rule fires whenever its window has data | `avg(temp,10s) >= -40` |
| W119 | W | `FAILSAFE` closure overrides an actuator driven by an MCU rule | `FAILSAFE fan OFF` with REALTIME `cool` |
| W120 | W | `FOR` shorter than, or not a multiple of, the sample period (effective hold rounds up) | `FOR 150ms` at 100 ms |
| W121 | W | `REALTIME` deadline below the board's floor | `REALTIME 2ms` on unoq |
| I122 | I | LED-matrix display truncated (too many sensors, rules or actuators) | |
| P201 | E | `REALTIME` rule uses a window that the placement policy puts on Linux | `REALTIME` + `avg` (policy linux) |
| P202 | E | `ENSURE` condition uses a windowed value | `ENSURE fan ON WHEN avg(temp,1min) > 40` |
| P203 | E | More than 32 Linux-placed SetState rules (mask width) | |
| P204 | E | More than 16 sensors | |
| P205 | W | Estimated Bridge traffic above 30 % of the link | `--sample-ms 10` with 16 sensors |

Hysteresis checks (W104/I105/W112) apply only to (ON, OFF) rule pairs for one actuator whose
guards each constrain exactly one, identical, non-integer variable and whose boxes are disjoint.

---

## 7. Rule IR and lowering (`ir.py`, `lower.py`, `adc.py`)

The IR types live in `iotc/ir.py` (frozen dataclasses). Summary:

```python
Var = tuple[str, str | None, int | None]   # (sensor, agg_fn | None, window_ms | None)

Bound(value: Fraction, closed: bool)
Interval(lo: Bound, hi: Bound, integer: bool)     # is_empty, intersect, touches, contains,
                                                  # gap_to (non-negative distance)
CodeTest(var: Var, kind: "range" | "avg", ...)    # compiled integer test (ADR-17)
IRSensor(id, name, pin, kind, unit, lo, hi, adc_max)
IRActuator(id, name, pin, active_low, initial, failsafe)
IRWindow(id, sensor_id, window_ms, buckets, placement)
IRRule(id, name, guard: dict[Var, Interval], tests: tuple[CodeTest, ...], hold_ms, action,
       deadline_ms, placement, reasons, mask_bit, span)
IRNever(id, a, s1, b, s2, span); IREnsure(id, actuator, state, guard, tests, closure, span)
IRFailsafe(actuator, state, span)
IRProgram(sensors, actuators, windows, rules, nevers, ensures, failsafes, fs_closure, init,
          sample_ms, heartbeat_ms, failsafe_timeout_ms, board, target, source_sha)
```

**Real boxes.** `temp > 35` lowers to `(35, 125]`; `temp >= 35` to `[35, 125]`; comparisons on
the same variable intersect. A windowed value is its own variable with the sensor's domain plus
"empty" (over-approximation: independent of the raw reading). DIGITAL raw/min/max variables are
integer-typed and normalised to closed integer bounds; `avg` over a DIGITAL sensor is real-valued
in [0, 1]. Windows are keyed by `(sensor, window_ms)`; `avg/min/max` over the same key share one
buffer.

**Codes (ADR-17).** Each sensor has an affine code map. ANALOG: `x(c) = lo + c·(hi−lo)/A` with
`c ∈ [0, A]`, `A = adc_max` from the board profile (1023 on both boards). DIGITAL: `x(c) = c`,
`c ∈ {0, 1}`. For a threshold `T` let `r = x⁻¹(T)` as a Fraction. Then:

| Comparison | Integer test on code `c` |
|---|---|
| `x > T` | `c ≥ ⌊r⌋ + 1` |
| `x ≥ T` | `c ≥ ⌈r⌉` |
| `x < T` | `c ≤ ⌈r⌉ − 1` |
| `x ≤ T` | `c ≤ ⌊r⌋` |
| `x == T` | `c == r` if `r` is an integer, else constant false |

Raw, `min` and `max` variables use these tests; all tests on one variable merge into one code
range `[cl, ch]`, clamped to `[0, A]` (empty → W113 when the real box is non-empty). `avg`
compares `sum/count OP r` as `q·sum OP p·count` with `r = p/q` in lowest terms (after clamping
`r` into `[−1, A+1]`), in 64-bit (u64/i64 on the MCU, unbounded ints in Python); S036 if
`q·A·maxcount` or `|p|·maxcount` could reach 2⁶³. For RANGE −40..125, `r = 6.2·(T + 40)`
exactly, so `temp > 35` ⇔ `c ≥ 466` and `temp < 30` ⇔ `c ≤ 433`.

Rule ids are assigned in source order; Linux-placed **SetState** rules additionally get a mask
bit in source order. `Ensure`/`Never` lower to `IREnsure`/`IRNever` (1-based numbering in
messages: `ENSURE#1`, `never#1`); `Failsafe` to `IRFailsafe`.

---

## 8. Analyses (`analysis/`)

### 8.1 Interval analysis (`intervals.py`)
- W101 (empty real box), W113 (empty code box on the board), W102 (raw-only full box), I118
  (windowed full box) per rule.
- For each actuator, every (ON-rule, OFF-rule) pair: intersect boxes. Non-empty → E103 (both
  guards raw-only) or W103, with the overlap printed only for variables at least one rule
  constrains, e.g. `temp ∈ (35, 40)`, and the hint "OFF wins at runtime; add `AND temp <= 35` to
  `cool`".
- Same pairs, single shared non-integer variable, disjoint: touching → W104; otherwise I105
  with the gap in units and the dead band in codes (number of codes in neither box), and W112 if
  the dead band is narrower than `2J+1` codes (`J` = ADC jitter from the profile).
- ON-setters(a) = rules setting `a := ON` plus ENSURE/FAILSAFE closures assigning `a := ON`;
  OFF-setters likewise. W106: ON-setters non-empty, OFF-setters empty (on `unoq` a FAILSAFE
  closure entry `a := OFF` counts as an OFF-setter). W117: initial OFF, no ON-setters, some
  OFF-setters. S051: actuator never referenced.

### 8.2 Placement (`placement.py`) — ADR-9
```
for rule in rules:
    if rule.deadline:             must_mcu   += "REALTIME {d}"
    if action is Notify:          must_linux += "NOTIFY leaves the MCU"
    if uses window and policy=linux: policy_linux += "{agg}({s}, {w}): window history kept on Linux"
    if must_mcu and policy_linux: P201 (name the window and its span; hint --place-windows=mcu)
    elif must_linux or policy_linux: LINUX
    else: MCU ("default: no Bridge hop")
SAFETY, FAILSAFE → MCU always
ENSURE using a window → P202
--target mcu-only → every rule MCU; the table is still printed as "plan for --target unoq"
```
Sensors are always read on the MCU, which streams codes every tick (unoq). Windows used by MCU
rules are computed on the MCU; windows used by Linux rules on Linux; a window used by both is
computed on both (identical algorithm).

Expected placement table for `greenhouse.iot`:
```
rule      runs on  reason
cool      MCU      REALTIME 10ms
uncool    MCU      REALTIME 10ms
warm      Linux    avg(temp, 5min): window history kept on Linux
unwarm    Linux    avg(temp, 5min): window history kept on Linux
alert     Linux    NOTIFY leaves the MCU
SAFETY    MCU      MCU owns the pins and can veto any write
FAILSAFE  MCU      applied by the MCU when Linux heartbeats stop
```

### 8.3 Safety analysis (`closure.py`, `safety.py`)

**Closure.** `closure(a, s)` is computed by a worklist: start with `a := s`; for each assignment
`x := v` taken from the list and each NEVER pair with side `(x, v)` and other side `(y, w)`,
require `y := ¬w`; if the closure already holds `y := w`, the closure is contradictory;
otherwise add `y := ¬w`.

- E116: `closure(ENSURE k)` contradictory.
- E108: two ENSUREs whose condition boxes intersect (exact, raw-only) and whose closures assign
  opposite states to some actuator; the message names the actuator and the overlap.
- FS = ∪ closure(a, failsafe(a)) over all FAILSAFE lines; E109 if contradictory.
- Initial state `init(a)` = FS(a) if assigned, else OFF. E109 if `init` violates any NEVER (hint
  "add `FAILSAFE valve ON`").
- W110: an ENSURE closure assigns an actuator the opposite of FS (ENSURE wins).
- W107: an ENSURE closure has more than one entry; lists all forced partners.
- W119 (unoq): an FS entry assigns an actuator that an MCU-placed rule sets ("`cool` is
  disabled while Linux is down").
- W111: a REALTIME rule's actuator appears in a NEVER pair, an ENSURE closure or FS.

**NEVER proofs.** A pair `(a, ON), (b, ON)` is **proven** (✓) iff for every rule R requesting
`a := ON` there is a rule R′ requesting `b := OFF` with `box(R) ⊆ box(R′)`, R′ without `FOR`,
and the same placement as R; and symmetrically for rules requesting `b := ON`. (Proof: OFF wins
within a pass, and immune values carry closures; §9.3.) Pairs with an OFF side are never
reported proven. Otherwise the report says **not proven, enforced at runtime** (⚠) and names an
uncovered rule and, if one exists, an overlapping request of the other side as witness.

```
⚠ NEVER heater ON AND fan ON      not proven: warm (heater ON on avg(temp,5min) < 18) is not
                                   covered by a fan OFF rule; cool (fan ON on temp > 35) can overlap
                                   → enforced by the MCU monitor (incumbent wins)
✓ ENSURE fan ON WHEN temp > 50    enforced by the MCU monitor; closure: fan ON, heater OFF (W107)
✓ FAILSAFE heater OFF             applied after 2000 ms without a fresh heartbeat
```

### 8.4 Code thresholds and resources (`report.py`)
`iotc check` prints:
- **Code thresholds** per comparison (`temp > 35  ⇔  code ≥ 466  (35.16 C)`).
- **Resources** per board: window RAM (`slot bytes × buckets × windows`), the largest Bridge
  message, Linux mask bits used, sample rate, and the estimated Bridge bytes/s against the link
  budget (115200 baud ≈ 11,520 B/s; P205 above 30 %).
- **Matrix layout** summary (I122 if truncated).

---

## 9. Runtime semantics (NORMATIVE)

Implemented three times: generated C++ (`iotc_gen.h`), generated Python (`main.py` +
`iotc_runtime.py`) and the interpreter (`interp.py` + `runtime/`). Symbols: `S` = sample_ms
(default 100), `H` = heartbeat_ms (500), `T` = failsafe_timeout_ms (2000).

### 9.1 State
- `cur[a]` ∈ {ON, OFF}: applied state of each actuator; initial value `init[a]` (§8.3), written
  in `setup()` **before** anything else, including `Bridge.begin()`; for active-low pins the
  inactive level is written immediately after `pinMode`.
- Inputs: `code[s]` per sensor (§9.2).
- Ticks: `n = ⌊now64 / S⌋`, `τ = n·S`; `lastN`. `now64` extends `millis()` to 64 bits.
- Windows (§9.5), FOR timers `since[r]`, `held[r]` (§9.6), NOTIFY `armed[r]`.
- Link (unoq): `maskL` (latched mask), `linkUp` (latched), `everBeat`, `lastBeat`, `ackT`,
  `lastSeq`; handler globals `rxSeq, rxMask, rxAck, rxBeatMs, rxFresh` (ADR-23).
- Refusal latch `refusedVal[a]` ∈ {NONE, ON, OFF}.

### 9.2 Input layer (ADR-24; outside the semantics)
Each pass consumes one code per sensor:
- `pins`: ANALOG → `analogRead` every pass (10-bit; a negative return is a fault: keep the last
  good code, count it); DIGITAL → `digitalRead` with `INPUT_PULLDOWN` (pressed to 3V3 = 1). No
  `pinMode` on analog pins.
- `sim`: codes from `sim_set(sensor, code)` messages, latched on ticks. If no `sim_set` arrived
  for 3000 ms (Linux sends all values every 500 ms), the compiled-in **fallback scenario** drives
  the codes (looping), until `sim_set` resumes.
- `scenario`: rows of a compiled-in trace drive the codes, one row per tick, starting at the
  scenario start (§9.9); before the start, rule evaluation is suspended and actuators hold `init`.
The interpreter consumes codes from a trace (§10.1); the input layer is not modelled.

### 9.3 One MCU pass (in order)
1. **Snapshot** handler globals under `noInterrupts()`; clear `rxFresh`.
2. **Tick.** `tick := (n ≠ lastN)`; if tick, `lastN := n`.
3. **Inputs** for this pass (§9.2).
4. **On ticks only:**
   a. *Link* (unoq): if a fresh heartbeat was snapshotted since the last tick:
      `everBeat := true; maskRx := rxMask; lastBeat := rxBeatMs; ackT := rxAck; lastSeq := rxSeq`.
      `up := everBeat ∧ (int32)(now − lastBeat) < T ∧ (int32)(τ − ackT) < T`. If `up ≠ linkUp`,
      set `linkUp := up` and log `LK`. If `linkUp`, `maskL := maskRx`. (Once DOWN, the link only
      comes back with a fresh heartbeat.)
   b. *Windows:* push `code[s]` at time `τ` into every MCU-side window of `s`.
   c. *FOR timers* of MCU rules (§9.6). *NOTIFY* edges of MCU-placed NOTIFY rules (mcu-only).
   d. *Refusal latch:* for every actuator, if the proposal of the previous pass equalled `cur`,
      `refusedVal := NONE` (see step 10).
5. **MCU rules**, source order: a SetState rule *fires* iff its code tests hold now (raw codes of
   this pass, windows as of the last tick) and (if FOR) `held[r]`. A firing rule adds a request
   `(a, s, r)`.
6. **Linux rules** (unoq): if `linkUp`, for each set bit of `maskL` add that rule's request.
7. **Resolve** per actuator: any OFF request → `OFF`; else any ON → `ON`; else `next[a] =
   cur[a]`. Proposal `p[a] := next[a]` is recorded after step 9.
8. **FAILSAFE** (unoq, `¬linkUp`): apply every FS entry, mark it `failsafe` (immune).
9. **ENSURE**, source order: if the condition holds, apply every entry of its closure, mark it
   `forced` (immune), overriding FS entries. (E108 rules out disagreement between ENSUREs.)
   Then record `p[a] := next[a]` for all `a`.
10. **NEVER fixpoint.** Repeat at most N_ACT times: let V be the pairs `(a,s1),(b,s2)` with
    `next[a]=s1 ∧ next[b]=s2`; if V is empty, stop; R = every side `x` of a pair in V with `x`
    not immune and `next[x] ≠ cur[x]`; for each `x` in R: `next[x] := cur[x]`, mark `refused`
    with `refusedBy[x]` = the lowest NEVER index among the violated pairs containing `x`.
    *Invariant:* if `cur` satisfies every NEVER, R is never empty while V is non-empty (closures
    guarantee every violated pair has two non-immune sides, one of which differs from `cur`), so
    the loop ends with no violation; rounds are set-based, so the result does not depend on the
    order of NEVER lines.
11. **Commit and log**, actuators in declaration order:
    - `next[a] ≠ cur[a]`: write the pin, log `EV` with result `forced` (src `ENSURE#k`, lowest
      k whose closure assigned it), `failsafe` (src `FAILSAFE`), or `applied` (src = lowest rule
      id among the requesters of `next[a]`); set `refusedVal[a] := NONE`.
    - `next[a] = cur[a] ≠ p[a]` (refused): if `refusedVal[a] ≠ p[a]`, log `EV` with result
      `refused`, src = lowest rule id requesting `p[a]`, detail `never#k`; set
      `refusedVal[a] := p[a]`.
    - `cur := next`.
12. **Stream** (unoq): on ticks `notify sample(τ, codes…)`; every 1000 ms `notify stats`; one
    `notify hb_ack(seq)` in the first pass after each fresh heartbeat.
13. **Matrix:** compute the frame; `draw()` only if it changed (§11.8).

The loop never blocks: no `delay()`, no Bridge `call`, no `Serial`/`Monitor` on unoq (ADR-22).

### 9.4 Linux side (unoq)
On each `sample(τ, codes)`:
- **Reset detection:** if a previous `τ'` exists and `(τ − τ') mod 2³² ≥ 2³¹` (time went
  backwards: MCU reset), clear windows, FOR timers and NOTIFY state, set mask 0, log
  `I,<τ>,mcu reset`.
- Push codes into Linux windows; update FOR timers (§9.6); evaluate Linux SetState rules into
  the mask (bit set iff code tests hold and, if FOR, held); NOTIFY rules fire on edges (§9.6) and
  log `NT` with time `τ`.
- If the mask changed, send `heartbeat(seq, mask, ackT=τ)` at once; in any case send one at
  least every `H` ms (monotonic clock). `seq := (seq + 1) mod 2³²`.

A **paused** Linux (demo control) neither evaluates nor sends heartbeats; the page and sliders
keep working. A **killed** Linux (container killed) does nothing; restored with `docker start`
it begins with empty state.

### 9.5 Windows (ADR-14)
For a window of `W` ms: `B = min(32, ⌊W/S⌋)` (`W < S` is S035). The bucket id of tick time `τ`
is `k(τ) = ⌊τ·B/W⌋` (64-bit). Slot `k mod B` stores `id, sum (u32), count (u32), min (u16),
max (u16)`; pushing into a slot whose `id ≠ k` resets it first. The window value at the latest
push time `τ*` aggregates the slots whose `id ∈ (k(τ*) − B, k(τ*)]` and `count > 0`; if there
are none, the window is **empty** and every comparison on it is false. Gaps of any length are
handled by the id check. All implementations align to `τ = 0`.

### 9.6 FOR timers and NOTIFY
On each tick (MCU) or sample (Linux) at time `τ`, for a rule with FOR `h`:
- box true: if `since` unset, `since := τ`; `held := held ∨ (τ − since ≥ h)`;
- box false: unset `since`, `held := false`.
A FOR rule fires iff its box holds in the pass and `held`. Times are 64-bit (Linux: unwrapped
τ), so `millis()` wrap is harmless.

A NOTIFY rule has `armed` (initially true). On a tick or sample where `box ∧ (held or no FOR)`
holds and `armed`: log `NT`, `armed := false`. Where it does not hold: `armed := true`. An empty
window counts as false. A Linux restart loses `armed` (one duplicate notification is possible).

### 9.7 REALTIME
Deadline `d`: the interval between two successive passes must not exceed `d`. The MCU measures
with `micros()` (resolution ≈ 31 µs on UNO Q [BOARD]), counts overruns, keeps the maximum pass
interval and reports both in `stats` every second. REALTIME is measured, not enforced. The
pass floor on UNO Q is ≈ 1 ms (the Bridge loop hook sleeps 1 ms); W121 below the profile's
`MIN_DEADLINE_MS`.

### 9.8 Event log (one format everywhere, ADR-15)
```
LK,<t>,UP|DOWN
EV,<t>,<actuator>,<ON|OFF>,<src>,<applied|refused|forced|failsafe>,<detail>
NT,<t>,<rule>,"<text>"
SY,<t>,<iteration>
ST,<t>,<overruns>,<max_pass_us>,<faults>
I,<t>,<message>
```
`src` is a rule name, `ENSURE#k` or `FAILSAFE`; `detail` is `-` or `never#k`. NOTIFY text is
quoted with RFC 4180 doubling. **Canonical order** of a log: by `t`, then kind (LK < EV < NT <
SY), then actuator or rule index. Comparisons (differential, co-simulation, conformance) use the
canonical order and exclude `ST` and `I` lines.

### 9.9 Scenario start (conformance)
In `--input scenario` on unoq, the scenario starts at the first tick after the link is UP; on
mcu-only, at the first tick. At the start, the MCU resets its windows, FOR timers and NOTIFY
state, sets `maskL := 0`, sends `sync(τ, iteration)`, logs `SY`, and from then on latches only
heartbeats with `ackT ≥ τ_start`. Linux resets its engine state on `sync`. Each loop of the
scenario starts again with a `sync`.

### 9.10 Interpreter time model
One trace row = one MCU tick pass at time `t_i` (a multiple of S; strictly increasing; gaps
allowed). For row `i` (unoq):
1. Deliver the heartbeat Linux sent while processing row `i−1`, if any (`lastBeat := t_i`).
2. Run the MCU pass (§9.3) at `now = t_i` with the row's codes.
3. If Linux is alive (`t_i <` kill time, or ≥ revive time), Linux processes the row's sample
   (§9.4) and may send a heartbeat (mask changed, or `t_i − lastSent ≥ H`, or never sent),
   delivered at row `i+1`.
`--kill-linux-at K` stops Linux from the first row with `t ≥ K`; `--revive-linux-at R` restarts
it with empty state. `--link-up-at-start` models a scenario start (§9.9): link UP, mask 0. The
model assumes Bridge latency < S; the conformance mode measures whether this holds on the board.

---

## 10. Interpreter and fuzzer (`interp.py`, `fuzz.py`)

### 10.1 Traces
```
# trace.csv — units are quantised: c = clamp(⌊(v−lo)·A/(hi−lo) + 1/2⌋, 0, A)
t_ms,temp,humidity
0,25.0,40
100,25.4,40
# or raw codes:   t_ms,temp:raw,humidity:raw
```
Rows must have strictly increasing `t_ms` that are multiples of S, one column per sensor, and
DIGITAL values in {0, 1}; otherwise exit 2 with the row number.

`iotc run f.iot --trace t.csv [--target unoq|mcu-only] [--kill-linux-at T] [--revive-linux-at T]
[--link-up-at-start]` prints the canonical log.

### 10.2 Fuzzer
Generators: random walks, spikes, plateaus at codes `k−1, k, k+1` around every threshold code
`k`, jitter segments (`±J` codes held ≥ 1 s), Linux death and revival. Default 10,000 traces,
`--seed`. Invariants checked after every pass:
1. No NEVER pair holds in `cur`.
2. If ENSURE k's condition holds in a pass, every entry of its closure holds after that pass.
3. If `¬linkUp` in a pass (unoq), every FS entry holds after the pass unless an active ENSURE
   closure assigns that actuator.
4. Every EV `applied/forced/failsafe` line corresponds to a change of `cur`, and every change of
   `cur` has exactly one such line.
5. Chatter (input-aware): within a jitter segment of length ≥ 1 s, an actuator whose hysteresis
   dead band is ≥ 2J+1 codes toggles at most once.

On a violation: shrink (delete halves, then single rows, re-time rows onto the grid, while it
still fails) and save `tests/traces/regress_<hash>.csv`. **Mutant self-checks** must each produce
a violation: `--mutant disable-monitor` (no step 10), `single-pass-never` (v2 step 7),
`fs-over-ensure` (step 8 after step 9). Every program whose report says ✓ for a NEVER pair must
also survive `--mutant disable-monitor` (that is the test of the proof).

---

## 11. Back ends

### 11.1 Board profiles (`backend/profiles.py`)

| Concern | `uno` (Wokwi / ATmega328P) | `unoq` (STM32U585 MCU, Zephyr core) |
|---|---|---|
| FQBN | `arduino:avr:uno` | `arduino:zephyr:unoq` (App Lab adds `:wait_linux_boot=app`) |
| Pins | A0–A5, D0–D13 | A0–A5, D0–D13, LED3R/G/B, LED4R/G/B (`LED3`=`LED3R`, `LED4`=`LED4R`) |
| ADC | 10-bit, 5 V, `A = 1023` | 10-bit default (14-bit hardware), 3.3 V, `A = 1023` |
| Jitter `J` (codes) | 2 | 2 [BOARD, Phase 0] |
| Onboard LEDs | — | `LED3_R…LED4_B`, active-low; `LED3_*` also PWM (unused) |
| LED matrix | — | 8×13, `Arduino_LED_Matrix`, `draw(uint8_t[104])`, 3-bit grey |
| Logging | `Serial` @115200, EV lines printed | none in `loop()`; events by `notify` (ADR-22) |
| Bridge | none | `#include <Arduino_RouterBridge.h>` (bundled with the platform) |
| Window RAM budget | 1024 B → S032 | not a constraint |
| `MIN_DEADLINE_MS` | 1 | 5 [BOARD] |
| D0/D1 | `Serial` | `Serial1`, also the Zephyr console |

### 11.2 Targets and inputs

| `--target` | Output | Use |
|---|---|---|
| `unoq` (default for `--board unoq`) | App folder (§11.7) | the demo |
| `mcu-only` | one sketch folder | Wokwi (`--board uno`); UNO Q MCU alone (`--board unoq`, LEDs + matrix only, compiled with `:wait_linux_boot=no`) |

`--input pins|sim|scenario` (default `pins`); `--scenario file.csv` (required for `scenario`,
optional fallback for `sim`); `--demo-controls` adds the pause control to the page;
`--no-matrix` disables the matrix renderer.

### 11.3 MCU sketch shape (`sketch/iotc_gen.h`, generated)
`sketch.ino` is a thin wrapper: `#include "iotc_gen.h"`, `setup(){iotc::setup();}`,
`loop(){iotc::loop();}`. All generated code is valid standalone C++ (no reliance on Arduino
prototype generation) inside `namespace iotc`, with index-based names and `static const`
tables:

```cpp
// GENERATED by iotc 0.3.0 from greenhouse_board.iot sha256:3f2a… — do not edit
#pragma once
#include <Arduino.h>
#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>
namespace iotc {
static const uint8_t N_SENS = 2, N_ACT = 2, N_RULE = 6, N_NEVER = 1, N_ENSURE = 1;
static const pin_size_t ACT_PIN[N_ACT] = { LED4_B, LED3_R };      // 0: fan, 1: heater
static const bool ACT_ACTIVE_LOW[N_ACT] = { true, true };
static const uint8_t INIT[N_ACT] = { 0, 0 };
// … code tests, closures, windows, NEVER table, name tables (escaped string literals)
static volatile uint32_t rxSeq, rxMask, rxAck, rxBeatMs; static volatile bool rxFresh;
static void onHeartbeat(uint32_t seq, uint32_t mask, uint32_t ack) {
  noInterrupts(); rxSeq = seq; rxMask = mask; rxAck = ack; rxBeatMs = millis(); rxFresh = true; interrupts();
}
static bool rule_0(const uint16_t* c) { return c[0] >= 466; }      // cool: temp > 35
// … setup(): write init → Bridge.begin() (false → error glyph, stay FS) → provide_safe → matrix
// … loop(): §9.3 steps 1–13
}
```

Emitter rules: no `delay()`, no Bridge `call`, no `Serial`/`Monitor` in `loop()` on unoq, no
user text in comments (comments carry only `[a-z0-9_]` names), unsigned/64-bit time arithmetic,
`pin_size_t` for pins, no zero-length arrays (size-1 dummies), `noInterrupts()` around handler
globals, `uint64_t` for window comparisons.

### 11.4 Escaping (`escape.py`)
- C++ string literals: printable ASCII except `\`, `"`, `?` as is; everything else as 3-digit
  octal escapes of the UTF-8 bytes (no trigraphs, no greedy `\x`).
- Python: `repr()`.
- CSV: RFC 4180 (quote, double quotes).
- JSON inside HTML: `json.dumps` with `<`, `>`, `&`, U+2028, U+2029 escaped as `\uXXXX`.
- The page sets text only through `textContent`; a CSP meta tag restricts scripts to `'self'`.

### 11.5 Bridge contract (`backend/bridge.py` — the only place names live)

| Name | Direction | Payload (exact arity) | Purpose |
|---|---|---|---|
| `sample` | MCU → Linux | `t:u32, c0…cN−1:u16` | codes on each tick |
| `event` | MCU → Linux | `t:u32, act:u8, state:u8, src:u8, result:u8, detail:u8` | EV lines |
| `link` | MCU → Linux | `t:u32, up:u8` | LK lines |
| `stats` | MCU → Linux | `t:u32, overruns:u32, max_pass_us:u32, faults:u32` | ST lines |
| `hb_ack` | MCU → Linux | `seq:u32` | Bridge round-trip measurement |
| `sync` | MCU → Linux | `t:u32, iteration:u16` | scenario start (§9.9) |
| `heartbeat` | Linux → MCU | `seq:u32, mask:u32, ack:u32` | liveness + Linux rule flags |
| `sim_set` | Linux → MCU | `sensor:u8, code:u16` | `--input sim` |

Encodings: `src` < 0xF0 is a rule id; `0xF0 + k` is ENSURE k+1; `0xFF` is FAILSAFE. `result`:
0 applied, 1 refused, 2 forced, 3 failsafe. `detail`: `0xFF` none, else NEVER index (0-based).
Linux→MCU messages must be ≤ 256 bytes (dropped by the router otherwise); MCU→Linux messages are
limited by the 115200-baud link and a 1 KB transmit buffer (P205). MCU handlers have fixed arity;
Python coerces every argument (`int(x) & 0xFFFFFFFF` etc.).

### 11.6 Linux program shape (`python/main.py`, generated)
- Imports only `arduino.app_utils` (`App`, `Bridge`, `Leds`), `arduino.app_bricks.web_ui`
  (`WebUI`) and the stdlib, plus `iotc_runtime` (a byte copy of `iotc/runtime/`).
- Generated tables and per-rule predicate functions for the Linux rules (`def r2(c, w): …`,
  integer tests only).
- Bridge handlers only `put` into a bounded `queue.Queue(maxsize=512)` (drop oldest, count
  drops); `loop()` drains it, runs the engine (§9.4), sends heartbeats, prints canonical log lines
  (visible in App Lab's Python console and `arduino-app-cli app logs`), and pushes page updates
  (≤ 5 Hz). Every handler and the loop body are wrapped in try/except (log and continue): there
  is no automatic restart.
- `Leds.set_led1_color(0, 1, 0)` / `(0, 0, 0)` toggles LED1 on each heartbeat sent ("Linux
  alive"), guarded by try/except [BOARD].
- All registrations (`Bridge.provide`, `ui.on_message`, `ui.on_connect`, `ui.expose_api`) happen
  before `App.run(user_loop=loop)`; `on_connect` sends a full snapshot; `GET /api/state` returns
  it.
- Pure logic (engine, windows, FOR, NOTIFY, predicate evaluation) is separate from I/O so the
  co-simulation can import it with stubbed `arduino.*` modules.

### 11.7 App folder (`app_writer.py`)
```
build/<app>/
  app.yaml                # from template; name, description, icon (exactly one emoji); bricks: [arduino:web_ui]; no ports
  sketch/sketch.ino       # thin wrapper
  sketch/iotc_gen.h       # generated logic
  sketch/sketch.yaml      # from template: platform arduino:zephyr only (never list Arduino_RouterBridge)
  python/main.py          # generated
  python/iotc_runtime.py  # byte copy of iotc/runtime/*.py (golden-checked)
  assets/index.html app.js style.css config.json
  assets/libs/            # socket.io.min.js, arduino.js, chart.umd.min.js (from templates)
  .iotc-build             # marker
```
No `python/requirements.txt` (no venv, no network at start). No folder named `data/` in assets.
The writer builds into a temporary directory and replaces the target only if it is empty or
holds a `.iotc-build` marker; otherwise exit 2. The app slug is the source stem lowercased with
other characters mapped to `_` (prefix `app_` if it starts with a digit or is a Windows reserved
name).

### 11.8 LED matrix renderer (`runtime/matrix.py` + generated C++)
A pure function `frame(state) → uint8_t[104]` (row-major, 8 rows × 13 columns, values 0–7),
generated identically in C++ and Python (ASCII-art goldens via `iotc matrix-preview`; the page
mirrors it):
- Columns 0–9: rows 0–3 hold one 2-row bar per sensor (≤ 2 sensors) or one row each (≤ 4); the
  bar length is `⌊code·10/(A+1)⌋`, threshold codes of the rules on that sensor are dim ticks
  (level 2), the bar is level 5, the tip level 7. Row 5: one pixel per rule (≤ 10): 7 firing,
  1 idle, alternating 7/1 per tick while a FOR timer runs. Row 7: one 2-pixel block per actuator
  (≤ 5): 7 ON, 1 OFF.
- Columns 10–12, rows 0–7: status glyph (3×5 font, rows 1–5): `F` while FAILSAFE, `E` while an
  ENSURE is forcing, `!` for 1 s after a refusal, `X` if `Bridge.begin()` failed; otherwise a
  heartbeat pixel at (7, 12) toggling with each fresh heartbeat.
- `draw()` is called only when the frame changed. Overflow → I122, truncated deterministically.

### 11.9 Web dashboard (`webpage.py`)
Generated `index.html`, `app.js`, `style.css`, `config.json` using only the copied local libraries
(no CDN). Panels: sensor sliders (`--input sim`) with unit and code; per-sensor gauge with
threshold ticks; a 60 s Chart.js chart with threshold lines and actuator ON bands; actuator lamps
coloured by the last result; placement table with live row highlight (MCU and Linux colours);
safety panel (✓/⚠, ENSURE, FAILSAFE, link); event log capped at 200 rows, newest first, in
sentences ("fan ON refused by NEVER#1 (heater is ON)"); link banner (UP / FAILSAFE, heartbeat seq
and age, client watchdog: no sample for 1.5 s → "LINUX LINK LOST"); stats (overruns, max pass µs
vs deadline, Bridge round trip median/max from `hb_ack`); matrix mirror; compiler report tab
(`iotc check` output); conformance panel (predicted vs actual, §17); demo controls (pause the
Linux rule engine for 10 s / until resumed).

---

## 12. Hardware

### 12.1 Board-only demo (primary)
Arduino UNO Q · USB-C cable + 5 V/3 A supply · laptop with App Lab, adb · phone hotspot. No
breadboard, no components.

| DSL name | Board element | Notes |
|---|---|---|
| `heater` | `LED3R` (LED3 red) | MCU, active-low |
| `fan` | `LED4B` (LED4 blue) | MCU, active-low |
| `temp`, `humidity` | `A0`, `A1` bound in source; values from web sliders (`--input sim`) | floating pins are never read in sim mode |
| status | 8×13 LED matrix | generated renderer |
| Linux alive | LED1 green | blinked by `main.py` |

### 12.2 Optional kit (`--input pins`, no compiler change)
10 kΩ pots (outer legs to **3V3** and GND, wiper to A0/A1 — never 5 V on A0–A5), LDR + 10 kΩ
divider, push button to 3V3 on a D pin (INPUT_PULLDOWN), active buzzer module on a D pin. Real
loads only through a MOSFET or relay module with a gate/base pull-down.

---

## 13. Testing

### 13.1 Examples
`examples/` holds ≥ 12 valid programs (greenhouse, greenhouse_demo, greenhouse_board,
greenhouse_wokwi, cold_room, door_alarm, light_sensor, fahrenheit, place_windows_mcu,
never_proven, ensure_chain, notify_only), `examples/edge/` (keyword_names, notify_nasty, empty,
sixteen_sensors, thirtytwo_linux_rules, forward_refs) and one file per error code under
`examples/errors/`.

### 13.2 Test layers

| Layer | Test | Pass condition |
|---|---|---|
| Source/lexer | golden token stream per example + every §4.2 edge case | exact match |
| Parser | AST snapshot per example; recovery cases | AST matches; code + `line:col` match |
| Sema / analyses | one file per code | exactly that code at that span (`--json`), golden render |
| ADC lowering | exhaustive over codes 0..A, all ops, random thresholds and ranges | integer test ≡ exact real comparison |
| IR | `Interval`/closure property tests (seeded generator) | algebraic laws hold |
| Windows | property tests vs a brute-force model; C++ window on host | identical values |
| Interpreter | hand traces incl. the demo script | canonical log exact |
| Back ends | generated files per example | byte-equal goldens; `py_compile`; host `g++ -Wall -Werror`; `arduino-cli compile` (when installed) |
| Determinism | builds with `PYTHONHASHSEED=1` and `999` | byte-identical, no `\r` |
| Differential | §13.3 | host C++ log = interpreter log |
| Co-simulation | host unoq sketch + generated `main.py` with stubbed `arduino.*` | log = `iotc run --target unoq` |
| Fuzzer | seeded run; mutants | 0 violations; every mutant caught |
| Board | demo script §17; conformance | passes; N/N |

### 13.3 Differential testing and co-simulation
`tests/host_shim/` implements `Arduino.h` (`millis`, `micros`, `analogRead`, `digitalRead`,
`digitalWrite`, `pinMode`, `noInterrupts`), `Arduino_RouterBridge.h` (`Bridge.notify` appends to
a log; `provide_safe` stores the handler) and `Arduino_LED_Matrix.h` (records frames). The shim
`main()` reads trace rows, delivers heartbeats per §9.10 and calls `loop()` once per row (mode A)
or 10 times per row with interpolated `millis()` (mode B; must equal mode A). WSL
`g++ -std=gnu++17 -O0 -Wall -Werror` is the reference compiler; tests skip if absent. The
co-simulation connects the host unoq sketch to the generated `main.py` (stub `arduino.app_utils`,
stub `WebUI`, injected clock) on the same row clock.

---

## 14. Repository layout, CLI, commands

```
Architecture.md  README.md  pyproject.toml  tasks.py  Makefile  .gitattributes  conftest.py
iotc/  source.py tokens.py lexer.py ast.py parser.py diagnostics.py escape.py cli.py
       sema/      symbols.py units.py pins.py checker.py
       ir.py      lower.py adc.py
       analysis/  intervals.py closure.py placement.py safety.py report.py
       runtime/   window.py engine.py matrix.py        # shared: interpreter + board main.py
       interp.py  fuzz.py
       backend/   profiles.py bridge.py mcu_cpp.py linux_py.py webpage.py app_writer.py deploy.py
                  templates/                           # copied from the board (Phase 0)
examples/  *.iot  edge/*.iot  errors/*.iot
scenarios/ demo_tour.csv  fallback_sweep.csv
hardware/  phase0/probe/  phase0/serial_probe/  phase0/results/
scripts/   deploy.ps1 linux_kill.ps1 linux_pause.ps1 linux_restore.ps1 versions.ps1 preflight.ps1 (+ .sh)
tests/     test_*.py gen.py golden/ traces/ analysis_cases/ host_shim/ fixtures/ EDGE_CASES.md
docs/      phase0_checklist.md demo_card.md report/
wokwi/     diagram.json
```

| Command | Effect |
|---|---|
| `iotc check f.iot [--board unoq] [--json]` | diagnostics, thresholds, placement, safety, resources |
| `iotc build f.iot --board unoq -o build/app [--input sim] [--scenario s.csv] [--demo-controls]` | App folder |
| `iotc build f.iot --board uno --target mcu-only -o out` | single sketch (Wokwi) |
| `iotc run f.iot --trace t.csv [--target …] [--kill-linux-at T]` | interpreter log |
| `iotc fuzz f.iot -n 10000 --seed 42 [--mutant …]` | fuzzer |
| `iotc matrix-preview f.iot --trace t.csv --at T` | ASCII frame |
| `iotc deploy build/app [--via adb\|ssh] [--host h]` | push + start App |
| `py tasks.py test\|golden\|fuzz\|diff\|cosim\|arduino\|demo` | project tasks |

Options: `--sample-ms` (100), `--heartbeat-ms` (500), `--failsafe-timeout-ms` (2000),
`--place-windows linux|mcu` (linux), `--werror`, `--ascii`, `--color`.

---

## 15. Plan and milestones (≈ 12 working days from 17 Nov 2026)

| Day | Build | Board gate |
|---|---|---|
| 1 | This document; repo skeleton, `tasks.py`, goldens infrastructure; frozen `ir.py`; Phase 0 probe App + scripts | — |
| 2 | source, lexer, diagnostics, parser + recovery | **B0**: run the probe, fill §19, copy templates |
| 3 | sema, units, pins, ADC lowering | — |
| 4 | intervals, closures, safety, placement, report | — |
| 5 | runtime (windows, engine, matrix), interpreter | — |
| 6 | MCU back end, host shim, differential test | **B1**: `mcu-only --input scenario` on the board |
| 7 | Linux back end, dashboard, App writer, deploy | **B2**: generated App with sliders |
| 8 | Demo controls, fallback scenario, stats, co-simulation | **B3**: pause + `docker kill` failsafe |
| 9 | Fuzzer, mutants, examples, compile checks | — |
| 10 | Conformance mode, polish (feature freeze) | **B4**: conformance on the board |
| 11 | Demo card, report, slides, backups | rehearsal ×3, video |
| 12 | Buffer | final rehearsal; freeze versions |

### 15.1 Phase 0 checklist (see `docs/phase0_checklist.md`)
1. Deploy `hardware/phase0/probe` (App Lab import or `scripts/deploy.ps1`); collect versions
   (`scripts/versions.ps1`).
2. Observe LED3/LED4 colour cycle (active-low) and the matrix test pattern.
3. Read the probe report: pass interval, `micros()` resolution, matrix `draw()` cost, floating
   ADC noise, Bridge round trip (median/max over 200), handler preemption counts, type matrix,
   16-code `sample`, 2 KB notify burst stall, `Serial` cost, LED1 access.
4. Run `scripts/linux_pause.ps1` / `linux_restore.ps1` and `linux_kill.ps1` / `linux_restore.ps1`;
   record whether the MCU kept running (LED cycle continues), whether heartbeats resume without an
   MCU reset, and whether the container restarts by itself.
5. Deploy `hardware/phase0/serial_probe` (no `Bridge.begin`): does `Serial.println` block or
   crash? (LED blink rate tells.)
6. Copy the `blink-with-ui` template from the board into `iotc/backend/templates/`.
7. Record all results in §19; freeze App Lab and board image versions until the demo.

---

## 16. Watch list

**Hardware / platform**
- App Lab Stop and `arduino-app-cli app stop/start/restart` reset the MCU (ADR-25). Starting an
  App in the `failed` state also stops it first (reset). Restore Linux with `docker start`.
- `Bridge.begin()` blocks on the router with no timeout. On cold boot the loader holds the sketch
  until Linux and the router are up (20–30 s boot animation): power the board early.
- If the `arduino-router` service restarts, the MCU's handler registration is lost and it stays in
  FAILSAFE until reset. Never demonstrate it; documented limitation.
- Sketch `Bridge.call` has no timeout: never generate it. `Serial` on UNO Q makes blocking calls
  (ADR-22).
- Python Bridge handlers run on their own thread: queue and handle in `loop()`; never
  `Bridge.call` from a handler.
- Bridge names are matched at runtime only: hence `bridge.py` as the single source.
- `pinMode(OUTPUT)` starts the pin LOW: active-low LEDs flash for microseconds at boot.
- Never put 5 V on A0–A5.
- App Lab can be slow: keep adb/ssh + `arduino-app-cli` as the fallback deployment path.
- Network: a phone hotspot (WPA2, no captive portal); write down the board IP for when `.local`
  fails; USB + adb works without any network.
- Do not update App Lab, the board image or libraries after Phase 0.

**Compiler correctness**
- Boundary semantics are where bugs hide: integer code tests + exhaustive tests (ADR-17).
- F → C conversion: convert thresholds exactly; `95 F` against a C sensor is exactly 35.
- Interpreter, C++ and Python drifting apart: §9 is normative; shared runtime module; differential
  test; co-simulation.
- Golden churn hiding real changes: every golden update needs a sentence in the commit.

**Scope**
- Don't add `OR`, `NOT`, PWM or new sensor types. Each breaks the box model or the monitor.
- Fuzzer shrinking and differential testing are worth more than another language feature.

---

## 17. Demo script (board-only, ≈ 10 minutes, compiler first, board last)

Program `examples/greenhouse_board.iot`:
```
SENSOR   temp     AT A0 ANALOG UNIT C   RANGE -40..125;
SENSOR   humidity AT A1 ANALOG UNIT PCT RANGE 0..100;
ACTUATOR fan      AT LED4B;
ACTUATOR heater   AT LED3R;
RULE cool   REALTIME 20ms: WHEN temp > 35 THEN fan ON;      // code ≥ 466
RULE uncool REALTIME 20ms: WHEN temp < 30 THEN fan OFF;     // code ≤ 433
RULE warm:   WHEN avg(temp, 10s) < 18 THEN heater ON;       // Linux
RULE unwarm: WHEN avg(temp, 10s) > 22 THEN heater OFF;      // Linux
RULE alert:  WHEN temp > 45 FOR 5s THEN NOTIFY "Overheat";  // Linux
RULE dry:    WHEN humidity < 20 FOR 3s THEN NOTIFY "Air too dry";
SAFETY NEVER heater ON AND fan ON;
SAFETY ENSURE fan ON WHEN temp > 50;                        // closure: heater OFF (W107)
FAILSAFE heater OFF;
```

1. `iotc check examples/greenhouse_board.iot --board unoq`: code thresholds, hysteresis gap
   (I105: 5 C, 32 codes), placement table, safety report (⚠ NEVER with witness, ENSURE closure),
   resources.
2. Error programs: S001 typo with suggestion, S021 pin clash, S010 unit mismatch, E103 conflict
   with overlap, P201 REALTIME + avg, E116 contradictory ENSURE.
3. `iotc fuzz -n 10000 --seed 42` → 0 violations; `--mutant disable-monitor` → violation and a
   shrunk trace.
4. App already running (default App, started before the talk): open
   `http://<board>.local:7000` on the projector.
5. **Hysteresis:** temp slider 40 → fan (LED4 blue) ON; 32 → stays ON; 28 → OFF. The matrix bar
   sits between the two threshold ticks.
6. **Linux rule:** slider 10 °C, wait ≈ 10 s → "warm (Linux) → heater ON" (LED3 red); the
   placement row lights in the Linux colour; LED1 blinks with heartbeats.
7. **Failsafe (pause):** click "Pause Linux rule engine". Within 2 s: link DOWN, "heater OFF
   (FAILSAFE)", matrix `F`, LED1 stops. Slider 40 → fan still ON (MCU rule). Resume → link UP;
   slider back to 10 → warm again.
8. **NEVER refusal:** heater ON; slider 40 → "fan ON refused by NEVER#1 (heater is ON)", matrix
   `!`. ≈ 10 s later avg > 22: unwarm turns the heater OFF and the fan is applied.
9. **ENSURE:** slider 55 → fan forced ON, heater forced OFF, matrix `E`; after 5 s the
   "Overheat" toast (NOTIFY on Linux).
10. **Real kill:** `scripts\linux_kill.ps1` (`docker kill`). The page dies. On the board: LED1
    stops, matrix `F`; after 3 s the fallback scenario sweeps the temp bar and the fan LED follows
    it with hysteresis — the placement analysis visible in hardware. Restore with
    `scripts\linux_restore.ps1` (`docker start`).
11. **Conformance:** the `--input scenario` build: "predicted by the compiler N/N = board".

Backups in order: live App → `mcu-only --input scenario` on the MCU alone → Wokwi
`greenhouse_wokwi.iot` → recorded video.

---

## 18. Viva preparation (likely questions)

- *Why is this a compiler and not a template?* Analyses over the program (intervals, closures,
  placement, safety proofs) decide what code is generated and where; invalid programs are
  rejected; thresholds are compiled to exact integer tests.
- *Why AND-only?* Every condition is a box, so emptiness, overlap and gap are exact interval
  operations. OR is two rules.
- *Why integer ADC codes?* The board only ever sees 1024 values. Compiling `temp > 35` to
  `code ≥ 466` makes the MCU, Linux and the interpreter agree exactly; floats would disagree at the
  boundary (e.g. `temp >= 35.000001` is true in float32 and false exactly).
- *Why was the v2 NEVER step unsound?* Pairs (a ON, b ON) and (b OFF, c ON): a single pass
  reverting b and c recreates a violation of the first pair; another order gives a different
  result. The fixpoint reverts all changed sides of all violated pairs per round: terminating,
  order-independent, and the invariant follows from the closures.
- *Why does avg run on Linux if the MCU has enough RAM?* A placement policy: latency-tolerant work
  leaves the processor that owns the pins and deadlines. `--place-windows=mcu` changes it.
- *Why is P201 an error?* The Bridge has no bounded latency (measured median/max in §19).
- *Can Linux turn the heater on while the fan is on?* No. Linux only sets rule flags; the MCU
  owns actions and the monitor.
- *What if Linux is alive but stops receiving samples?* The heartbeat acknowledges the last
  sample; a stale acknowledgement takes the link DOWN (ADR-21).
- *Is the windowed analysis sound?* Over-approximating: may warn spuriously (W103), never misses
  a real conflict among boxes.
- *How do you know the generated code matches the interpreter?* Differential testing on the host,
  co-simulation of both generated programs, and conformance on the board.
- *Why doesn't the MCU start before Linux?* The UNO Q loader holds the sketch until Linux boots;
  initial safe states are written first in `setup()`; pins are high-Z (off) before.

---

## 19. Decision log and Phase 0 results

Research (17 Nov 2026) from Arduino sources: arduino-libraries/Arduino_RouterBridge,
Arduino_RPClite, arduino/ArduinoCore-zephyr, arduino/arduino-app-cli, arduino/app-bricks-py,
arduino/app-bricks-examples, docs.arduino.cc. "Board" = result on our board (Phase 0).

| Item | Research result | Board | Action |
|---|---|---|---|
| FQBN | `arduino:zephyr:unoq` (+ `:wait_linux_boot=app` in App Lab) | | §11.1 |
| Logging object | `Serial` is the RouterBridge Monitor (core ≥ 0.55); writes make blocking RPC calls | | ADR-22 |
| `pinMode` on analog pins | old cores broke `analogRead`; current core reapplies pinctrl | | never emit it |
| ADC | 14-bit hardware, default read resolution 10 → 0..1023, 3.3 V | | `A = 1023` |
| LED names/polarity | `LED3_R/G/B`, `LED4_R/G/B`, active-low | | profile |
| LED matrix API | `Arduino_LED_Matrix`: `begin`, `setGrayscaleBits(3)`, `draw(uint8_t[104])` | | §11.8 |
| `Bridge.begin()` | blocks on the router (no timeout), returns bool | | ADR-21, §16 |
| `provide_safe` context | may run on the bridge thread (RPClite ≥ 0.3.0) and preempt `loop()` | | ADR-23 |
| Message limits | Linux→MCU ≤ 256 B; MCU→Linux limited by 115200-baud UART + 1 KB TX buffer | | §11.5 |
| Pass floor | ≈ 1 ms (`__loopHook` sleeps 1 ms) | | `MIN_DEADLINE_MS` |
| `micros()` resolution | ≈ 31 µs (LPTIM 32.768 kHz) | | §9.7 |
| Bridge round trip | community: 12–50 ms | | P201 text |
| App Lab Stop | resets the MCU (Wait-for-App: sketch halted) | | ADR-25 |
| Python crash | container exits, MCU keeps running, no auto-restart | | §11.6 |
| Python on board | 3.13 in Docker (`python-apps-base`), no venv without requirements.txt | | §11.6 |
| WebUI | port 7000; `send_message(type, dict, room=None)`; `on_message(type, cb(sid, data))`; `on_connect`; `expose_api` | | §11.6 |
| Template | `blink-with-ui`; `sketch.yaml` platform only; one-emoji icon; no `ports:` | | §11.7 |
| Apps location | `/home/arduino/ArduinoApps/<name>`, App id `user:<name>` | | deploy |
| Router restart | MCU loses handler registration until reset | | §16 |
| Versions (CLI, core, RouterBridge, RPClite, router, bricks, Python) | — | | freeze |
| Jitter `J`, matrix `draw()` cost, TX burst stall, `Serial` without Bridge, LED1 access, container restart policy, deploy time | — | | profile constants |

Open questions: none blocking. Revisit ADR-6 (incumbent wins) if the audience finds "cooling
refused while heating" surprising; the alternative is "REALTIME rules win".

---

## 20. Reviewer's notes (opinion, not normative)

- Placement analysis only means something on a two-processor target; the UNO Q is what makes the
  middle end novel. Build the demo around the failsafe moment (§17 steps 7 and 10).
- The v3 changes come from an adversarial review: integer codes, tick grid, fixpoint monitor,
  closures and the acknowledged heartbeat each close a concrete hole with a counterexample.
- Keep the language small. The marks are in the analyses, the error messages and the evidence
  (fuzzer, differential test, co-simulation, conformance, measured Bridge latency).
- Write the report's semantics chapter directly from §9; it is already the formal model.
