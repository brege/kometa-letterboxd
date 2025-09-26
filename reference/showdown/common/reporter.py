"""Minimal reporting helpers for console output."""

from __future__ import annotations

from typing import Callable, Optional


class Reporter:
    """Tiny wrapper around a callable for status messages."""

    def __init__(self, emit: Optional[Callable[[str], None]] = None) -> None:
        self._emit = emit or print

    def info(self, message: str) -> None:
        if not self._emit:
            return
        try:
            self._emit(message)
        except BrokenPipeError:
            self._emit = None

    def warn(self, message: str) -> None:
        self.info(message)

    def child(self, prefix: str) -> "Reporter":
        def _wrapped(msg: str) -> None:
            self.info(f"{prefix}{msg}")

        return Reporter(_wrapped)


def null_reporter() -> Reporter:
    return Reporter(lambda _msg: None)
