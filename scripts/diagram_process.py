from __future__ import annotations

import signal
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import FrameType

_DEFERRED_INTERRUPT: ContextVar[list[KeyboardInterrupt] | None] = ContextVar(
    "managed_acquisition_interrupt", default=None
)
_INTERRUPTIBLE: ContextVar[bool] = ContextVar("managed_interruptible", default=True)


@contextmanager
def defer_cancellation() -> Iterator[None]:
    """Latch owned interrupts until a returned child has a cleanup owner."""
    pending = _DEFERRED_INTERRUPT.get()
    token = None
    if pending is None:
        pending = []
        token = _DEFERRED_INTERRUPT.set(pending)
    mode_token = _INTERRUPTIBLE.set(False)
    primary: BaseException | None = None
    try:
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        _INTERRUPTIBLE.reset(mode_token)
        if token is not None:
            _DEFERRED_INTERRUPT.reset(token)
        if token is not None and pending:
            if primary is None:
                raise pending[0]
            primary.add_note("Managed cancellation was received during owned cleanup")


@contextmanager
def interruptible_cancellation() -> Iterator[None]:
    pending = _DEFERRED_INTERRUPT.get()
    token = _INTERRUPTIBLE.set(True)
    primary: BaseException | None = None
    try:
        if pending:
            raise pending[0]
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        _INTERRUPTIBLE.reset(token)
        if primary is None and pending:
            raise pending[0]


@dataclass(slots=True)
class DiagramCancellation:
    signal_number: int | None = None
    interrupt: bool = False
    received_at: float | None = None

    def requested(self) -> bool:
        return self.signal_number is not None

    def receive(self, signal_number: int, _frame: FrameType | None) -> None:
        if self.signal_number is None:
            self.signal_number = signal_number
            self.received_at = time.monotonic()
            if self.interrupt:
                error = KeyboardInterrupt()
                pending = _DEFERRED_INTERRUPT.get()
                if pending is None:
                    raise error
                pending.append(error)
                if _INTERRUPTIBLE.get() and sys.exception() is None:
                    raise error

    def exit_code(self, status: int) -> int:
        return status if self.signal_number is None else 128 + self.signal_number


@contextmanager
def cancellation_signals(*, interrupt: bool = False) -> Iterator[DiagramCancellation]:
    cancellation = DiagramCancellation(interrupt=interrupt)
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        for number in previous:
            signal.signal(number, cancellation.receive)
        yield cancellation
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
