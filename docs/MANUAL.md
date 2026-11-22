# Quick manual: run iotc and test the UNO Q

About 45 minutes in total. Part A needs only the laptop. Part B needs the board.

## What you need

- Your laptop (Windows) with this repo.
- Arduino UNO Q.
- A USB-C cable that carries data, not a charge-only cable.
- A 5 V / 3 A USB-C power supply. This is optional: use it if the board reboots on laptop USB power.

There is no breadboard and nothing to wire. Everything uses the board's onboard LEDs and LED matrix.

Run every command below in **PowerShell, in the repo folder**:
```powershell
cd C:\Users\akd24\Documents\Mini-Project
```

---

## Part A — Laptop only (5 min)

```powershell
py -3.13 tasks.py test
```
Expect `670 passed` (or more).

```powershell
py -3.13 -m iotc check examples\greenhouse_board.iot
```
This prints the compiler's analysis of the demo program: code thresholds, placement, safety and resources.

```powershell
py -3.13 -m iotc run examples\greenhouse_board.iot --trace tests\traces\demo_script.csv --kill-linux-at 15000 --revive-linux-at 20000
```
This simulates the whole demo, including the Linux crash. Look for `heater,OFF,FAILSAFE` at 16700.

```powershell
py -3.13 -m iotc matrix-preview examples\greenhouse_board.iot --trace tests\traces\demo_script.csv --kill-linux-at 15000 --at 16700
```
This prints what the LED matrix will show at 16.7 s; the `F` on the right means FAILSAFE.

**If any command fails,** copy its output and send it to me.

---

## Part B — Board probe (30–40 min)

This runs a measurement App, not the demo itself. The generated demo App comes later, on day 7.

### B1. Setup (once)

1. Install adb, then **open a new PowerShell window**:
   ```powershell
   winget install Google.PlatformTools
   ```
2. Plug the UNO Q into the laptop by USB-C. Wait about 60 s while the matrix plays its boot animation.
3. Check that adb sees the board:
   ```powershell
   adb devices
   ```
   You should see one line ending in `device`.
4. If PowerShell blocks the scripts, run this once per window:
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   ```

App Lab can stay open, but **don't press Stop in App Lab**: it resets the board's microcontroller.

### B2. Versions and templates (3 min)

```powershell
scripts\versions.ps1
scripts\pull_templates.ps1
```

### B3. Main probe (5 min)

```powershell
scripts\deploy.ps1 hardware\phase0\probe
```
The first start compiles on the board and takes 1–3 minutes. Note the **"app start … after N s"** time it prints. Logs then stream in the terminal; press Ctrl+C to stop following them (the App keeps running).

**Watch the board and note:**
- [ ] **LED cycle:** LED3 should go red, green, blue, then LED4 red, green, blue, then both off, repeating every 0.6 s per step. Write *inverted* if it's the opposite: mostly lit, with one colour dark.
- [ ] **Matrix:** a dot moving across the top and a brightness gradient on the bottom row.
- [ ] **Bridge failure:** if both red LEDs stay on and nothing moves, `Bridge.begin()` failed. Write that down.
- [ ] **Web page:** run `scripts\open_page.ps1`. It tunnels the board's port 7000 over the USB cable (no Wi-Fi needed) and opens `http://localhost:7000`. Does the page with a JSON report open?

Wait about 40 s, then copy the report to the laptop:
```powershell
scripts\probe_pull.ps1
```

### B4. Linux pause / kill / restore (5 min)

Keep the probe running and watch the LEDs throughout.
```powershell
scripts\linux_pause.ps1
scripts\linux_restore.ps1
scripts\linux_kill.ps1
adb shell arduino-app-cli app list
scripts\linux_restore.ps1
scripts\probe_pull.ps1
```
1. **After `linux_pause`:** do the LEDs keep cycling? (yes/no)
2. **After the first `linux_restore`:** does the web page come back? Roughly how many seconds did it take?
3. **After `linux_kill`:** do the LEDs keep cycling? (yes/no)
4. **About 30 s after the kill, before the second restore:** did Python restart by itself? Check the `app list` output.
5. **Before `probe_pull`:** wait about 20 s after the second `linux_restore`, so the new report has fresh data.

### B5. Serial probe (3 min)

```powershell
scripts\deploy.ps1 hardware\phase0\serial_probe -NoFollow
```
Watch the LEDs:
- [ ] LED4 **blue** lights for 2 s. That means the sketch started.
- [ ] Then LED3 **green**: does it blink steadily twice per second, blink slowly, or stay dark?

### B6. Cold boot (2 min)

Unplug the board, plug it back in and time it:
- [ ] Seconds until the matrix boot animation ends.
- [ ] Does the serial probe start again on its own?

---

## Send me

1. These two files: `hardware\phase0\results\versions.txt` and `hardware\phase0\results\probe_report.json`.
2. Your answers to the checkboxes in B3–B6.
3. Any red error text from the scripts or from App Lab.

You can just say "results are in the folder", and I'll read the files myself.

## If something goes wrong

| Problem | Fix |
|---|---|
| `adb devices` shows nothing | Try another cable (it may be charge-only), wait for the boot to finish, then run `adb kill-server` and `adb devices` again. |
| `adb` is not recognised | Open a **new** PowerShell window after installing it. |
| "scripts cannot be loaded" | Run `Set-ExecutionPolicy -Scope Process Bypass`. |
| Deploy says another app is running | Stop it from the App Lab app list, or run `adb shell arduino-app-cli app stop user:<name>`. |
| The web page doesn't open | Run `scripts\open_page.ps1` (USB tunnel, `http://localhost:7000`). Addresses like `172.17.x.x` from `hostname -I` are internal to the board and can't be reached from the laptop. |
| The board keeps rebooting | Use the 5 V / 3 A USB-C supply. |

---

## Part C — B1: the compiler's own sketch on the board (10 min)

This is the first generated program to run on the board: everything runs on the microcontroller,
driven by a built-in 50 s scenario. No Linux rules, no web page.

```powershell
py -3.13 -m iotc build examples\greenhouse_board.iot --target mcu-only --input scenario --scenario scenarios\demo_tour.csv -o build\b1_mcu_only
scripts\deploy.ps1 build\b1_mcu_only -NoFollow
```

The first start compiles on the board (1–3 min). Then watch one loop (it repeats every 50 s). The
compiler predicts exactly this (`iotc run`, times from the start of each loop):

| Time | LEDs | LED matrix |
|---|---|---|
| 0–3 s | both off | top bar in the middle, ticks visible |
| 3 s | **LED4 blue ON** (fan) | bar jumps right |
| 9 s | LED4 off (hysteresis: it stayed on at 32 C, goes off at 28 C) | |
| ~17.6 s | **LED3 red ON** (heater, average below 18 C) | bar far left |
| 26 s | no change | **`!` on the right** for 1 s (fan refused: heater is ON) |
| 30 s | **LED4 blue ON, LED3 red OFF** together | |
| 34–42 s | blue stays ON | **`E` on the right** (ENSURE forcing) |
| 42 s | LED4 off | |
| 50 s | loop restarts | |

**Send me:** does it match? Note anything different (times are approximate ±1 s), and whether
the sketch compiled. If App Lab shows a compile error, copy the error text.

---

## Part D — B2: the full UNO Q App with the web dashboard (15 min)

Rules run on both processors: `cool`/`uncool` on the microcontroller, `warm`/`unwarm`/`alert`/`dry`
on Linux. Sensor values come from sliders on the page.

```powershell
py -3.13 -m iotc build examples\greenhouse_board.iot --input sim --demo-controls --scenario scenarios\fallback_sweep.csv -o build\b2_board
scripts\deploy.ps1 build\b2_board -NoFollow
scripts\open_page.ps1
```

`open_page.ps1` tunnels the page over USB and opens `http://localhost:7000` (no Wi-Fi needed).
Until the page connects, the board plays a slow 25–40 C sweep by itself; the fan LED (LED4 blue)
follows it.

**Try these, in order (Architecture §17 steps 5–9):**

1. **Hysteresis:** temp slider to 40 → fan (LED4 blue) ON; back to 32 → stays ON; 28 → OFF.
2. **Linux rule:** temp slider to 10, wait about 10 s → heater (LED3 red) ON. The log says
   `heater ON · rule warm (Linux)`; the `warm` row in the placement table lights up.
3. **Failsafe (soft):** press **Pause Linux rule engine (10 s)**. Within about 2 s: the pill turns red
   (FAILSAFE), the heater goes OFF, the matrix shows `F`. Move the temp slider to 40: the fan still
   turns ON (its rule runs on the microcontroller). After 10 s the link comes back UP.
4. **NEVER refusal:** slider to 10 again, wait for the heater, then slider to 40 → the log shows
   `fan ON refused by NEVER#1 (heater is ON)` and the matrix flashes `!`. About 10 s later the
   heater turns OFF by itself and the fan comes ON.
5. **ENSURE:** slider to 55 → fan forced ON, heater forced OFF, matrix `E`; after 5 s a toast says
   "Overheat".
6. **Real Linux kill (optional):** `scripts\linux_kill.ps1` → the page freezes, the banner says the
   link is lost; on the board the matrix shows `F`, and after 3 s the board plays its own sweep and
   the fan keeps following it. Restore with `scripts\linux_restore.ps1`, then run
   `scripts\open_page.ps1` again.

**Send me:** what worked, what looked wrong, and a screenshot of the page if you can. To stop:
`adb shell arduino-app-cli app stop user:iotc_b2_board`.

---

## Part E — B3: failsafe on the real board (20 min)

This rebuilds the B2 App with the day-8 changes. It then tests the three ways the Linux side can
die (page pause, `docker pause`, `docker kill`) and measures how fast the MCU fails safe. The
compiler predicts a link DOWN **1.5–2.1 s** after the last heartbeat: the 2000 ms timeout, minus up
to 500 ms since the last beat, plus up to one 100 ms tick.

```powershell
py -3.13 -m iotc build examples\greenhouse_board.iot --input sim --demo-controls --scenario scenarios\fallback_sweep.csv -o build\b2_board
scripts\deploy.ps1 build\b2_board -NoFollow
scripts\open_page.ps1
```

**Before each test, get the heater ON:** move the temp slider to about 10 C and wait about 10 s until
LED3 is red and the log shows `heater ON · rule warm (Linux)`. Without this the failsafe has
nothing to switch off. This was why B2 showed only `link DOWN/UP`.

**E1. Page pause (3 times).** Press **Pause Linux rule engine (10 s)**. In the event log, note the
board times of these three lines:
`Linux rule engine paused…` → `Linux link DOWN` → `heater OFF · FAILSAFE`.
While it is paused, move temp to 40: the fan (LED4 blue) must still come ON. After 10 s the log
shows `Linux link UP`. Move temp back to 10 to get the heater ON before the next try.

**E2. `docker pause` (frozen Linux).** With the heater ON, note the **board time** in the page
header, then run:
```powershell
scripts\linux_pause.ps1
```
The page freezes and the red "LINK LOST" banner appears after 1.5 s. On the board, within about 2 s,
LED3 goes off and the matrix shows `F`. About 3 s later the matrix bar starts sweeping (the board's
own fallback scenario), and LED4 follows it. Wait 20 s, then run:
```powershell
scripts\linux_restore.ps1
```
The page continues by itself, or press F5. Check the header: the **board time must have kept
counting** (for example 812 s, not 5 s). A small number would mean the MCU was reset, which must not
happen.

**E3. `docker kill` (crashed Linux).** Same as E2, but use `scripts\linux_kill.ps1` and then
`scripts\linux_restore.ps1`. After the restore, run `scripts\open_page.ps1` again: the Python program
restarts from scratch, so the old log is gone. Again check that the board time kept counting and
that the log starts with `Linux link UP`.

For E2/E3, film the board with your phone, with the PowerShell window in view. The script prints the
exact time of the pause or kill, and the video shows when LED3 goes off.

**Send me:**
- The three E1 rows (paused / DOWN / FAILSAFE board times).
- For E2/E3: kill-to-LED-off seconds (from the video), whether the sweep started, and the board
  time before and after.
- Anything unexpected, such as the page not coming back or the App showing "failed" in App Lab.

To stop: `adb shell arduino-app-cli app stop user:iotc_b2_board`.

---

## Part F — Laptop backup: the same App with no board

If the board fails during the demo, this runs the **same generated App** on the laptop. It runs
the same main.py, the same dashboard and the same MCU logic (the Python model that the C++ is tested
against). The page shows a **VIRTUAL BOARD** badge, so it can't be mistaken for the real board.

```powershell
py -3.13 tasks.py virtual
```
(This is short for `py -3.13 -m iotc dashboard examples\greenhouse_board.iot --scenario scenarios\fallback_sweep.csv`.)
A browser tab opens at `http://127.0.0.1:8000`. The sliders, pause button, log, chart, matrix mirror
and toasts all work as on the board. To run the 50 s tour unattended, with no slider input:
```powershell
py -3.13 -m iotc dashboard examples\greenhouse_board.iot --scenario scenarios\demo_tour.csv --autoplay
```
Stop with Ctrl+C. Add `--host 0.0.0.0` to open it from a phone on the same Wi-Fi.

---

## Part G — B4: conformance mode on the board (10 min)

The compiler predicts the board's exact event log for the 50 s tour: every LED change and every
NOTIFY, with the tick it happens on. The board plays the tour, and the page checks each line
against the prediction. The same check passes in the tests against the compiled C++ and against
the generated Linux program.

First stop the B2 App (only one App runs at a time), then build and deploy:
```powershell
adb shell arduino-app-cli app stop user:iotc_b2_board
py -3.13 -m iotc build examples\greenhouse_board.iot --input scenario --scenario scenarios\demo_tour.csv --demo-controls -o build\b4_conformance
scripts\deploy.ps1 build\b4_conformance -NoFollow
scripts\open_page.ps1
```
The build prints `conformance mode: the compiler predicts 9/9 event(s) per scenario iteration`.

On the page, the **Conformance** card first says "Waiting for the scenario to start". Within about 3 s
of `Linux link UP`, the tour starts. The LEDs and matrix follow the same timings as B1 (Part C). The
table fills in as the tour plays: each predicted line gets **✓ exact** when the board reports
it on the same tick.

1. Let **two full iterations** run (about 1 min 45 s). Expect `✓ Iteration 1: 9/9 predicted events
   matched exactly`, and the same for iteration 2. A line marked **≈ +100 ms** means a Bridge round trip
   took longer than one 100 ms tick that time. That is not a logic error, but tell me how many.
2. **Control test (optional, iteration 3):** press **Pause Linux rule engine** at about 15 s into an
   iteration. The `warm` heater line and later lines must now show ✗ missing or unexpected. This proves
   the check is not always green. The next iteration should match fully again.

**Send me:** the summary lines for iterations 1–3 from the bottom of the card, and a screenshot of the card.
To stop: `adb shell arduino-app-cli app stop user:iotc_b4_conformance`.

**Before any board session**, `py -3.13 -m iotc doctor` checks Python, WSL g++, adb, the board, the
App Lab version (against Phase 0), the running App, the Bridge router and the page tunnel.
