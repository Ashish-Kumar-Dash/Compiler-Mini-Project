"""iotc doctor: tool and board checks degrade to warnings, never crash."""
from iotc import doctor


def test_frozen_version_is_read_from_phase0_results():
    assert doctor.frozen_cli_version() == "0.13.0"


def test_missing_tools_are_warnings(monkeypatch, capsys):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert doctor.main(ascii_only=True) == 0
    out = capsys.readouterr().out
    assert "[warn] adb" in out and "winget install Google.PlatformTools" in out and "0 problem(s)" in out


def test_version_drift_is_reported(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "x" if name == "adb" else None)
    answers = {"devices": "List of devices attached\nABC\tdevice\n", "version": "Arduino App CLI version 0.14.1",
               "list": "", "systemctl": "active", "--list": ""}

    def fake_run(cmd, timeout=20):
        key = next(k for k in answers if k in " ".join(cmd))
        return 0, answers[key]
    monkeypatch.setattr(doctor, "_run", fake_run)
    res = {name: (st, detail) for name, st, detail in doctor.checks()}
    assert res["App Lab CLI"][0] == doctor.WARN and "0.13.0" in res["App Lab CLI"][1]
    assert res["page tunnel"][0] == doctor.WARN and res["Bridge router"][0] == doctor.OK
