# Slide content: iotc, an IoT Sensor DSL Compiler for the Arduino UNO Q

Twelve slides for the final presentation, written to drop into `IoT Sensor DSL Compiler.pptx`.
Each slide gives the on-slide text, then speaker notes. The numbers come from
`docs/evidence/` and `docs/REPORT.md`.

---

## 1. Title

**iotc: compiling sensor rules into a safe two-processor application**
Compiler Design mini project · B.Tech Year 3 · *[name, roll no., guide]*

*Notes:* One sentence: we built a compiler from a small rule language to a complete Arduino UNO Q
application, with analyses that catch bad rules and a failsafe that survives a Linux crash.

---

## 2. The problem

- Hand-written sensor code: conflicting rules, no hysteresis, float boundary bugs
- Safety lives in comments ("never heat and cool together")
- The UNO Q has **two processors**: which rule runs where? What if Linux dies?

*Notes:* Give the fan example: ON above 35, OFF below 40, so between 35 and 40 it chatters.

---

## 3. The language

```
SENSOR temp AT A0 ANALOG UNIT C RANGE -40..125;
ACTUATOR fan AT LED4B;
RULE cool REALTIME 20ms: WHEN temp > 35 THEN fan ON;
RULE warm: WHEN avg(temp, 10s) < 18 THEN heater ON;
SAFETY NEVER heater ON AND fan ON;
SAFETY ENSURE fan ON WHEN temp > 50;
FAILSAFE heater OFF;
```

- Conditions are AND-only, so **every condition is a box of intervals**

*Notes:* Small on purpose. Boxes make every analysis exact. OR is written as two rules.

---

## 4. Implementation design: five stages

| Stage | Output | Command |
|---|---|---|
| 1 Grammar | tokens, AST | `iotc dump --stage tokens / ast` |
| 2 Threshold/type checks | diagnostics, code thresholds | `iotc check` |
| 3 Event IR | boxes + integer tests, placement, safety | `iotc dump --stage ir` |
| 4 Target code | C++ sketch + Linux `main.py` + dashboard | `iotc build` |
| 5 Simulation | interpreter log, LED matrix, predicted board log | `iotc run`, `iotc dashboard` |

*Notes:* Every stage has a checked-in artifact in `docs/evidence/`.

---

## 5. Stage 1: lexer and LL(1) parser

- Hand-written lexer: maximal munch, did-you-mean (`WHN` → `WHEN`), exact durations
- LL(1) grammar: one token decides every choice; recursive descent
- Panic-mode recovery on `;` and declaration keywords; no error cascades

*Notes:* Show the FIRST-set table from the report if asked. The lexer never stops: errors become
tokens.

---

## 6. Stage 2: checks and ADC-code lowering

- One namespace, kinds, units with exact conversion (95 F = 35 C), pins per board
- **`temp > 35` → `code ≥ 466`** (exact rationals: `r = 6.2·(T + 40)`)
- Averages without division: `avg < 18` → `5·sum < 1798·count`

*Notes:* The key idea. The board only sees 1024 values. Comparing integer codes means the C++,
the Python and the simulator can never disagree at a threshold.

---

## 7. Stage 3: event IR and analyses

- Conflicts (E103) with the exact overlap; hysteresis gap (I105: 5 C = 32 codes)
- ENSURE/FAILSAFE **closures** through NEVER pairs; contradictions are errors
- NEVER **proven** statically, or enforced by the MCU monitor
- **Placement:** REALTIME → MCU, NOTIFY/averages → Linux; P201 if a deadline would cross the Bridge

*Notes:* P201 comes from a measurement: the Bridge took 7.4 ms median but 101.6 ms worst case.

---

## 8. Stage 4: generated code

- MCU: table-driven C++ `iotc_gen.h` (640 lines), integer predicates, no blocking calls
- Linux: `main.py` evaluates Linux rules, sends a heartbeat `(seq, mask, ack)`
- One Bridge contract table; DSL names never become identifiers; live web dashboard

*Notes:* Linux never drives a pin. It sends a bit mask; the MCU owns the pins and the safety
monitor.

---

## 9. Runtime: safety and failsafe

- Each pass: requests → OFF wins → FAILSAFE (if the link is down) → ENSURE → NEVER fixpoint →
  commit
- Heartbeat timeout 2 s; acknowledged samples catch a "deaf" Linux
- **Measured on the board: failsafe in 2.0 s** (predicted 1.5–2.1 s)

*Notes:* The NEVER check is a simultaneous fixpoint; a single-pass version had a counterexample.

---

## 10. Stage 5: simulation and verification

- Reference interpreter = the semantics; LED-matrix preview
- **Differential testing:** the generated C++ compiled with g++, compared with the interpreter on
  random traces
- **Conformance:** the compiler predicts the board's log; the page checks it (9/9)
- **685 tests**, golden files for every stage

*Notes:* The verification found real bugs: a scenario-mode deadlock, and a log that depended on
boot time (fixed by aligned starts).

---

## 11. Live demo

Hysteresis → Linux rule → pause Linux (failsafe) → NEVER refusal → ENSURE → real kill →
conformance

*Notes:* Follow `docs/DEMO_CARD.md`. The fallbacks are ready: the virtual board, the MCU-only
build, Wokwi, the video.

---

## 12. Conclusion and future work

- A small language plus exact analyses gives a compiler that explains its decisions
- Safety lives on the processor that owns the pins
- Next: OR as unions of boxes, PWM outputs, a property-based fuzzer, real sensors (`--input pins`)

*Notes:* Thank the audience; take questions (likely questions are at the end of the demo card).
