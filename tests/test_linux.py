"""Linux back end (Architecture §9.4, §11.6, §11.9): the generated main.py, run on the laptop with
stub arduino.* modules, must send exactly the heartbeats and NOTIFY lines of the interpreter's
Linux model; plus robustness (ranges, pause, MCU reset) and dashboard static checks."""
from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from iotc.backend.app_writer import write
from iotc.backend.webpage import check_static
from iotc.interp import Interpreter, canonical, parse_trace
from iotc.options import Options
from iotc.pipeline import compile_file

ROOT = Path(__file__).resolve().parents[1]
DEMO = (ROOT / "tests" / "traces" / "demo_script.csv").read_text()


class Recorder:
    def __init__(self):
        self.notifies = []
        self.provided = {}
        self.messages = []
        self.ui_handlers = {}

    # Bridge
    def notify(self, name, *args):
        self.notifies.append((name, args))

    def provide(self, name, fn):
        self.provided[name] = fn

    # WebUI
    def send_message(self, kind, data, room=None):
        self.messages.append((kind, data))


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Build the board demo App and import its main.py with stub arduino modules."""
    res = compile_file(ROOT / "examples" / "greenhouse_board.iot", Options.make("unoq"))
    out = tmp_path / "app"
    write(res.ir, out, "sim", None, res.report, True)
    rec = Recorder()
    app_utils = types.ModuleType("arduino.app_utils")
    app_utils.App = types.SimpleNamespace(run=lambda user_loop=None: None)
    app_utils.Bridge = types.SimpleNamespace(notify=rec.notify, provide=rec.provide)
    app_utils.Leds = None
    web_ui = types.ModuleType("arduino.app_bricks.web_ui")

    class WebUI:
        def send_message(self, kind, data, room=None):
            rec.send_message(kind, data, room)

        def on_message(self, kind, fn):
            rec.ui_handlers[kind] = fn

        def on_connect(self, fn):
            rec.ui_handlers["connect"] = fn

        def expose_api(self, method, path, fn):
            rec.ui_handlers[path] = fn

    web_ui.WebUI = WebUI
    for name, mod in (("arduino", types.ModuleType("arduino")), ("arduino.app_utils", app_utils),
                      ("arduino.app_bricks", types.ModuleType("arduino.app_bricks")),
                      ("arduino.app_bricks.web_ui", web_ui)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.syspath_prepend(str(out / "python"))
    for m in [m for m in sys.modules if m == "iotc_runtime" or m.startswith("iotc_runtime.")]:
        monkeypatch.delitem(sys.modules, m)
    spec = importlib.util.spec_from_file_location("generated_main", out / "python" / "main.py")
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)
    main.Leds = None
    return types.SimpleNamespace(main=main, rec=rec, ir=res.ir, out=out)


def test_main_py_is_python_313_and_uses_no_bridge_call(app):
    src = (app.out / "python" / "main.py").read_text(encoding="utf-8")
    ast.parse(src, feature_version=(3, 13))
    assert "Bridge.call" not in src and "requirements" not in src


def test_runtime_is_a_byte_copy(app):
    for name in ("__init__.py", "window.py", "engine.py", "matrix.py"):
        assert (app.out / "python" / "iotc_runtime" / name).read_bytes() == (ROOT / "iotc" / "runtime" / name).read_bytes()


def test_cosim_heartbeats_and_notifies_match_interpreter(app):
    """Feed the demo trace to the generated Brain on a fake clock; compare with LinuxSim."""
    ir = app.ir
    trace = parse_trace(DEMO, ir)
    it = Interpreter(ir)
    events = it.run(trace)
    want_hb = [hb for _, hb in it.heartbeats]
    want_nt = [line for line in canonical(events, it.table) if line.startswith("NT")]
    lines = []
    brain = app.main.Brain(app.main.TABLE, app.rec.notify, lambda k, d: None, lines.append, lambda: 0.0)
    for t, codes in trace.rows:
        now = t / 1000.0
        brain.on_sample(t, codes, now)
        brain.tick(now)
    got_hb = [args for name, args in app.rec.notifies if name == "heartbeat"]
    # the interpreter delivers each heartbeat one row later; the last one may not be delivered yet
    assert got_hb[: len(want_hb)] == want_hb
    assert len(got_hb) - len(want_hb) <= 1
    assert [line for line in lines if line.startswith("NT")] == want_nt


def test_event_and_link_lines_are_canonical(app):
    lines = []
    brain = app.main.Brain(app.main.TABLE, app.rec.notify, lambda k, d: None, lines.append, lambda: 0.0)
    brain.on_link(100, 1)
    brain.on_event(3800, 1, 1, 2, 0, 0xFF)          # heater ON by rule 2 (warm), applied
    brain.on_event(15500, 0, 1, 0, 1, 0)            # fan ON by cool, refused by never#1
    brain.on_event(16700, 1, 0, 0xFF, 3, 0xFF)      # heater OFF failsafe
    brain.on_event(45000, 0, 1, 0xF0, 2, 0xFF)      # forced by ENSURE#1
    assert lines == ["LK,100,UP", "EV,3800,heater,ON,warm,applied,-", "EV,15500,fan,ON,cool,refused,never#1",
                     "EV,16700,heater,OFF,FAILSAFE,failsafe,-", "EV,45000,fan,ON,ENSURE#1,forced,-"]
    assert brain.cur == [1, 0]


def test_sliders_are_clamped_and_sent_with_exact_types(app):
    brain = app.main.Brain(app.main.TABLE, app.rec.notify, lambda k, d: None, lambda s: None, lambda: 0.0)
    brain.set_slider(0, 5000)          # clamped to adc_max
    brain.set_slider(1, -3)
    brain.set_slider(9, 100)           # ignored: no such sensor
    brain.tick(1.0)
    sims = [args for name, args in app.rec.notifies if name == "sim_set"]
    assert sims == [(0, 1023), (1, 0)]
    assert all(isinstance(a, int) for args in sims for a in args)


def test_pause_stops_heartbeats_and_evaluation(app):
    brain = app.main.Brain(app.main.TABLE, app.rec.notify, lambda k, d: None, lambda s: None, lambda: 0.0)
    brain.pause(10, now=0.0)
    for i in range(50):
        brain.on_sample(i * 100, [310, 512], i * 0.1)
        brain.tick(i * 0.1)
    assert not [1 for name, _ in app.rec.notifies if name == "heartbeat"]
    brain.resume()
    brain.on_sample(5000, [310, 512], 5.0)
    assert [1 for name, _ in app.rec.notifies if name == "heartbeat"]


def test_mcu_reset_is_detected(app):
    lines = []
    brain = app.main.Brain(app.main.TABLE, app.rec.notify, lambda k, d: None, lines.append, lambda: 0.0)
    for t in (100000, 100100, 100200):
        brain.on_sample(t, [310, 512], t / 1000)
    brain.on_sample(300, [310, 512], 100.3)
    assert "I,300,mcu reset" in lines


def test_samples_stopping_make_the_acknowledgement_stale(app):
    """If MCU samples stop, the fallback heartbeat repeats the old ack, so the MCU fails safe."""
    brain = app.main.Brain(app.main.TABLE, app.rec.notify, lambda k, d: None, lambda s: None, lambda: 0.0)
    brain.on_sample(1000, [400, 512], 1.0)
    for i in range(1, 30):
        brain.tick(1.0 + i * 0.1)
    acks = {args[2] for name, args in app.rec.notifies if name == "heartbeat"}
    assert acks == {1000}


def test_snapshot_and_state_are_json(app):
    brain = app.main.Brain(app.main.TABLE, app.rec.notify, lambda k, d: None, lambda s: None, lambda: 0.0)
    brain.on_sample(100, [466, 512], 0.1)
    snap = brain.snapshot(0.2)
    json.dumps(snap)
    assert len(snap["state"]["matrix"]) == 104 and snap["state"]["firing"][0] == 1   # cool firing


def test_dashboard_static_safety(app):
    html = (app.out / "assets" / "index.html").read_text(encoding="utf-8")
    js = (app.out / "assets" / "app.js").read_text(encoding="utf-8")
    assert check_static(html, js) == []
    assert "Content-Security-Policy" in html
    cfg = json.loads((app.out / "assets" / "config.json").read_text(encoding="utf-8"))
    assert [s["name"] for s in cfg["sensors"]] == ["temp", "humidity"]
    assert {t["code"] for t in cfg["sensors"][0]["ticks"]} >= {433, 466, 528, 559}
    assert cfg["demo_controls"] is True and cfg["input"] == "sim"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_app_js_parses(app):
    subprocess.run(["node", "--check", str(app.out / "assets" / "app.js")], check=True)


def test_app_yaml_has_web_ui_brick(app):
    text = (app.out / "app.yaml").read_text(encoding="utf-8")
    assert "arduino:web_ui" in text and "ports:" not in text
