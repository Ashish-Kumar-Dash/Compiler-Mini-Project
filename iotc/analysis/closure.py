"""Static closures over NEVER pairs (Architecture §8.3, ADR-20).

closure(a := s) is the least set of assignments containing a := s such that, for every NEVER pair
with one side assigned, the other side is assigned the opposite of its forbidden state."""
from __future__ import annotations

from dataclasses import dataclass

from ..ir import Assignment, IRNever, State, other


@dataclass(frozen=True)
class Closure:
    assignments: tuple[Assignment, ...]      # sorted by actuator id
    conflict: int | None                     # actuator forced both ON and OFF, or None

    def as_dict(self) -> dict[int, State]:
        return dict(self.assignments)


def closure(start: list[Assignment] | tuple[Assignment, ...], nevers: tuple[IRNever, ...]) -> Closure:
    assigned: dict[int, State] = {}
    work: list[Assignment] = []
    conflict: int | None = None
    for a, s in start:
        if a in assigned and assigned[a] != s:
            conflict = a if conflict is None else conflict
        elif a not in assigned:
            assigned[a] = s
            work.append((a, s))
    while work:
        x, v = work.pop(0)
        for n in nevers:
            for (p, ps), (q, qs) in (((n.a, n.s1), (n.b, n.s2)), ((n.b, n.s2), (n.a, n.s1))):
                if (p, ps) != (x, v):
                    continue
                need = other(qs)
                have = assigned.get(q)
                if have is None:
                    assigned[q] = need
                    work.append((q, need))
                elif have != need and conflict is None:
                    conflict = q
    return Closure(tuple(sorted(assigned.items())), conflict)
