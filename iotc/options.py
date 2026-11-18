"""Compilation options shared by check/build/run (Architecture §14)."""
from __future__ import annotations

from dataclasses import dataclass

from .backend.profiles import Profile, get_profile


@dataclass(frozen=True)
class Options:
    board: str = "unoq"
    target: str = "unoq"                 # "unoq" | "mcu-only"
    sample_ms: int = 100
    heartbeat_ms: int = 500
    failsafe_timeout_ms: int = 2000
    place_windows: str = "linux"         # "linux" | "mcu"

    @property
    def profile(self) -> Profile:
        return get_profile(self.board)

    @staticmethod
    def make(board: str = "unoq", target: str | None = None, **kw) -> "Options":
        if target is None:
            target = "unoq" if board == "unoq" else "mcu-only"
        if board == "uno" and target == "unoq":
            raise ValueError("--target unoq needs --board unoq (the classic Uno has no Linux side)")
        for key in ("sample_ms", "heartbeat_ms", "failsafe_timeout_ms"):
            if key in kw and (not isinstance(kw[key], int) or kw[key] <= 0):
                raise ValueError(f"--{key.replace('_', '-')} must be a positive integer")
        return Options(board=board, target=target, **kw)
