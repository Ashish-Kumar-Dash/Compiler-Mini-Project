"""Bucketed sliding window over integer ADC codes (Architecture §9.5, ADR-14, ADR-17).

This module is NORMATIVE code: the generated C++ (`iotc_gen.h`) mirrors it line for line, and
it is byte-copied into the board App (`python/iotc_runtime/`), so it imports the standard
library only.

Semantics (§9.5): with `B = min(32, W // S)`, tick time `t` falls in bucket `k(t) = t*B // W`.
Ring slot `k % B` holds `(id, sum, count, min, max)`; pushing into a slot whose id differs from
`k` resets it first. The aggregate as of the latest push time `t*` covers the slots with
`id in (k(t*) - B, k(t*)]` and `count > 0`; if there are none the window is empty and every
test on it is false. Before the first push and after `reset()` the window is empty.

Arithmetic: `sum` and `count` are unbounded Python ints here; the C++ mirror stores them as u32
per slot (min/max as u16) and compares averages in 64 bits (§7). The compiler guarantees that
neither overflows (S036).
"""
from __future__ import annotations

MAX_BUCKETS = 32
AVG_OPS = (">", ">=", "<", "<=", "==")


class Window:
    """One window of `window_ms` over ticks every `sample_ms` (§9.5).

    Preconditions of `push` (not checked, as in the C++ mirror): `t` is a non-negative tick time
    in ms, non-decreasing across calls; `code` is a non-negative int."""

    def __init__(self, window_ms: int, sample_ms: int) -> None:
        if window_ms <= 0 or sample_ms <= 0:
            raise ValueError("window_ms and sample_ms must be positive")
        if window_ms < sample_ms:
            raise ValueError("window_ms must be >= sample_ms (S035)")
        self.window_ms = window_ms
        self.sample_ms = sample_ms
        self.buckets = min(MAX_BUCKETS, window_ms // sample_ms)
        self._ids = [0] * self.buckets
        self._sums = [0] * self.buckets
        self._counts = [0] * self.buckets
        self._mins = [0] * self.buckets
        self._maxs = [0] * self.buckets
        self._last_k = 0  # k(t*) of the latest push; unused while every count is 0

    def _bucket_id(self, t: int) -> int:
        """k(t) = floor(t * B / W) (§9.5; 64-bit in C++)."""
        return (t * self.buckets) // self.window_ms

    def reset(self) -> None:
        """Empty the window (§9.4 reset detection, §9.9 scenario start)."""
        for i in range(self.buckets):
            self._ids[i] = 0
            self._sums[i] = 0
            self._counts[i] = 0
            self._mins[i] = 0
            self._maxs[i] = 0
        self._last_k = 0

    def push(self, t: int, code: int) -> None:
        """Add `code` sampled at tick time `t` (§9.3 step 4b, §9.4)."""
        k = self._bucket_id(t)
        i = k % self.buckets
        if self._ids[i] != k or self._counts[i] == 0:
            self._ids[i] = k
            self._sums[i] = code
            self._counts[i] = 1
            self._mins[i] = code
            self._maxs[i] = code
        else:
            self._sums[i] += code
            self._counts[i] += 1
            if code < self._mins[i]:
                self._mins[i] = code
            if code > self._maxs[i]:
                self._maxs[i] = code
        self._last_k = k

    def _live(self, i: int) -> bool:
        """Slot `i` is part of the aggregate as of the latest push (a slot with count 0 is
        unused, so the window is empty before the first push and after reset())."""
        if self._counts[i] == 0:
            return False
        return self._last_k - self.buckets < self._ids[i] <= self._last_k

    def count(self) -> int:
        n = 0
        for i in range(self.buckets):
            if self._live(i):
                n += self._counts[i]
        return n

    def total(self) -> int:
        s = 0
        for i in range(self.buckets):
            if self._live(i):
                s += self._sums[i]
        return s

    def is_empty(self) -> bool:
        return self.count() == 0

    def min_code(self) -> int | None:
        """Smallest code in the window, or None if empty."""
        m: int | None = None
        for i in range(self.buckets):
            if self._live(i) and (m is None or self._mins[i] < m):
                m = self._mins[i]
        return m

    def max_code(self) -> int | None:
        """Largest code in the window, or None if empty."""
        m: int | None = None
        for i in range(self.buckets):
            if self._live(i) and (m is None or self._maxs[i] > m):
                m = self._maxs[i]
        return m

    def avg_test(self, op: str, p: int, q: int) -> bool:
        """Exact `avg OP p/q` as `q*total OP p*count` (§7, ADR-17); requires `q > 0`.
        False if the window is empty."""
        if op not in AVG_OPS:
            raise ValueError(f"unknown avg operator {op!r}")
        if q <= 0:
            raise ValueError("q must be positive")
        n = self.count()
        if n == 0:
            return False
        lhs = q * self.total()
        rhs = p * n
        if op == ">":
            return lhs > rhs
        if op == ">=":
            return lhs >= rhs
        if op == "<":
            return lhs < rhs
        if op == "<=":
            return lhs <= rhs
        return lhs == rhs

    def range_test(self, agg: str, lo: int | None, hi: int | None) -> bool:
        """`lo <= agg <= hi` for agg 'min' or 'max' (§7 code ranges); None = unbounded.
        False if the window is empty."""
        if agg == "min":
            v = self.min_code()
        elif agg == "max":
            v = self.max_code()
        else:
            raise ValueError(f"unknown range aggregate {agg!r}")
        if v is None:
            return False
        if lo is not None and v < lo:
            return False
        if hi is not None and v > hi:
            return False
        return True
