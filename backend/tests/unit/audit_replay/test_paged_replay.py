from __future__ import annotations

import asyncio
from dataclasses import replace

from ci_coordinator.audit_replay import (
    AuditEventInput,
    AuditEventRecord,
    AuditLedgerSnapshot,
    build_audit_event,
)
from ci_coordinator.audit_replay.paged_replay import (
    iter_verified_replay_events,
    scan_audit_replay,
)
from ci_coordinator.audit_replay.replay import (
    AllAuditReplayFilter,
    SubjectAuditReplayFilter,
)


def test_scan_verifies_bounded_pages_before_reporting_a_missing_filter() -> None:
    first, second = _events(2)
    repository = _Repository((first, replace(second, payload_hash="0" * 64)))

    scan = asyncio.run(
        scan_audit_replay(
            repository,
            SubjectAuditReplayFilter(
                subject_type="dynamic-ci-plan",
                subject_id="missing",
            ),
            page_size=1,
        )
    )

    assert scan.report.status == "invalid-ledger"
    assert scan.report.ledger.reason == "payload hash does not match payload"
    assert repository.page_requests == [(0, 2, 1), (1, 2, 1)]


def test_scan_rejects_rows_beyond_the_frozen_ledger_head() -> None:
    first, second = _events(2)
    repository = _Repository(
        (first, second),
        snapshot=AuditLedgerSnapshot(
            last_sequence=first.sequence,
            last_event_hash=first.event_hash,
            maximum_sequence=second.sequence,
        ),
    )

    scan = asyncio.run(scan_audit_replay(repository, AllAuditReplayFilter()))

    assert scan.report.status == "invalid-ledger"
    assert (
        scan.report.ledger.reason == "audit ledger head does not match the maximum stored sequence"
    )
    assert repository.page_requests == []


def test_output_pass_is_bound_to_the_verified_head_when_new_events_exist() -> None:
    first, second = _events(2)
    frozen = AuditLedgerSnapshot(
        last_sequence=first.sequence,
        last_event_hash=first.event_hash,
        maximum_sequence=first.sequence,
    )
    repository = _Repository((first, second), snapshot=frozen)

    async def scenario() -> tuple[str, ...]:
        scan = await scan_audit_replay(repository, AllAuditReplayFilter(), page_size=1)
        return tuple(
            [
                event.audit_event_id
                async for event in iter_verified_replay_events(
                    repository,
                    scan,
                    include_payload=False,
                    page_size=1,
                )
            ]
        )

    emitted = asyncio.run(scenario())

    assert emitted == (first.audit_event_id,)
    assert repository.page_requests == [(0, 1, 1), (0, 1, 1)]


class _Repository:
    def __init__(
        self,
        records: tuple[AuditEventRecord, ...],
        *,
        snapshot: AuditLedgerSnapshot | None = None,
    ) -> None:
        self._records = records
        last = records[-1] if records else None
        self._snapshot = snapshot or AuditLedgerSnapshot(
            last_sequence=0 if last is None else last.sequence,
            last_event_hash=None if last is None else last.event_hash,
            maximum_sequence=None if last is None else last.sequence,
        )
        self.page_requests: list[tuple[int, int, int]] = []

    async def snapshot(self) -> AuditLedgerSnapshot:
        return self._snapshot

    async def load_page(
        self,
        *,
        after_sequence: int,
        through_sequence: int,
        limit: int,
    ) -> tuple[AuditEventRecord, ...]:
        self.page_requests.append((after_sequence, through_sequence, limit))
        return tuple(
            event for event in self._records if after_sequence < event.sequence <= through_sequence
        )[:limit]


def _events(count: int) -> tuple[AuditEventRecord, ...]:
    events: list[AuditEventRecord] = []
    previous: AuditEventRecord | None = None
    for index in range(count):
        event = build_audit_event(
            AuditEventInput(
                idempotency_key=f"paged-replay-{index}",
                subject_type="dynamic-ci-plan",
                subject_id="plan-1",
                event_type="dynamic-ci-plan.verified",
                created_at="2026-07-15T00:00:00.000Z",
                actor="test-suite",
                payload={"index": index},
            ),
            previous,
        )
        events.append(event)
        previous = event
    return tuple(events)
