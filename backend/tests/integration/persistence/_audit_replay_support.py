from __future__ import annotations

from ci_coordinator.audit_replay import (
    AUDIT_REPLAY_PAGE_SIZE,
    AuditEventRecord,
    AuditReplayRepository,
)


async def load_test_audit_records(
    repository: AuditReplayRepository,
) -> tuple[AuditEventRecord, ...]:
    """Materialize only the finite ledger owned by one integration-test fixture."""

    snapshot = await repository.snapshot()
    records: list[AuditEventRecord] = []
    after_sequence = 0
    while after_sequence < snapshot.last_sequence:
        page = await repository.load_page(
            after_sequence=after_sequence,
            through_sequence=snapshot.last_sequence,
            limit=AUDIT_REPLAY_PAGE_SIZE,
        )
        if not page:
            raise AssertionError("test audit ledger ended before its frozen head")
        records.extend(page)
        after_sequence = page[-1].sequence
    return tuple(records)
