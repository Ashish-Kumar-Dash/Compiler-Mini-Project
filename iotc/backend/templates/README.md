# App templates (copied from the board, Phase 0)

Pulled with `scripts/pull_templates.ps1` from the UNO Q built-in example `blink-with-ui`
(arduino-app-cli 0.13.0, App Lab 0.10.0, app-bricks 0.12.0; 22 Nov 2026), then trimmed to what
the back end uses:

- `blink-with-ui/app.yaml`, `sketch/sketch.yaml`: the manifest shapes `app_writer.py` follows
  (one-emoji icon, `bricks: [arduino:web_ui]`, platform `arduino:zephyr` only).
- `blink-with-ui/assets/libs/arduino.js` (MPL-2.0, Arduino) and `socket.io.min.js` (Socket.IO 4.8.1,
  MIT): copied verbatim into every generated App; the page needs no CDN.
- `python/main.py`, `sketch/sketch.ino`, `assets/*`: reference only.

The board's `home-climate-monitoring-and-storage` example ships no Chart.js, so the generated
dashboard draws its charts on a plain canvas.
