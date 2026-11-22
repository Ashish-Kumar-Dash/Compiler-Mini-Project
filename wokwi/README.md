# Wokwi backup (classic Arduino Uno, browser simulation)

This is the third rung of the fallback ladder: the same rules compiled for a classic Uno
(`--board uno --target mcu-only`) and run in the Wokwi browser simulator. Two potentiometers stand
in for the sensors, and two LEDs for the actuators. Everything runs on the one microcontroller.
There is no Linux side, no placement split and no failsafe link.

1. Build the sketch:
   ```powershell
   py -3.13 -m iotc build examples\greenhouse_wokwi.iot --board uno --target mcu-only -o build\wokwi_sketch
   ```
   (`py -3.13 tasks.py backups` builds this along with the other backups.)
2. On wokwi.com, create a new **Arduino Uno** project.
3. Replace `sketch.ino` with `build\wokwi_sketch\wokwi_sketch.ino`.
4. Add a file named `iotc_gen.h` with the contents of `build\wokwi_sketch\iotc_gen.h`.
5. Replace `diagram.json` with the one in this folder.
6. Start the simulation. Turning the left potentiometer (temp, A0) past about 46 % turns the blue LED
   (fan) on (code 466 of 1023, i.e. 35 C on the −40..125 C scale). The Serial monitor prints the canonical event log
   (`EV,…`, `NT,…`).

This is a manual backup. The generated sketch is checked by the C++ differential tests (`uno`
target); the Wokwi wiring in `diagram.json` has not been checked in Wokwi itself, so try it once
before relying on it.
