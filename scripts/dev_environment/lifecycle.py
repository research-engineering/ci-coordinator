"""Linearizable lifecycle ownership for one local development instance."""

from __future__ import annotations

import signal
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

from scripts.dev_environment.identity import InstanceIdentity
from scripts.dev_environment.private_files import (
    PrivateLockBusy,
    bounded_private_lock,
    ensure_private_directory,
)


class TerminationRequest(BaseException):
    def __init__(self, signal_number: int) -> None:
        super().__init__(f"termination requested by signal {signal_number}")
        self.signal_number = signal_number


class OperationBusy(ValueError):
    code = "operation_busy"

    def __init__(self) -> None:
        super().__init__(self.code)


class OperationBlocked(ValueError):
    code = "watch_blocked"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class InstanceMutationLease:
    """Borrow the current mutation descriptor; the context remains its owner."""

    root_digest: str
    _descriptor: int | None

    @property
    def inherited_fds(self) -> tuple[int, ...]:
        if self._descriptor is None:
            raise OperationBlocked
        return (self._descriptor,)

    def _expire(self) -> None:
        object.__setattr__(self, "_descriptor", None)


@dataclass(frozen=True, slots=True)
class OperationPaths:
    mutation: Path
    control: Path
    session: Path
    stop: Path
    completed: Path


def operation_paths(identity: InstanceIdentity, *, create: bool = True) -> OperationPaths:
    lock_directory = identity.state_home / "operation-locks"
    if create:
        ensure_private_directory(identity.state_home)
        ensure_private_directory(lock_directory)
    prefix = lock_directory / identity.project_name
    return OperationPaths(
        mutation=prefix.with_suffix(".lock"),
        control=prefix.with_suffix(".control.lock"),
        session=prefix.with_suffix(".watch.json"),
        stop=prefix.with_suffix(".stop.json"),
        completed=prefix.with_suffix(".watch-complete.json"),
    )


@contextmanager
def instance_operation_lock(
    identity: InstanceIdentity,
    *,
    timeout_seconds: float = 0,
) -> Iterator[InstanceMutationLease]:
    paths = operation_paths(identity)
    started = time.monotonic()
    with ExitStack() as mutation:
        try:
            with bounded_private_lock(paths.control, timeout_seconds=timeout_seconds):
                deadline = started + timeout_seconds
                descriptor = mutation.enter_context(
                    bounded_private_lock(
                        paths.mutation,
                        timeout_seconds=max(0, deadline - time.monotonic()),
                    )
                )
                # A durable intent fences abandoned provider effects even if the
                # provider closed inherited descriptors or escaped its group.
                if paths.session.exists() or paths.session.is_symlink():
                    raise OperationBlocked
        except PrivateLockBusy as error:
            raise OperationBusy from error
        lease = InstanceMutationLease(identity.root_digest, descriptor)
        try:
            yield lease
        finally:
            lease._expire()


@contextmanager
def sigterm_guard() -> Iterator[None]:
    previous = signal.getsignal(signal.SIGTERM)
    received = False

    def request_termination(
        signal_number: int,
        _frame: FrameType | None,
    ) -> None:
        nonlocal received
        if received:
            return
        received = True
        raise TerminationRequest(signal_number)

    signal.signal(signal.SIGTERM, request_termination)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
