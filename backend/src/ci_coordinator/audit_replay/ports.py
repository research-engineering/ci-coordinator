from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

from ci_coordinator.audit_replay.event import (
    AuditEventRecord,
    PreparedAuditEvent,
    is_hash,
)
from ci_coordinator.audit_replay.recorder import AuditAppendResult
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

AUDIT_REPLAY_PAGE_SIZE: Final = 16


@dataclass(frozen=True, slots=True)
class AuditLedgerSnapshot:
    last_sequence: int
    last_event_hash: str | None
    maximum_sequence: int | None

    def __post_init__(self) -> None:
        if (
            type(self.last_sequence) is not int
            or not 0 <= self.last_sequence <= MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError("audit snapshot sequence is outside its admitted interval")
        if self.last_sequence == 0:
            if self.last_event_hash is not None:
                raise ValueError("empty audit snapshot cannot have a head hash")
        elif not is_hash(self.last_event_hash):
            raise ValueError("non-empty audit snapshot requires a valid head hash")
        if self.maximum_sequence is not None and (
            type(self.maximum_sequence) is not int
            or not 1 <= self.maximum_sequence <= MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError("audit snapshot maximum sequence is outside its admitted interval")


class AuditReplayRepository(Protocol):
    """Read immutable pages from a ledger with storage-enforced unique idempotency keys."""

    async def snapshot(self) -> AuditLedgerSnapshot:
        pass

    async def load_page(
        self,
        *,
        after_sequence: int,
        through_sequence: int,
        limit: int,
    ) -> tuple[AuditEventRecord, ...]:
        pass


class AuditEventRepository(AuditReplayRepository, Protocol):
    async def append(self, event: PreparedAuditEvent) -> AuditAppendResult:
        pass
