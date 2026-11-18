"""Hand-written single-pass lexer (Architecture §4.2, §5.1).

The lexer never stops: a bad character or token becomes one ERROR token plus one L-diagnostic,
and lexing continues. Columns are 1-based code points; a tab counts as one column.
"""
from __future__ import annotations

from fractions import Fraction

from .diagnostics import Sink
from .source import Source, Span
from .tokens import (
    AGGREGATES,
    DURATION_MS,
    K,
    KEYWORD_SET,
    KEYWORDS,
    PIN_EXAMPLES,
    PIN_RE,
    PUNCTUATION,
    UNIT_SET,
    UNITS,
    Token,
    canonical_pin,
)

MAX_INT_DIGITS = 9
MAX_IDENT = 32
MAX_NOTIFY = 64

CONFUSABLES = {
    "−": "use the ASCII minus sign '-'",
    "–": "use the ASCII minus sign '-'",
    "—": "use the ASCII minus sign '-'",
    " ": "this is a non-breaking space; use a normal space",
    "＞": "use ASCII '>'",
    "＜": "use ASCII '<'",
    "＝": "use ASCII '='",
    "‘": "use ASCII double quotes \"...\"",
    "’": "use ASCII double quotes \"...\"",
    "“": "use ASCII double quotes \"...\"",
    "”": "use ASCII double quotes \"...\"",
    "…": "use two ASCII dots '..' for ranges",
}

SUFFIX_HINTS = {
    "m": "min", "mn": "min", "mins": "min", "minute": "min", "minutes": "min",
    "sec": "s", "secs": "s", "second": "s", "seconds": "s",
    "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
    "msec": "ms", "msecs": "ms", "millis": "ms",
    "MS": "ms", "S": "s", "MIN": "min", "H": "h", "Ms": "ms", "Min": "min",
}

SPECIAL_WORD_HINTS = {
    "OR": "OR is not supported: write two rules",
    "NOT": "NOT is not supported: invert the comparison or write two rules",
    "IF": "rules start with RULE name: WHEN ...",
    "AVG": "aggregates are lowercase: avg",
    "MIN": "aggregates are lowercase: min (durations are written 5min)",
    "MAX": "aggregates are lowercase: max",
    "TRUE": "conditions compare a sensor with a number, e.g. door == 1",
    "FALSE": "conditions compare a sensor with a number, e.g. door == 0",
}


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def did_you_mean(word: str, candidates, max_dist: int | None = None) -> str | None:
    """Closest candidate within distance max(1, len/3) (capped at 2); ties by candidate order."""
    limit = max_dist if max_dist is not None else min(2, max(1, len(word) // 3))
    best, best_d = None, limit + 1
    for c in candidates:
        d = levenshtein(word, c)
        if d < best_d:
            best, best_d = c, d
    return best


def _is_ascii_letter(ch: str) -> bool:
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def _is_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def _is_word_char(ch: str) -> bool:
    return _is_ascii_letter(ch) or _is_digit(ch) or ch == "_"


class Lexer:
    def __init__(self, source: Source, sink: Sink) -> None:
        self.src = source
        self.text = source.text
        self.sink = sink
        self.i = 0
        self.line = 1
        self.col = 1
        self.tokens: list[Token] = []

    # -- cursor helpers --------------------------------------------------------------------
    def peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.text[j] if j < len(self.text) else ""

    def advance(self, n: int = 1) -> str:
        out = self.text[self.i:self.i + n]
        for ch in out:
            if ch == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
        self.i += len(out)
        return out

    def span_from(self, line: int, col: int) -> Span:
        return Span(line, col, self.line, self.col)

    def emit(self, kind: K, text: str, value, line: int, col: int) -> Token:
        tok = Token(kind, text, value, Span(line, col, self.line, self.col))
        self.tokens.append(tok)
        return tok

    def error(self, code: str, message: str, line: int, col: int, text: str,
              hint: str | None = None) -> None:
        span = Span(line, col, self.line, self.col) if self.line == line else \
            Span(line, col, line, col + max(1, len(text)))
        self.sink.add(code, message, span, hint)
        self.tokens.append(Token(K.ERROR, text, None, span))

    # -- main loop ------------------------------------------------------------------------
    def run(self) -> list[Token]:
        if self.src.issue is not None:
            iss = self.src.issue
            self.sink.add("L000", f"file is not valid UTF-8 (byte 0x{iss.byte:02X})",
                          Span.point(iss.line, iss.col), "save the file as UTF-8")
        while self.i < len(self.text):
            ch = self.peek()
            line, col = self.line, self.col
            if ch in " \t\n":
                self.advance()
            elif ch == "�" and self.src.issue is not None:
                self.advance()          # already reported as L000
            elif ch == "/":
                self.lex_slash(line, col)
            elif ch == '"':
                self.lex_string(line, col)
            elif _is_digit(ch) or (ch == "-" and _is_digit(self.peek(1))):
                self.lex_number(line, col)
            elif ch == "-":
                self.advance()
                self.error("L001", "'-' must be immediately followed by a digit", line, col, "-",
                           "write negative numbers like -40 (no space)")
            elif ch == ".":
                self.lex_dot(line, col)
            elif ch in "<>=!":
                self.lex_operator(line, col)
            elif ch in PUNCTUATION:
                self.advance()
                self.emit(K.PUNCT, ch, ch, line, col)
            elif ch == "%":
                self.advance()
                self.emit(K.UNIT, "%", "PCT", line, col)
            elif _is_ascii_letter(ch) or ch == "_":
                self.lex_word(line, col)
            else:
                self.lex_unknown(line, col)
        self.tokens.append(Token(K.EOF, "", None, Span(self.line, self.col, self.line, self.col + 1)))
        return self.tokens

    # -- pieces ---------------------------------------------------------------------------
    def lex_slash(self, line: int, col: int) -> None:
        nxt = self.peek(1)
        if nxt == "/":
            while self.i < len(self.text) and self.peek() != "\n":
                self.advance()
        elif nxt == "*":
            start = self.i
            self.advance(2)
            while self.i < len(self.text) and not (self.peek() == "*" and self.peek(1) == "/"):
                self.advance()
            if self.i < len(self.text):
                self.advance(2)
            self.error("L001", "block comments are not supported", line, col, "/*",
                       "only // comments are supported")
        else:
            self.advance()
            self.error("L001", "unexpected character '/'", line, col, "/",
                       "comments start with //")

    def lex_string(self, line: int, col: int) -> None:
        start = self.i
        self.advance()  # opening quote
        chars: list[str] = []
        bad_escape: tuple[int, int, str] | None = None
        while True:
            ch = self.peek()
            if ch == "" or ch == "\n":
                self.error("L002", "unterminated string", line, col, self.text[start:self.i],
                           "close the string with \" on the same line")
                return
            if ch == '"':
                self.advance()
                break
            if ch == "\\":
                esc_line, esc_col = self.line, self.col
                nxt = self.peek(1)
                if nxt in ('"', "\\"):
                    self.advance(2)
                    chars.append(nxt)
                    continue
                self.advance(2 if nxt not in ("", "\n") else 1)
                if bad_escape is None:
                    bad_escape = (esc_line, esc_col, "\\" + (nxt if nxt not in ("", "\n") else ""))
                continue
            if ord(ch) < 0x20 or ord(ch) == 0x7F:
                if bad_escape is None:
                    bad_escape = (self.line, self.col, ch)
                self.advance()
                continue
            chars.append(ch)
            self.advance()
        text = self.text[start:self.i]
        value = "".join(chars)
        tok = self.emit(K.STRING, text, value, line, col)
        if bad_escape is not None:
            bl, bc, bt = bad_escape
            if bt.startswith("\\"):
                self.sink.add("L004", f"unsupported escape '{bt}' in string",
                              Span(bl, bc, bl, bc + len(bt)), "only \\\" and \\\\ are allowed")
            else:
                self.sink.add("L004", f"control character U+{ord(bt):04X} in string",
                              Span(bl, bc, bl, bc + 1), "strings may contain printable characters only")
        elif not value or len(value) > MAX_NOTIFY or any(not (0x20 <= ord(c) <= 0x7E) for c in value):
            if not value:
                msg, hint = "empty NOTIFY text", "write a short message, e.g. \"Overheat\""
            elif len(value) > MAX_NOTIFY:
                msg, hint = f"NOTIFY text is {len(value)} characters long (max {MAX_NOTIFY})", "shorten the message"
            else:
                bad = next(c for c in value if not (0x20 <= ord(c) <= 0x7E))
                msg, hint = (f"NOTIFY text contains non-ASCII character '{bad}'",
                             "use printable ASCII characters only")
            self.sink.add("L008", msg, tok.span, hint)

    def lex_number(self, line: int, col: int) -> None:
        start = self.i
        if self.peek() == "-":
            self.advance()
        int_start = self.i
        while _is_digit(self.peek()):
            self.advance()
        int_digits = self.i - int_start
        if self.peek() == "." and _is_digit(self.peek(1)):
            self.advance()
            while _is_digit(self.peek()):
                self.advance()
        elif self.peek() == "." and self.peek(1) != ".":
            self.advance()
            text = self.text[start:self.i]
            self.error("L001", f"'{text}' is not a valid number", line, col, text,
                       f"write {text[:-1]} or {text}0")
            return
        num_text = self.text[start:self.i]
        # suffix: maximal word run
        if _is_ascii_letter(self.peek()) or self.peek() == "_":
            suf_start = self.i
            while _is_word_char(self.peek()):
                self.advance()
            suffix = self.text[suf_start:self.i]
            text = self.text[start:self.i]
            self.number_with_suffix(text, num_text, suffix, int_digits, line, col)
            return
        if int_digits > MAX_INT_DIGITS:
            self.error("L003", "number too large", line, col, num_text,
                       f"numbers may have at most {MAX_INT_DIGITS} integer digits")
            return
        self.emit(K.NUMBER, num_text, num_text, line, col)
        if self.peek() == "%":
            pl, pc = self.line, self.col
            self.advance()
            self.emit(K.UNIT, "%", "PCT", pl, pc)

    def number_with_suffix(self, text: str, num_text: str, suffix: str, int_digits: int,
                           line: int, col: int) -> None:
        if suffix in DURATION_MS:
            if num_text.startswith("-"):
                self.error("L003", "durations must be positive", line, col, text,
                           f"write {text[1:]}")
                return
            if int_digits > MAX_INT_DIGITS:
                self.error("L003", "duration too large", line, col, text,
                           "durations are at most 24h")
                return
            ms = Fraction(num_text) * DURATION_MS[suffix]
            if ms.denominator != 1:
                self.error("L003", f"duration {text} is not a whole number of milliseconds", line,
                           col, text, "use a whole number of milliseconds, e.g. 1ms or 1500ms")
                return
            self.emit(K.DURATION, text, int(ms), line, col)
            return
        if suffix[0] in "eE" and len(suffix) > 1 and _is_digit(suffix[1]):
            self.error("L003", f"exponents are not supported in '{text}'", line, col, text,
                       "write the number out in full")
            return
        if num_text in ("0", "-0") and suffix[:1] in "xX":
            self.error("L003", f"hexadecimal numbers are not supported: '{text}'", line, col, text,
                       "write the number in decimal")
            return
        if suffix.startswith("_"):
            self.error("L003", f"digit separators are not supported: '{text}'", line, col, text,
                       "write the number without '_'")
            return
        if suffix in UNIT_SET:
            self.error("L003", f"unit glued to number: '{text}'", line, col, text,
                       f"units need a space: {num_text} {suffix}")
            return
        if suffix in SUFFIX_HINTS:
            self.error("L003", f"bad duration suffix '{suffix}'", line, col, text,
                       f"write {num_text}{SUFFIX_HINTS[suffix]}")
            return
        self.error("L003", f"unexpected letters '{suffix}' after number", line, col, text,
                   "durations end in ms, s, min or h; units need a space")

    def lex_dot(self, line: int, col: int) -> None:
        if self.peek(1) == ".":
            self.advance(2)
            self.emit(K.OP, "..", "..", line, col)
            return
        if _is_digit(self.peek(1)):
            self.advance()
            while _is_digit(self.peek()):
                self.advance()
            text = self.text[self.i - (self.col - col):self.i]
            self.error("L001", f"'{text}' is not a valid number", line, col, text,
                       f"write 0{text}")
            return
        self.advance()
        self.error("L001", "unexpected character '.'", line, col, ".",
                   "ranges are written lo..hi")

    def lex_operator(self, line: int, col: int) -> None:
        ch, nxt = self.peek(), self.peek(1)
        two = ch + nxt
        if two in (">=", "<=", "=="):
            self.advance(2)
            self.emit(K.OP, two, two, line, col)
            return
        if two == "!=":
            self.advance(2)
            self.error("L006", "'!=' is not supported", line, col, "!=",
                       "write two rules (one with <, one with >)")
            return
        if two in ("=>", "=<"):
            self.advance(2)
            fix = ">=" if two == "=>" else "<="
            self.error("L001", f"'{two}' is not an operator", line, col, two, f"use {fix}")
            return
        if ch in "<>":
            self.advance()
            self.emit(K.OP, ch, ch, line, col)
            return
        self.advance()
        if ch == "=":
            prev = self.tokens[-1] if self.tokens else None
            if prev is not None and prev.kind is K.OP and prev.text in "<>" and prev.span.line == line:
                hint = f"write {prev.text}= without a space"
            else:
                hint = "use == to compare"
            self.error("L001", "unexpected character '='", line, col, "=", hint)
            return
        self.error("L001", "unexpected character '!'", line, col, "!", "NOT is not supported")

    def lex_word(self, line: int, col: int) -> None:
        start = self.i
        while _is_word_char(self.peek()):
            self.advance()
        # non-ASCII letters glued to the word: one L001 for the whole run
        if self.peek() and ord(self.peek()) > 127 and self.peek().isalnum():
            while self.peek() and (_is_word_char(self.peek()) or (ord(self.peek()) > 127 and self.peek().isalnum())):
                self.advance()
            text = self.text[start:self.i]
            self.error("L001", f"non-ASCII letters in '{text}'", line, col, text,
                       "identifiers use ASCII letters, digits and '_' only")
            return
        word = self.text[start:self.i]
        letters = [c for c in word if _is_ascii_letter(c)]
        if letters and all(c.islower() for c in letters):
            self.lower_word(word, line, col)
        elif letters and all(c.isupper() for c in letters):
            self.upper_word(word, line, col)
        elif not letters:
            self.error("L007", f"invalid identifier '{word}'", line, col, word,
                       "identifiers start with a lowercase letter")
        else:
            if word.upper() in KEYWORD_SET:
                hint = f"keywords are UPPERCASE: write {word.upper()}"
            elif PIN_RE.match(word.upper()) or word.upper() in UNIT_SET:
                hint = f"write {word.upper()}"
            else:
                hint = f"identifiers are lowercase: write {word.lower()}"
            self.error("L007", f"mixed-case word '{word}'", line, col, word, hint)

    def lower_word(self, word: str, line: int, col: int) -> None:
        if word in AGGREGATES:
            self.emit(K.AGG, word, word, line, col)
            return
        if word.startswith("_") or "__" in word or len(word) > MAX_IDENT:
            if word.startswith("_"):
                why, hint = "starts with '_'", "identifiers start with a lowercase letter"
            elif "__" in word:
                why, hint = "contains '__'", "use a single '_'"
            else:
                why, hint = f"is longer than {MAX_IDENT} characters", "use a shorter name"
            self.error("L007", f"invalid identifier '{word}': {why}", line, col, word, hint)
            return
        self.emit(K.IDENT, word, word, line, col)

    def upper_word(self, word: str, line: int, col: int) -> None:
        if word in KEYWORD_SET:
            self.emit(K.KEYWORD, word, word, line, col)
        elif word in UNIT_SET:
            self.emit(K.UNIT, word, word, line, col)
        elif PIN_RE.match(word):
            self.emit(K.PIN, word, canonical_pin(word), line, col)
        else:
            if word in SPECIAL_WORD_HINTS:
                hint = SPECIAL_WORD_HINTS[word]
            elif "_" in word and PIN_RE.match(word.replace("_", "")):
                hint = f"write {word.replace('_', '')}"
            else:
                guess = did_you_mean(word, KEYWORDS + UNITS + PIN_EXAMPLES)
                hint = f"did you mean {guess}?" if guess else None
            self.error("L005", f"unknown word '{word}'", line, col, word, hint)

    def lex_unknown(self, line: int, col: int) -> None:
        ch = self.peek()
        if ch in ("“", "”", "‘", "’"):
            # smart-quoted text: one diagnostic for the whole run on this line
            start = self.i
            self.advance()
            while self.peek() not in ("", "\n") and self.peek() not in ("“", "”", "‘", "’"):
                self.advance()
            if self.peek() in ("“", "”", "‘", "’"):
                self.advance()
            text = self.text[start:self.i]
            self.error("L001", "typographic quotes are not string delimiters", line, col, text,
                       CONFUSABLES[ch])
            return
        if ch in CONFUSABLES:
            self.advance()
            self.error("L001", f"unexpected character U+{ord(ch):04X}", line, col, ch, CONFUSABLES[ch])
            return
        if ch.isdigit():
            start = self.i
            while self.peek() and self.peek().isdigit():
                self.advance()
            text = self.text[start:self.i]
            self.error("L001", f"non-ASCII digits '{text}'", line, col, text, "use ASCII digits 0-9")
            return
        self.advance()
        shown = f"'{ch}'" if ch.isprintable() and ord(ch) >= 0x20 else f"U+{ord(ch):04X}"
        self.error("L001", f"unknown character {shown}", line, col, ch, None)


def lex(source: Source, sink: Sink) -> list[Token]:
    return Lexer(source, sink).run()
