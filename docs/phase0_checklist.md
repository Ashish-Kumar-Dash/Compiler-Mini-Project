# Phase 0 board checklist (gate B0)

Goal: answer every remaining board question in one session (about 30–40 minutes), so the generated
code is built on measured facts. Results go into `Architecture.md` §19 and the `unoq` board
profile. You need: the UNO Q, its USB-C cable and power supply, and this laptop.

## 0. One-time setup (5 min)

1. Install adb: `winget install Google.PlatformTools`, then open a **new** terminal.
2. Connect the board by USB-C, wait about 1 minute for it to boot, then run `adb devices`. You
   should see one line ending in `device`.
3. Note the board name you chose in App Lab (it is also `<name>.local` on the network).

All commands below are run from the repo folder in PowerShell. If scripts are blocked, prefix
them with `powershell -ExecutionPolicy Bypass -File`.

## 1. Versions and templates (3 min)

```powershell
scripts\versions.ps1          # writes hardware\phase0\results\versions.txt
scripts\pull_templates.ps1    # copies blink-with-ui + home-climate examples into iotc\backend\templates\
```

## 2. Main probe (about 2 min to start, then watch for 1 min)

```powershell
scripts\deploy.ps1 hardware\phase0\probe
```

The first start compiles the sketch on the board, which takes 1–3 minutes. The script prints the
start time; please note it. Logs then stream in the terminal (Ctrl+C stops following; the App
keeps running).

**Watch the board and write down:**

- [ ] LED3 cycles **red → green → blue**, then LED4 cycles **red → green → blue**, then both go off,
      and the cycle repeats (one step every 0.6 s). If instead almost everything is lit and one
      colour goes dark each step, write "inverted".
- [ ] The LED matrix shows a dot moving across the top rows and a brightness gradient on the
      bottom row.
- [ ] If both red LEDs stay on and nothing else happens: `Bridge.begin()` failed. Write that down
      and skip to step 6.
- [ ] Open `http://<boardname>.local:7000` in a browser (or `http://<board-IP>:7000`). Does the
      probe page with the JSON report appear?

**After about 40 s** (the burst test runs at 20 s and the Serial test at 25 s), copy the report:

```powershell
scripts\probe_pull.ps1        # -> hardware\phase0\results\probe_report.json
```

The same report is printed every 10 s in the log stream, between `===== IOTC PROBE REPORT =====`
lines.

## 3. Linux pause / kill / restore (5 min) — the failsafe mechanism

With the probe still running (the LEDs keep cycling while you do this):

```powershell
scripts\linux_pause.ps1       # freeze the Python side
```
- [ ] Do the LEDs keep cycling? (They should: the MCU does not depend on Python.)

```powershell
scripts\linux_restore.ps1     # unpause
```
- [ ] Does the web page come back? How many seconds did it take?

```powershell
scripts\linux_kill.ps1        # hard-kill the Python container
```
- [ ] Do the LEDs keep cycling?
- [ ] Wait 30 s: did the Python side restart **by itself**? Check with
      `adb shell arduino-app-cli app list`.

```powershell
scripts\linux_restore.ps1     # docker start
scripts\probe_pull.ps1        # pull the report again ~20 s later
```
- [ ] In the new report: is `mcu_uptime_at_first_contact_ms` large (the MCU was **not** reset) or
      small (the MCU **was** reset)? Is `heartbeat_context.hb_total` still increasing?

## 4. Serial probe (3 min)

```powershell
scripts\deploy.ps1 hardware\phase0\serial_probe -NoFollow
```
- [ ] LED4 **blue** lights for 2 s after the sketch starts (the sketch is alive).
- [ ] Then LED3 **green**: blinks steadily twice per second / blinks slowly / stays dark?

## 5. Cold boot (3 min)

Unplug the board, plug it back in, and start a stopwatch.
- [ ] Seconds until the matrix boot animation ends.
- [ ] Is the serial probe running again on its own (did App Lab keep it as the default App)?

## 6. Send back

Paste into the chat:
1. `hardware\phase0\results\versions.txt` (or just say it is there; I can read it).
2. `hardware\phase0\results\probe_report.json` (both pulls).
3. Your checkbox answers from steps 2–5.
4. Any error text from the deploy script or App Lab.

I then fill in Architecture §19, set the `unoq` profile constants (jitter, REALTIME floor, matrix
cost, Bridge round trip) and adjust the generated code if anything differs from the research.
