"""Virtual UNO Q (Architecture §15, demo fallback ladder): the generated App runs on the laptop.

The generated python/main.py is imported unchanged. Its `arduino.*` imports are replaced by:
  * a virtual MCU: runtime.mcu.McuModel (the normative pass, equal to the C++ by the differential
    test) wrapped in the sketch's Bridge glue from cpp_core.py: handler snapshot, hb_ack, the sim
    input latch with the 3 s fallback to the compiled-in scenario, sample / event / link / stats;
  * a small stdlib web server standing in for the WebUI brick. socket.io becomes Server-Sent Events
    (page <- Linux) plus POST (page -> Linux); app.js runs unchanged through a replacement
    libs/arduino.js.

The MCU runs in its own thread and the Linux side in another, so they meet only through the Bridge
calls and main.py's inbox queue, as on the board. Use: `iotc dashboard FILE --scenario CSV`.
"""
from __future__ import annotations

import importlib.util
import json
import queue
import sys
import tempfile
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .backend import bridge
from .backend.linux_py import sim_init
from .ir import IRProgram
from .runtime.mcu import McuModel
from .table import build_table

SIM_TIMEOUT_MS = 3000           # cpp_core.py SIM_TIMEOUT_MS
MAX_POST = 64 * 1024

# Replacement for the WebUI brick's libs/arduino.js: the same three calls app.js uses.
SHIM_JS = """// iotc virtual board: stands in for the WebUI client (socket.io) on the laptop.
'use strict';
window.WebUI = class WebUI {
  constructor() {
    this.handlers = {};
    this.sid = null;
    this.source = new EventSource('_iotc/events');
    this.source.onmessage = (m) => {
      const d = JSON.parse(m.data);
      if (d.kind === '_sid') { this.sid = d.data; return; }
      const f = this.handlers[d.kind];
      if (f) f(d.data);
    };
  }
  on_message(kind, fn) { this.handlers[kind] = fn; }
  send_message(kind, data) {
    fetch('_iotc/send', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: kind, data: data, sid: this.sid }) });
  }
};
"""

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".json": "application/json"}


class VirtualMcu:
    """The sketch's loop() in sim input mode around McuModel (cpp_core.py pass(), steps 1-12)."""

    def __init__(self, table: dict, scenario: list[list[int]] | None, sim_init: list[int],
                 clock, deadline_ms: int | None = None, autoplay: bool = False) -> None:
        self.t = table
        self.model = McuModel(table)
        self.S = table["sample_ms"]
        self.clock = clock                       # -> board milliseconds
        self.scen = scenario or []
        self.autoplay = autoplay and bool(self.scen)
        self.lock = threading.Lock()
        self.rx: tuple[int, int, int, int] | None = None
        self.sim_rx = list(sim_init)
        self.sim_rx_ms = 0
        self.sim_ever = False
        self.codes = list(sim_init)
        self.fb_row = 0
        self.last_n: int | None = None
        self.provided: dict = {}
        self.last_stats: int | None = None
        self.deadline_us = (deadline_ms or 0) * 1000
        self.max_pass_us = 0
        self.overruns = 0
        self.last_pass: float | None = None
        self.rule_id = {r["name"]: i for i, r in enumerate(table["rules"])}

    # -- Bridge seen from main.py: Bridge.notify / Bridge.provide -----------------------------
    def notify(self, name: str, *args) -> None:
        """Linux -> MCU (the provide_safe handlers): store under the lock, as the sketch does."""
        msg = bridge.by_name(name)
        kinds = [k for _, k in msg.args]
        if len(args) != len(kinds) or not all(isinstance(a, int) and not isinstance(a, bool) for a in args):
            raise TypeError(f"{name}{args}: the MCU handler takes exactly {len(kinds)} integers")
        for a, k in zip(args, kinds):
            bridge.check_arg(k, a)
        now = self.clock()
        with self.lock:
            if name == "heartbeat":
                self.rx = (args[0], args[1], args[2], now)
            elif name == "sim_set":
                s, c = args
                if s < len(self.sim_rx) and c <= self.t["sensors"][s]["adc_max"]:
                    self.sim_rx[s] = c
                    self.sim_rx_ms = now
                    self.sim_ever = True

    def provide(self, name: str, fn) -> None:
        self.provided[name] = fn

    def _send(self, name: str, *args) -> None:
        fn = self.provided.get(name)
        if fn is not None:
            fn(*args)

    # -- one pass ---------------------------------------------------------------------------
    def encode(self, ev: tuple) -> tuple:
        _, t, a, s, src, result, detail = ev
        if src == "FAILSAFE":
            code = bridge.SRC_FAILSAFE
        elif src.startswith("ENSURE#"):
            code = bridge.SRC_ENSURE_BASE + int(src[7:]) - 1
        else:
            code = self.rule_id[src]
        det = bridge.DETAIL_NONE if detail == "-" else int(detail.split("#")[1]) - 1
        return (t, a, s, code, bridge.RESULTS.index(result), det)

    def pass_(self, now: int) -> None:
        wall = time.perf_counter()
        if self.last_pass is not None:
            dt = int((wall - self.last_pass) * 1e6)
            self.max_pass_us = max(self.max_pass_us, dt)
            if self.deadline_us and dt > self.deadline_us:
                self.overruns += 1
        self.last_pass = wall
        with self.lock:                                         # step 1: snapshot (ADR-23)
            rx, self.rx = self.rx, None
            sim, ever, at = list(self.sim_rx), self.sim_ever, self.sim_rx_ms
        if rx is not None:
            self.model.deliver_heartbeat(*rx)
            self._send("hb_ack", rx[0])
        n = now // self.S
        tick = n != self.last_n
        if tick:                                                # step 3: inputs (sim latch)
            self.last_n = n
            stale = self.autoplay or not ever or now - at >= SIM_TIMEOUT_MS
            if stale and self.scen:
                self.codes = list(self.scen[self.fb_row])
                self.fb_row = (self.fb_row + 1) % len(self.scen)
            elif ever:
                self.codes = sim
        for ev in self.model.step(now, self.codes):
            if ev[0] == "LK":
                self._send("link", ev[1], 1 if ev[2] else 0)
            elif ev[0] == "EV":
                self._send("event", *self.encode(ev))
        if tick:                                                # step 12: stream
            self._send("sample", n * self.S, *self.codes)
        if self.last_stats is None or now - self.last_stats >= 1000:
            self.last_stats = now
            self._send("stats", now, self.overruns, self.max_pass_us, 0)


class Hub:
    """Stands in for the WebUI brick: send_message / on_message / on_connect / expose_api."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.clients: dict[str, queue.Queue] = {}
        self.handlers: dict = {}
        self.connect_cb = None
        self.apis: dict[str, object] = {}
        self.next_sid = 0

    def __call__(self, *args, **kwargs):        # `ui = WebUI()` in main.py returns the hub
        return self

    def send_message(self, kind, data, room=None) -> None:
        payload = json.dumps({"kind": kind, "data": data}, separators=(",", ":"))
        with self.lock:
            targets = [q for sid, q in self.clients.items() if room is None or sid == room]
        for q in targets:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass                                # a stalled browser never blocks the brain

    def on_message(self, kind, fn) -> None:
        self.handlers[kind] = fn

    def on_connect(self, fn) -> None:
        self.connect_cb = fn

    def expose_api(self, method, path, fn) -> None:
        self.apis[path] = fn

    def open(self) -> tuple[str, queue.Queue]:
        with self.lock:
            self.next_sid += 1
            sid = f"v{self.next_sid}"
            q: queue.Queue = queue.Queue(maxsize=2000)
            self.clients[sid] = q
        return sid, q

    def close(self, sid: str) -> None:
        with self.lock:
            self.clients.pop(sid, None)

    def close_all(self) -> None:
        with self.lock:
            qs = list(self.clients.values())
        for q in qs:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass


def load_main(app_dir: Path, mcu: VirtualMcu, hub: Hub):
    """Import the generated main.py with the virtual Bridge and WebUI; return (module, loop)."""
    captured: dict = {}
    app_utils = types.ModuleType("arduino.app_utils")
    app_utils.App = types.SimpleNamespace(run=lambda user_loop=None: captured.update(loop=user_loop))
    app_utils.Bridge = types.SimpleNamespace(notify=mcu.notify, provide=mcu.provide)
    web_ui = types.ModuleType("arduino.app_bricks.web_ui")
    web_ui.WebUI = hub
    saved = {k: sys.modules.get(k) for k in ("arduino", "arduino.app_utils", "arduino.app_bricks",
                                              "arduino.app_bricks.web_ui")}
    runtime_mods = [m for m in sys.modules if m == "iotc_runtime" or m.startswith("iotc_runtime.")]
    for m in runtime_mods:
        del sys.modules[m]
    sys.modules.update({"arduino": types.ModuleType("arduino"), "arduino.app_utils": app_utils,
                        "arduino.app_bricks": types.ModuleType("arduino.app_bricks"),
                        "arduino.app_bricks.web_ui": web_ui})
    sys.path.insert(0, str(app_dir / "python"))
    try:
        spec = importlib.util.spec_from_file_location("iotc_virtual_main", app_dir / "python" / "main.py")
        main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main)
    finally:
        sys.path.remove(str(app_dir / "python"))
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return main, captured["loop"]


def make_handler(assets: Path, hub: Hub, config: bytes):
    class Handler(BaseHTTPRequestHandler):
        server_version = "iotc-virtual-board"

        def log_message(self, fmt, *args):      # quiet; the event log is on stdout
            pass

        def _reply(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/_iotc/events":
                return self._events()
            if path in hub.apis:
                return self._reply(200, json.dumps(hub.apis[path]()).encode(), CONTENT_TYPES[".json"])
            if path in ("/", "/index.html"):
                path = "/index.html"
            if path == "/config.json":
                return self._reply(200, config, CONTENT_TYPES[".json"])
            if path == "/libs/arduino.js":
                return self._reply(200, SHIM_JS.encode(), CONTENT_TYPES[".js"])
            if path == "/libs/socket.io.min.js":
                return self._reply(200, b"// not needed on the virtual board\n", CONTENT_TYPES[".js"])
            target = (assets / path.lstrip("/")).resolve()
            if assets.resolve() not in target.parents or not target.is_file():
                return self._reply(404, b"not found", "text/plain")
            return self._reply(200, target.read_bytes(),
                               CONTENT_TYPES.get(target.suffix, "application/octet-stream"))

        def do_POST(self):
            if urlsplit(self.path).path != "/_iotc/send":
                return self._reply(404, b"not found", "text/plain")
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_POST:
                return self._reply(413, b"too large", "text/plain")
            try:
                msg = json.loads(self.rfile.read(n))
                fn = hub.handlers.get(msg["kind"])
                if fn is not None:
                    fn(msg.get("sid"), msg.get("data"))
            except (ValueError, KeyError, TypeError):
                return self._reply(400, b"bad message", "text/plain")
            return self._reply(204, b"", "text/plain")

        def _events(self):
            sid, q = hub.open()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(f'data: {{"kind":"_sid","data":"{sid}"}}\n\n'.encode())
                self.wfile.flush()
                if hub.connect_cb is not None:
                    hub.connect_cb(sid)
                while True:
                    try:
                        item = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    if item is None:
                        return
                    self.wfile.write(b"data: " + item.encode() + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return
            finally:
                hub.close(sid)

    return Handler


class VirtualBoard:
    """Build the App into a temporary folder, start the MCU and Linux threads and the web server."""

    def __init__(self, ir: IRProgram, report, scenario: list[list[int]] | None, *, autoplay: bool = False,
                 host: str = "127.0.0.1", port: int = 8000, emit=None) -> None:
        from .backend.app_writer import write
        if ir.board != "unoq" or ir.target != "unoq":
            raise ValueError("the virtual board runs the full UNO Q App: use --board unoq --target unoq")
        self.tmp = tempfile.TemporaryDirectory(prefix="iotc-virtual-")
        self.app = Path(self.tmp.name) / "app"
        write(ir, self.app, "sim", scenario, report, True)
        cfg = json.loads((self.app / "assets" / "config.json").read_text(encoding="utf-8"))
        cfg["input"] = "autoplay" if autoplay and scenario else "sim"
        cfg["virtual"] = True
        self.config = json.dumps(cfg, ensure_ascii=False).encode("utf-8")
        self.t0 = time.monotonic()
        self.hub = Hub()

        def clock() -> int:
            return int((time.monotonic() - self.t0) * 1000)

        # no REALTIME deadline: laptop thread timing says nothing about the MCU's pass time
        self.mcu = VirtualMcu(build_table(ir), scenario, sim_init(ir, scenario), clock, None, autoplay)
        self.main_mod, self.linux_loop = load_main(self.app, self.mcu, self.hub)
        if emit is not None:                     # redirect the App's stdout log (tests)
            self.main_mod._emit = emit
            self.main_mod.brain.emit = emit
        self.stop = threading.Event()
        self.server = ThreadingHTTPServer((host, port), make_handler(self.app / "assets", self.hub, self.config))
        self.server.daemon_threads = True
        self.threads = [threading.Thread(target=self._run_mcu, name="virtual-mcu", daemon=True),
                        threading.Thread(target=self._run_linux, name="virtual-linux", daemon=True),
                        threading.Thread(target=self.server.serve_forever, name="virtual-web", daemon=True)]

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}/"

    def _run_mcu(self) -> None:
        while not self.stop.is_set():
            self.mcu.pass_(self.mcu.clock())
            time.sleep(0.002)

    def _run_linux(self) -> None:
        while not self.stop.is_set():
            self.linux_loop()                  # main.py's loop(): drains the inbox, ticks, sleeps 5 ms

    def start(self) -> "VirtualBoard":
        for th in self.threads:
            th.start()
        return self

    def close(self) -> None:
        self.stop.set()
        self.hub.close_all()
        self.server.shutdown()
        self.server.server_close()
        for th in self.threads[:2]:
            th.join(timeout=2)
        self.tmp.cleanup()
