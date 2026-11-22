# Stage 4: generated UNO Q App (selected files)

Reproduce:

```
py -3.13 -m iotc build examples/greenhouse_board.iot --input sim --demo-controls --scenario scenarios/fallback_sweep.csv -o build/b2_board
```

The full App folder contains:

```
  app.yaml
  assets/app.js
  assets/config.json
  assets/index.html
  assets/libs/arduino.js
  assets/libs/socket.io.min.js
  assets/style.css
  python/iotc_runtime/__init__.py
  python/iotc_runtime/engine.py
  python/iotc_runtime/matrix.py
  python/iotc_runtime/window.py
  python/main.py
  sketch/iotc_gen.h
  sketch/sketch.ino
  sketch/sketch.yaml
```

Copied here: the MCU sketch (`sketch/`), the Linux program (`python/main.py`), the App manifest and the dashboard configuration. The runtime package, page script and client libraries are omitted (they are byte copies of `iotc/runtime/` and `iotc/backend/web/`).
