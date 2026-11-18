"""Recursive-descent LL(1) parser with panic-mode recovery (Architecture §4.3, §5.2).

* ERROR tokens (already reported by the lexer) put the parser into panic mode silently.
* A syntax error records one P001 "expected X, found Y" with a hint where one helps, then skips
  to the sync set {';' (consumed), SENSOR, ACTUATOR, RULE, SAFETY, FAILSAFE, EOF}.
* Progress is guaranteed: a sync that would stop where the previous one did consumes a token.
"""
from __future__ import annotations

from . import ast as A
from .diagnostics import Sink
from .source import Span
from .tokens import DECL_START, PIN_RE, RELOPS, UNIT_SET, K, Token, canonical_pin, describe

DECL_LIST = "a declaration (SENSOR, ACTUATOR, RULE, SAFETY or FAILSAFE)"
DURATION_SUFFIXES = {"ms", "s", "min", "h"}


class _Panic(Exception):
    pass


class Parser:
    def __init__(self, tokens: list[Token], sink: Sink) -> None:
        self.toks = tokens
        self.sink = sink
        self.pos = 0
        self.last_sync_pos = -1

    # -- token helpers ------------------------------------------------------------------------
    @property
    def tok(self) -> Token:
        return self.toks[self.pos]

    def peek(self, k: int = 1) -> Token:
        j = min(self.pos + k, len(self.toks) - 1)
        return self.toks[j]

    def advance(self) -> Token:
        t = self.toks[self.pos]
        if t.kind is not K.EOF:
            self.pos += 1
        return t

    def fail(self, expected: str, hint: str | None = None, message: str | None = None) -> "_Panic":
        """Record P001 at the current token (unless it is a lexer ERROR) and return a panic."""
        t = self.tok
        if t.kind is not K.ERROR:
            self.sink.add("P001", message or f"expected {expected}, found {describe(t)}", t.span, hint)
        return _Panic()

    def expect_kw(self, word: str, hint: str | None = None) -> Token:
        t = self.tok
        if t.is_kw(word):
            return self.advance()
        if hint is None and t.kind is K.IDENT and t.text.upper() == word:
            hint = f"keywords are UPPERCASE: write {word}"
        raise self.fail(f"`{word}`", hint)

    def expect_punct(self, ch: str, hint: str | None = None) -> Token:
        t = self.tok
        if t.is_op(ch):
            return self.advance()
        if hint is None and ch == ";" and t.kind is K.KEYWORD and t.text in DECL_START:
            hint = "end each declaration with ';'"
        prev = self.toks[self.pos - 1] if self.pos > 0 else None
        if (ch == ";" and prev is not None and t.kind is not K.ERROR
                and t.span.line > prev.span.end_line):
            # point where the ';' belongs (just after the previous token), not at the next line
            where = Span.point(prev.span.end_line, prev.span.end_col)
            self.sink.add("P001", f"expected ';', found {describe(t)} on the next line", where,
                          hint or "end each declaration with ';'")
            raise _Panic()
        raise self.fail(f"'{ch}'", hint)

    def expect_ident(self, what: str = "a name") -> A.Name:
        t = self.tok
        if t.kind is K.IDENT:
            self.advance()
            return A.Name(t.text, t.span)
        hint = None
        if t.kind is K.AGG:
            hint = f"'{t.text}' is reserved (an aggregate); choose another name"
        elif t.kind is K.KEYWORD:
            hint = f"'{t.text}' is a keyword; names are lowercase"
        raise self.fail(what, hint)

    def expect_state(self) -> str:
        t = self.tok
        if t.is_kw("ON", "OFF"):
            self.advance()
            return t.text
        hint = None
        if t.kind is K.IDENT and t.text.upper() in ("ON", "OFF"):
            hint = f"keywords are UPPERCASE: write {t.text.upper()}"
        elif t.kind is K.IDENT and t.text in ("high", "low", "true", "false", "open", "closed"):
            hint = "actuator states are ON or OFF"
        raise self.fail("`ON` or `OFF`", hint)

    def expect_duration(self, what: str = "a duration") -> Token:
        t = self.tok
        if t.kind is K.DURATION:
            return self.advance()
        hint = None
        nxt = self.peek()
        if t.kind is K.NUMBER and nxt.kind in (K.IDENT, K.AGG) and nxt.text in DURATION_SUFFIXES:
            hint = f"write {t.text}{nxt.text} without a space"
        elif t.kind is K.NUMBER:
            hint = f"add a unit: {t.text}ms, {t.text}s, {t.text}min or {t.text}h"
        raise self.fail(what, hint)

    # -- recovery -----------------------------------------------------------------------------
    def sync(self) -> None:
        if self.pos == self.last_sync_pos and self.tok.kind is not K.EOF:
            self.advance()
        while True:
            t = self.tok
            if t.kind is K.EOF:
                break
            if t.is_op(";"):
                self.advance()
                break
            if t.kind is K.KEYWORD and t.text in DECL_START:
                break
            self.advance()
        self.last_sync_pos = self.pos

    # -- grammar ------------------------------------------------------------------------------
    def parse_program(self) -> A.Program:
        sensors: list[A.SensorDecl] = []
        actuators: list[A.ActuatorDecl] = []
        rules: list[A.Rule] = []
        safeties: list[A.Safety] = []
        failsafes: list[A.Failsafe] = []
        while self.tok.kind is not K.EOF:
            t = self.tok
            try:
                if t.kind is K.ERROR:
                    raise _Panic()
                if t.is_kw("SENSOR"):
                    sensors.append(self.sensor())
                elif t.is_kw("ACTUATOR"):
                    actuators.append(self.actuator())
                elif t.is_kw("RULE"):
                    rules.append(self.rule())
                elif t.is_kw("SAFETY"):
                    safeties.append(self.safety())
                elif t.is_kw("FAILSAFE"):
                    failsafes.append(self.failsafe())
                else:
                    hint = None
                    if t.kind is K.IDENT and t.text.upper() in DECL_START:
                        hint = f"keywords are UPPERCASE: write {t.text.upper()}"
                    raise self.fail(DECL_LIST, hint)
            except _Panic:
                self.sync()
        return A.Program(tuple(sensors), tuple(actuators), tuple(rules), tuple(safeties),
                         tuple(failsafes))

    def end(self, start: Token) -> Span:
        semi = self.expect_punct(";")
        return start.span.to(semi.span)

    def pin(self) -> A.Name:
        t = self.tok
        if t.kind is K.PIN:
            self.advance()
            return A.Name(canonical_pin(t.text), t.span)
        hint = None
        if t.kind is K.IDENT and PIN_RE.match(t.text.upper()):
            hint = f"pins are UPPERCASE: write {t.text.upper()}"
        raise self.fail("a pin (A0-A5, D0-D13, LED3, LED4, ...)", hint)

    def sensor(self) -> A.SensorDecl:
        start = self.advance()  # SENSOR
        name = self.expect_ident("a sensor name")
        self.expect_kw("AT")
        pin = self.pin()
        t = self.tok
        if t.is_kw("DIGITAL"):
            self.advance()
            if self.tok.is_kw("UNIT", "RANGE"):
                raise self.fail("';'", "DIGITAL sensors take no UNIT or RANGE")
            span = self.end(start)
            return A.SensorDecl(name, pin, "DIGITAL", None, None, None, span)
        if not t.is_kw("ANALOG"):
            hint = None
            if t.is_kw("UNIT", "RANGE"):
                hint = "write the kind first: ANALOG UNIT C RANGE lo..hi, or DIGITAL"
            raise self.fail("`ANALOG` or `DIGITAL`", hint)
        self.advance()
        if self.tok.is_kw("RANGE"):
            raise self.fail("`UNIT`", "write UNIT before RANGE: ANALOG UNIT C RANGE lo..hi")
        self.expect_kw("UNIT")
        unit = self.unit_required()
        self.expect_kw("RANGE")
        lo = self.number("the lower RANGE bound")
        if not self.tok.is_op(".."):
            raise self.fail("'..'", "ranges are written lo..hi, e.g. RANGE -40..125")
        self.advance()
        hi = self.number("the upper RANGE bound")
        span = self.end(start)
        return A.SensorDecl(name, pin, "ANALOG", unit, lo, hi, span)

    def unit_required(self) -> A.Name:
        t = self.tok
        if t.kind is K.UNIT:
            self.advance()
            return A.Name(t.value, t.span)
        hint = None
        if t.kind is K.IDENT and t.text.upper() in UNIT_SET:
            hint = f"units are UPPERCASE: write {t.text.upper()}"
        raise self.fail("a unit (C, F, PCT, LUX, CM)", hint)

    def number(self, what: str = "a number") -> A.Name:
        t = self.tok
        if t.kind is K.NUMBER:
            self.advance()
            return A.Name(t.value, t.span)
        hint = None
        if t.kind is K.DURATION:
            hint = "durations are not allowed here"
        raise self.fail(what, hint)

    def actuator(self) -> A.ActuatorDecl:
        start = self.advance()
        name = self.expect_ident("an actuator name")
        self.expect_kw("AT")
        pin = self.pin()
        return A.ActuatorDecl(name, pin, self.end(start))

    def rule(self) -> A.Rule:
        start = self.advance()
        name = self.expect_ident("a rule name")
        deadline = deadline_span = None
        if self.tok.is_kw("REALTIME"):
            self.advance()
            d = self.expect_duration("a REALTIME deadline (e.g. 10ms)")
            deadline, deadline_span = d.value, d.span
        if not self.tok.is_op(":"):
            hint = None
            if self.tok.is_kw("WHEN"):
                hint = f"write RULE {name.text}: WHEN ..."
            raise self.fail("':'", hint)
        self.advance()
        self.expect_kw("WHEN")
        guard = self.cond()
        hold = hold_span = None
        if self.tok.is_kw("FOR"):
            self.advance()
            d = self.expect_duration("a FOR duration (e.g. 10s)")
            hold, hold_span = d.value, d.span
        self.expect_kw("THEN")
        action = self.action()
        if self.tok.is_op(","):
            raise self.fail("';'", "one action per rule: write two rules")
        span = self.end(start)
        return A.Rule(name, deadline, deadline_span, guard, hold, hold_span, action, span)

    def cond(self) -> tuple[A.Cmp, ...]:
        cmps = [self.cmp()]
        while self.tok.is_kw("AND"):
            self.advance()
            if self.tok.is_kw("THEN", "FOR"):
                raise self.fail("a comparison after AND")
            cmps.append(self.cmp())
        return tuple(cmps)

    def value(self) -> A.Value:
        t = self.tok
        if t.kind is K.IDENT:
            self.advance()
            return A.SensorRef(A.Name(t.text, t.span), t.span)
        if t.kind is K.AGG:
            self.advance()
            self.expect_punct("(", f"write {t.text}(sensor, 10s)")
            sensor = self.expect_ident("a sensor name")
            self.expect_punct(",", f"write {t.text}({sensor.text}, 10s)")
            d = self.expect_duration("a window duration (e.g. 10s)")
            close = self.expect_punct(")")
            return A.Agg(t.text, sensor, d.value, d.span, t.span.to(close.span))
        hint = None
        if t.kind is K.NUMBER and self.peek().kind is K.OP and self.peek().text in RELOPS \
                and self.peek(2).kind is K.IDENT:
            flip = {">": "<", "<": ">", ">=": "<=", "<=": ">=", "==": "=="}[self.peek().text]
            hint = f"write the sensor first: {self.peek(2).text} {flip} {t.text}"
        raise self.fail("a sensor name or avg/min/max(...)", hint)

    def cmp(self) -> A.Cmp:
        value = self.value()
        t = self.tok
        if not (t.kind is K.OP and t.text in RELOPS):
            raise self.fail("a comparison operator (>, <, >=, <=, ==)")
        op = self.advance()
        number = self.number("a number")
        unit = None
        if self.tok.kind is K.UNIT:
            u = self.advance()
            unit = A.Name(u.value, u.span)
        end_span = unit.span if unit else number.span
        return A.Cmp(value, op.text, op.span, number, unit, value.span.to(end_span))

    def action(self) -> A.Action:
        t = self.tok
        if t.is_kw("NOTIFY"):
            self.advance()
            s = self.tok
            if s.kind is not K.STRING:
                raise self.fail("a string after NOTIFY", 'write NOTIFY "message"')
            self.advance()
            return A.Notify(s.value, t.span.to(s.span))
        if t.kind is K.IDENT:
            name = A.Name(t.text, t.span)
            self.advance()
            state = self.expect_state()
            return A.SetState(name, state, t.span.to(self.toks[self.pos - 1].span))
        raise self.fail("an actuator name or NOTIFY")

    def safety(self) -> A.Safety:
        start = self.advance()
        t = self.tok
        if t.is_kw("NEVER"):
            self.advance()
            a = self.expect_ident("an actuator name")
            s1 = self.expect_state()
            self.expect_kw("AND")
            b = self.expect_ident("an actuator name")
            s2 = self.expect_state()
            return A.Never(a, s1, b, s2, self.end(start))
        if t.is_kw("ENSURE"):
            self.advance()
            a = self.expect_ident("an actuator name")
            s = self.expect_state()
            self.expect_kw("WHEN")
            guard = self.cond()
            if self.tok.is_kw("FOR"):
                raise self.fail("';'", "FOR is not allowed in ENSURE (it is checked every pass)")
            return A.Ensure(a, s, guard, self.end(start))
        hint = None
        if t.kind is K.IDENT and t.text.upper() in ("NEVER", "ENSURE"):
            hint = f"keywords are UPPERCASE: write {t.text.upper()}"
        raise self.fail("`NEVER` or `ENSURE`", hint)

    def failsafe(self) -> A.Failsafe:
        start = self.advance()
        a = self.expect_ident("an actuator name")
        s = self.expect_state()
        return A.Failsafe(a, s, self.end(start))


def parse(tokens: list[Token], sink: Sink) -> A.Program:
    return Parser(tokens, sink).parse_program()
