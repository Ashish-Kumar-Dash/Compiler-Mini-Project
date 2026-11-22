"""`iotc doctor`: check the laptop tools and the board before a demo (Architecture §14).

Required: Python >= 3.11. Everything else is reported as a warning when missing, with the fix:
node (page script check), WSL g++ (differential and conformance tests), adb plus a connected UNO Q,
the App Lab CLI version against the one recorded in Phase 0, the running App and the page tunnel.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "hardware" / "phase0" / "results" / "versions.txt"
OK, WARN, FAIL = "ok", "warn", "fail"


def _run(cmd: list[str], timeout: float = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)


def frozen_cli_version() -> str | None:
    if not VERSIONS.is_file():
        return None
    m = re.search(r"Arduino App CLI version (\S+)", VERSIONS.read_text(encoding="utf-8-sig", errors="replace"))
    return m.group(1) if m else None


def checks() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    v = sys.version_info
    out.append(("Python", OK if v >= (3, 11) else FAIL, f"{v.major}.{v.minor}.{v.micro}"
                + ("" if v >= (3, 11) else " (needs 3.11 or newer; 3.13 recommended)")))
    node = shutil.which("node")
    out.append(("node", OK if node else WARN, node or "not found: the page-script syntax test is skipped"))
    if shutil.which("wsl"):
        rc, text = _run(["wsl", "-e", "sh", "-c", "g++ --version | head -1"], 60)
        out.append(("WSL g++", OK if rc == 0 and text else WARN,
                    text if rc == 0 and text else "not available: C++ differential/conformance tests are skipped"))
    else:
        out.append(("WSL g++", WARN, "WSL not found: C++ differential/conformance tests are skipped"))
    cli = shutil.which("arduino-cli")
    out.append(("arduino-cli", OK if cli else WARN, cli or "not found (optional: App Lab compiles on the board)"))

    adb = shutil.which("adb")
    if not adb:
        out.append(("adb", WARN, "not found: winget install Google.PlatformTools, then open a new terminal"))
        return out
    out.append(("adb", OK, adb))
    rc, text = _run(["adb", "devices"])
    devices = [ln.split()[0] for ln in text.splitlines()[1:] if ln.strip().endswith("device")]
    if not devices:
        out.append(("board", WARN, "no UNO Q found by adb: connect a data USB-C cable and wait for the boot"))
        return out
    out.append(("board", OK, f"connected ({', '.join(devices)})"))
    rc, text = _run(["adb", "shell", "arduino-app-cli version"])
    m = re.search(r"version (\S+)", text)
    frozen = frozen_cli_version()
    if not m:
        out.append(("App Lab CLI", WARN, f"could not read the version: {text[:120]}"))
    elif frozen and m.group(1) != frozen:
        out.append(("App Lab CLI", WARN, f"{m.group(1)}, but Phase 0 measured {frozen}: re-run B2 before the demo"))
    else:
        out.append(("App Lab CLI", OK, m.group(1) + (" (same as Phase 0)" if frozen else "")))
    rc, text = _run(["adb", "shell", "arduino-app-cli app list"])
    running = [re.sub(r"\s{2,}", "  ", ln.strip()) for ln in text.splitlines() if "running" in ln.lower()]
    out.append(("running App", OK if running else WARN,
                "; ".join(running) if running else "none running (deploy with scripts\\deploy.ps1)"))
    rc, text = _run(["adb", "shell", "systemctl is-active arduino-router"])
    out.append(("Bridge router", OK if text.strip() == "active" else WARN, text.strip() or "unknown"))
    rc, text = _run(["adb", "forward", "--list"])
    tunnel = "tcp:7000" in text
    out.append(("page tunnel", OK if tunnel else WARN,
                "localhost:7000 forwarded to the board" if tunnel else "not set: run scripts\\open_page.ps1"))
    return out


def main(ascii_only: bool = False) -> int:
    marks = {OK: "[ok]  ", WARN: "[warn]", FAIL: "[FAIL]"} if ascii_only else {OK: "✓", WARN: "⚠", FAIL: "✗"}
    results = checks()
    width = max(len(name) for name, _, _ in results)
    for name, status, detail in results:
        print(f"{marks[status]} {name.ljust(width)}  {detail}")
    fails = sum(1 for _, s, _ in results if s == FAIL)
    warns = sum(1 for _, s, _ in results if s == WARN)
    print(f"\n{fails} problem(s), {warns} warning(s)")
    return 1 if fails else 0
