from __future__ import annotations

import signal
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import FrameType


@dataclass(slots=True)
class DiagramCancellation:
    signal_number: int | None = None

    def requested(self) -> bool:
        return self.signal_number is not None

    def receive(self, signal_number: int, _frame: FrameType | None) -> None:
        if self.signal_number is None:
            self.signal_number = signal_number

    def exit_code(self, status: int) -> int:
        return status if self.signal_number is None else 128 + self.signal_number


@contextmanager
def cancellation_signals() -> Iterator[DiagramCancellation]:
    cancellation = DiagramCancellation()
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        for number in previous:
            signal.signal(number, cancellation.receive)
        yield cancellation
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
