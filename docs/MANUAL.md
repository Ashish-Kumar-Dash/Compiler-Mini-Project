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
Expect `618 passed` (or more).

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
