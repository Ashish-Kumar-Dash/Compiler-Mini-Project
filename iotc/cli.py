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
    for name in ("build", "run", "fuzz", "matrix-preview", "deploy"):
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
