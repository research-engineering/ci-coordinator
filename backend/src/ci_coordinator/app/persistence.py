from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from ci_coordinator.audit_replay import AuditEventRepository


class UnitOfWork(Protocol):
    @property
    def audit_events(self) -> AuditEventRepository:
        pass

    async def __aenter__(self) -> Self:
        pass

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork:
        pass
