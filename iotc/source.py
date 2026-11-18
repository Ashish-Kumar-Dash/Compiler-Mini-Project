"""Source loading, normalisation, hashing and deterministic file writing (Architecture §4.2, §5.1).

* Input files are read as bytes. A UTF-8 BOM is stripped, CRLF and lone CR become LF, and the
  result must decode as strict UTF-8; otherwise an L000 location is reported.
* Columns are 1-based Unicode code points; a tab counts as one column.
* Every generated file is written through write_text(): UTF-8, LF only, exactly one trailing
  newline (Architecture §0 rule 7).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePath

BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class Span:
    """A source range. Lines and columns are 1-based; end_col is exclusive."""

    line: int
    col: int
    end_line: int
    end_col: int

    @staticmethod
    def point(line: int, col: int, length: int = 1) -> "Span":
        return Span(line, col, line, col + max(length, 1))

    def to(self, other: "Span") -> "Span":
        """The span from the start of self to the end of other."""
        return Span(self.line, self.col, other.end_line, other.end_col)


@dataclass(frozen=True)
class DecodeIssue:
    """Where strict UTF-8 decoding failed (becomes diagnostic L000)."""

    line: int
    col: int
    byte: int


@dataclass(frozen=True)
class Source:
    name: str          # display name, POSIX separators
    text: str          # normalised text (LF only, no BOM)
    sha256: str        # hex digest of the normalised UTF-8 bytes
    issue: DecodeIssue | None = None

    @property
    def lines(self) -> list[str]:
        return self.text.split("\n")

    def line_text(self, line: int) -> str:
        lines = self.lines
        return lines[line - 1] if 1 <= line <= len(lines) else ""

    @property
    def short_sha(self) -> str:
        return self.sha256[:16]


def normalise_bytes(data: bytes) -> bytes:
    if data.startswith(BOM):
        data = data[len(BOM):]
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def decode_source(name: str, data: bytes) -> Source:
    """Normalise and decode. On invalid UTF-8 the text is decoded with replacement characters
    and `issue` records the first bad byte's position."""
    norm = normalise_bytes(data)
    digest = hashlib.sha256(norm).hexdigest()
    try:
        text = norm.decode("utf-8")
        issue = None
    except UnicodeDecodeError as exc:
        text = norm.decode("utf-8", errors="replace")
        before = norm[: exc.start]
        line = before.count(b"\n") + 1
        line_start = before.rfind(b"\n") + 1
        col = len(before[line_start:].decode("utf-8", errors="replace")) + 1
        issue = DecodeIssue(line, col, norm[exc.start])
    return Source(display_name(name), text, digest, issue)


def display_name(name: str | PurePath) -> str:
    """POSIX-style name, relative to the current directory when possible."""
    p = Path(name)
    try:
        p = p.resolve().relative_to(Path.cwd().resolve())
    except (ValueError, OSError):
        pass
    return PurePath(p).as_posix()


def load_source(path: str | Path) -> Source:
    return decode_source(str(path), Path(path).read_bytes())


def write_text(path: str | Path, text: str) -> None:
    """Write generated text deterministically: UTF-8, LF only, exactly one trailing newline."""
    if "\r" in text:
        raise ValueError(f"generated text for {path} contains a carriage return")
    text = text.rstrip("\n") + "\n"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_bytes(path: str | Path, data: bytes) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
