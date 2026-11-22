"""Command-line interface (Architecture §14). Exit codes: 0 ok (warnings allowed), 1 errors,
2 usage/IO error, 3 internal compiler error (§5.5)."""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from . import __version__
from .diagnostics import Sink, render, summary_line, to_ascii, to_json
from .options import Options
from .pipeline import compile_file


class UsageError(Exception):
    pass


def _stream_can_encode(stream, text: str = "✓⚠∈→") -> bool:
    enc = getattr(stream, "encoding", None) or "ascii"
    try:
        text.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _setup_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def _add_common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("file", help=".iot source file")
    sp.add_argument("--board", choices=["uno", "unoq"], default="unoq")
    sp.add_argument("--target", choices=["unoq", "mcu-only"], default=None)
    sp.add_argument("--json", action="store_true", help="machine-readable diagnostics")
    sp.add_argument("--ascii", action="store_true", help="ASCII-only output")
    sp.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    sp.add_argument("--werror", action="store_true", help="treat warnings as errors")
    sp.add_argument("--sample-ms", type=int, default=100)
    sp.add_argument("--heartbeat-ms", type=int, default=500)
    sp.add_argument("--failsafe-timeout-ms", type=int, default=2000)
    sp.add_argument("--place-windows", choices=["linux", "mcu"], default="linux")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="iotc", description="IoT Sensor DSL compiler for the Arduino UNO Q")
    p.add_argument("--version", action="version", version=f"iotc {__version__}")
    p.add_argument("--debug", action="store_true", help="show tracebacks for internal errors")
    sub = p.add_subparsers(dest="cmd")
    _add_common(sub.add_parser("check", help="diagnostics, analyses and reports"))
    for name, help_ in (("run", "run a trace through the reference interpreter"),
                        ("matrix-preview", "print the LED-matrix frame at a time in a trace")):
        sp = sub.add_parser(name, help=help_)
        _add_common(sp)
        sp.add_argument("--trace", required=True, help="trace CSV (Architecture §10.1)")
        sp.add_argument("--kill-linux-at", type=int, default=None, metavar="T_MS")
        sp.add_argument("--revive-linux-at", type=int, default=None, metavar="T_MS")
        sp.add_argument("--deaf-linux-at", type=int, default=None, metavar="T_MS",
                        help="Linux keeps heartbeating but stops processing samples")
        sp.add_argument("--link-up-at-start", action="store_true", help="scenario start (§9.9)")
        sp.add_argument("--mutant", choices=["disable-monitor", "single-pass-never", "fs-over-ensure"])
        if name == "matrix-preview":
            sp.add_argument("--at", type=int, required=True, metavar="T_MS")
    sp = sub.add_parser("build", help="generate the sketch / App folder")
    _add_common(sp)
    sp.add_argument("-o", "--out", required=True, help="output directory")
    sp.add_argument("--input", choices=["pins", "sim", "scenario"], default="pins")
    sp.add_argument("--scenario", help="scenario CSV (trace format, consecutive ticks)")
    sp.add_argument("--demo-controls", action="store_true", help="add the pause control to the page")
    sp = sub.add_parser("dashboard", help="run the full App on a virtual board on this laptop (demo backup)")
    _add_common(sp)
    sp.add_argument("--scenario", help="fallback scenario CSV: plays when no slider moves for 3 s")
    sp.add_argument("--autoplay", action="store_true", help="ignore the sliders and loop the scenario")
    sp.add_argument("--host", default="127.0.0.1", help="bind address (0.0.0.0 to reach it from a phone)")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    sp.add_argument("--seconds", type=float, default=None, help=argparse.SUPPRESS)   # tests: stop after N s
    for name in ("fuzz", "deploy"):
        sp = sub.add_parser(name)
        sp.add_argument("args", nargs=argparse.REMAINDER)
    return p


def options_from(args: argparse.Namespace) -> Options:
    try:
        return Options.make(board=args.board, target=args.target, sample_ms=args.sample_ms,
                            heartbeat_ms=args.heartbeat_ms, failsafe_timeout_ms=args.failsafe_timeout_ms,
                            place_windows=args.place_windows)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


def check_output(path: str, *, options: Options | None = None, as_json: bool = False,
                 ascii_only: bool = False, color: bool = False, werror: bool = False,
                 with_report: bool = True) -> tuple[str, int]:
    """Compile up to the analyses and render the result. Returns (text, exit code)."""
    sink = Sink(werror=werror)
    res = compile_file(path, options or Options(), sink)
    diags = sink.sorted()
    if as_json:
        reports = res.report.json() if res.report else None
        text = json.dumps(to_json(diags, res.source, reports=reports, suppressed=sink.suppressed),
                          ensure_ascii=False, indent=2)
    else:
        parts = []
        if diags or sink.suppressed:
            parts.append(render(diags, res.source, color=color, suppressed=sink.suppressed))
        if res.report is not None and with_report:
            parts.append(res.report.text())
        parts.append(f"{res.source.name}: {summary_line(diags, sink.suppressed)}")
        text = "\n\n".join(parts)
        if ascii_only:
            text = to_ascii(text)
    return text, (1 if sink.has_errors() else 0)


def _compile_or_report(args: argparse.Namespace):
    path = Path(args.file)
    if not path.is_file():
        raise UsageError(f"no such file: {args.file}")
    sink = Sink(werror=args.werror)
    res = compile_file(str(path), options_from(args), sink)
    if not res.ok:
        print(render(sink.sorted(), res.source, suppressed=sink.suppressed), file=sys.stderr)
        print(f"{res.source.name}: {summary_line(sink.items, sink.suppressed)}", file=sys.stderr)
        return None
    return res


def _interpreter(args: argparse.Namespace, ir, keep_frames: bool = False):
    from .interp import Interpreter, TraceError, load_trace
    try:
        trace = load_trace(args.trace, ir)
    except (TraceError, OSError) as exc:
        raise UsageError(str(exc)) from exc
    it = Interpreter(ir, kill_at=args.kill_linux_at, revive_at=args.revive_linux_at,
                     deaf_at=args.deaf_linux_at, link_up_at_start=args.link_up_at_start,
                     mutant=args.mutant, keep_frames=keep_frames)
    return it, it.run(trace)


def cmd_run(args: argparse.Namespace) -> int:
    from .interp import canonical
    res = _compile_or_report(args)
    if res is None:
        return 1
    it, events = _interpreter(args, res.ir)
    for line in canonical(events, it.table):
        print(line)
    return 0


def cmd_matrix_preview(args: argparse.Namespace) -> int:
    from .runtime.matrix import ascii_art
    res = _compile_or_report(args)
    if res is None:
        return 1
    it, _ = _interpreter(args, res.ir, keep_frames=True)
    shown = [f for f in it.frames if f[0] <= args.at]
    if not shown:
        raise UsageError(f"no trace row at or before t={args.at}")
    t, px = shown[-1]
    print(f"t={t} ms")
    print(ascii_art(px))
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    from .backend.app_writer import OutputError, write
    from .interp import TraceError, load_scenario
    res = _compile_or_report(args)
    if res is None:
        return 1
    if args.input == "scenario" and not args.scenario:
        raise UsageError("--input scenario needs --scenario FILE")
    scenario = None
    if args.scenario:
        try:
            scenario = load_scenario(args.scenario, res.ir)
        except (TraceError, OSError) as exc:
            raise UsageError(str(exc)) from exc
    try:
        files = write(res.ir, args.out, args.input, scenario, res.report, args.demo_controls)
    except OutputError as exc:
        raise UsageError(str(exc)) from exc
    warnings = sum(1 for d in res.sink.items if d.severity == "warning")
    print(f"built {args.out} (board {res.ir.board}, target {res.ir.target}, input {args.input}"
          f"{', ' + str(len(scenario)) + ' scenario rows' if scenario else ''}; {warnings} warning(s))")
    for f in files:
        print(f"  {f}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    import time
    import webbrowser

    from .interp import TraceError, load_scenario
    from .virtual_board import VirtualBoard
    res = _compile_or_report(args)
    if res is None:
        return 1
    if res.ir.board != "unoq" or res.ir.target != "unoq":
        raise UsageError("iotc dashboard runs the full UNO Q App: use --board unoq and --target unoq")
    if args.autoplay and not args.scenario:
        raise UsageError("--autoplay needs --scenario FILE")
    scenario = None
    if args.scenario:
        try:
            scenario = load_scenario(args.scenario, res.ir)
        except (TraceError, OSError) as exc:
            raise UsageError(str(exc)) from exc
    try:
        vb = VirtualBoard(res.ir, res.report, scenario, autoplay=args.autoplay, host=args.host, port=args.port)
    except OSError as exc:
        raise UsageError(f"cannot listen on {args.host}:{args.port}: {exc}") from exc
    vb.start()
    print(f"iotc virtual board: {vb.url}  (Ctrl+C to stop)", flush=True)
    print("This is a laptop simulation of the UNO Q App, not the board.", flush=True)
    if not args.no_browser:
        webbrowser.open(vb.url)
    try:
        deadline = None if args.seconds is None else time.monotonic() + args.seconds
        while deadline is None or time.monotonic() < deadline:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        vb.close()
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        raise UsageError(f"no such file: {args.file}")
    color = args.color == "always" or (args.color == "auto" and sys.stdout.isatty() and not args.json)
    ascii_only = args.ascii or not _stream_can_encode(sys.stdout)
    text, rc = check_output(str(path), options=options_from(args), as_json=args.json,
                            ascii_only=ascii_only, color=color, werror=args.werror)
    print(text)
    return rc


def main(argv: list[str] | None = None) -> int:
    _setup_streams()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code in (0, None) else 2
    if args.cmd is None:
        parser.print_help()
        return 2
    try:
        if args.cmd == "check":
            return cmd_check(args)
        if args.cmd == "build":
            return cmd_build(args)
        if args.cmd == "run":
            return cmd_run(args)
        if args.cmd == "matrix-preview":
            return cmd_matrix_preview(args)
        if args.cmd == "dashboard":
            return cmd_dashboard(args)
        print(f"iotc {args.cmd}: not implemented yet", file=sys.stderr)
        return 2
    except UsageError as exc:
        print(f"iotc: error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"iotc: error: {exc}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - exit code 3 contract
        print("iotc: internal compiler error, please report (run with --debug for details)",
              file=sys.stderr)
        if getattr(args, "debug", False):
            traceback.print_exc()
        return 3
