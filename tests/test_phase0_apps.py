"""Static guards for the hand-written Phase 0 Apps (Architecture §11.7 template rules, §15.1)."""
import py_compile
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APPS = [ROOT / "hardware" / "phase0" / "probe", ROOT / "hardware" / "phase0" / "serial_probe"]


def _yaml_field(text: str, key: str) -> str:
    for line in text.splitlines():
        if line.startswith(key + ":"):
            return line.split(":", 1)[1].strip()
    raise KeyError(key)


def _is_single_emoji(s: str) -> bool:
    chars = [c for c in s if unicodedata.category(c) not in ("Mn", "Cf")]
    return len(chars) == 1 and ord(chars[0]) > 0x2000


@pytest.mark.parametrize("app", APPS, ids=lambda p: p.name)
def test_app_folder(app, tmp_path):
    app_yaml = (app / "app.yaml").read_text(encoding="utf-8")
    assert _is_single_emoji(_yaml_field(app_yaml, "icon"))
    assert "ports:" not in app_yaml
    sketch_yaml = (app / "sketch" / "sketch.yaml").read_text(encoding="utf-8")
    assert "platform: arduino:zephyr" in sketch_yaml
    assert "RouterBridge" not in sketch_yaml
    assert not (app / "python" / "requirements.txt").exists()
    py_compile.compile(str(app / "python" / "main.py"), cfile=str(tmp_path / "m.pyc"), doraise=True)
    for f in app.rglob("*"):
        if f.is_file():
            assert b"\r" not in f.read_bytes(), f


def test_probe_loop_never_prints_except_serial_test():
    src = (APPS[0] / "sketch" / "iotc_probe.h").read_text(encoding="utf-8")
    loop = src.split("static void loop()", 1)[1]
    assert loop.count("Serial.") == 2  # only the one-off timed begin + println test
    assert "delay(" not in src and ".call(" not in src
