from __future__ import annotations

from collections.abc import AsyncIterator
from typing import NoReturn

from ci_coordinator.audit_replay.chain import (
    InvalidAuditChain,
    normalize_and_verify_audit_chain_extension,
)
from ci_coordinator.audit_replay.event import AuditEventRecord, AuditJsonResourceFailure
from ci_coordinator.audit_replay.ports import (
    AUDIT_REPLAY_PAGE_SIZE,
    AuditLedgerSnapshot,
    AuditReplayRepository,
)
from ci_coordinator.audit_replay.replay import (
    AllAuditReplayFilter,
    AuditReplayEventSummary,
    AuditReplayFilter,
    AuditReplayResult,
    InvalidAuditReplayLedgerSummary,
    InvalidLedgerAuditReplayReport,
    NotFoundAuditReplayReport,
    ValidAuditReplayLedgerSummary,
    ValidAuditReplayReport,
    audit_event_matches_filter,
    summarize_audit_event,
)


class AuditReplaySnapshotChanged(RuntimeError):
    """Raised when the output pass cannot reproduce its verified ledger epoch."""


class AuditReplayScan:
    __last_event: AuditEventRecord | None
    __matched_event_count: int
    __report: AuditReplayResult
    __snapshot: AuditLedgerSnapshot

    __slots__ = ("__last_event", "__matched_event_count", "__report", "__snapshot")

    def __init__(
        self,
        token: object,
        *,
        snapshot: AuditLedgerSnapshot,
        report: AuditReplayResult,
        matched_event_count: int,
        last_event: AuditEventRecord | None,
    ) -> None:
        if token is not _SCAN_TOKEN:
            raise TypeError("AuditReplayScan cannot be constructed directly")
        object.__setattr__(self, "_AuditReplayScan__snapshot", snapshot)
        object.__setattr__(self, "_AuditReplayScan__report", report)
        object.__setattr__(self, "_AuditReplayScan__matched_event_count", matched_event_count)
        object.__setattr__(self, "_AuditReplayScan__last_event", last_event)

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("AuditReplayScan is immutable")

    def __reduce__(self) -> NoReturn:
        raise TypeError("AuditReplayScan cannot be serialized")

    @property
    def snapshot(self) -> AuditLedgerSnapshot:
        return self.__snapshot

    @property
    def report(self) -> AuditReplayResult:
        return self.__report

    @property
    def matched_event_count(self) -> int:
        return self.__matched_event_count

    @property
    def last_event(self) -> AuditEventRecord | None:
        return self.__last_event


_SCAN_TOKEN = object()


async def scan_audit_replay(
    repository: AuditReplayRepository,
    replay_filter: AuditReplayFilter,
    *,
    page_size: int = AUDIT_REPLAY_PAGE_SIZE,
) -> AuditReplayScan:
    """Verify one frozen ledger epoch while retaining only one bounded page."""

    _require_page_size(page_size)
    snapshot = await repository.snapshot()
    inconsistent_head = _snapshot_inconsistency(snapshot)
    if inconsistent_head is not None:
        return _invalid_scan(snapshot, replay_filter, inconsistent_head)

    previous: AuditEventRecord | None = None
    matched_event_count = 0
    while _sequence(previous) < snapshot.last_sequence:
        normalized, invalid = await _load_verified_page(
            repository,
            snapshot=snapshot,
            previous=previous,
            page_size=page_size,
        )
        if invalid is not None:
            return _invalid_scan(
                snapshot,
                replay_filter,
                invalid.reason,
                audit_event_id=invalid.audit_event_id,
                resource_failure=invalid.resource_failure,
            )
        if not normalized:
            return _invalid_scan(
                snapshot,
                replay_filter,
                "audit ledger ended before the frozen head sequence",
            )
        matched_event_count += sum(
            audit_event_matches_filter(event, replay_filter) for event in normalized
        )
        previous = normalized[-1]

    if _event_hash(previous) != snapshot.last_event_hash:
        return _invalid_scan(
            snapshot,
            replay_filter,
            "audit ledger head hash does not match the verified chain",
        )

    ledger = ValidAuditReplayLedgerSummary(
        total_events=snapshot.last_sequence,
        last_event_hash=snapshot.last_event_hash,
    )
    report: AuditReplayResult
    if not isinstance(replay_filter, AllAuditReplayFilter) and matched_event_count == 0:
        report = NotFoundAuditReplayReport(
            ledger=ledger,
            replay_filter=replay_filter,
        )
    else:
        report = ValidAuditReplayReport(
            ledger=ledger,
            replay_filter=replay_filter,
            events=(),
        )
    return AuditReplayScan(
        _SCAN_TOKEN,
        snapshot=snapshot,
        report=report,
        matched_event_count=matched_event_count,
        last_event=previous,
    )


async def iter_verified_replay_events(
    repository: AuditReplayRepository,
    scan: AuditReplayScan,
    *,
    include_payload: bool,
    page_size: int = AUDIT_REPLAY_PAGE_SIZE,
) -> AsyncIterator[AuditReplayEventSummary]:
    """Replay the exact verified epoch without accumulating its output projection."""

    _require_page_size(page_size)
    if not isinstance(scan.report, ValidAuditReplayReport):
        raise ValueError("only a valid audit replay scan can be streamed")

    previous: AuditEventRecord | None = None
    emitted = 0
    while _sequence(previous) < scan.snapshot.last_sequence:
        normalized, invalid = await _load_verified_page(
            repository,
            snapshot=scan.snapshot,
            previous=previous,
            page_size=page_size,
        )
        if invalid is not None or not normalized:
            raise AuditReplaySnapshotChanged(
                "verified audit ledger epoch changed before replay output completed"
            )
        for event in normalized:
            if audit_event_matches_filter(event, scan.report.replay_filter):
                emitted += 1
                yield summarize_audit_event(event, include_payload=include_payload)
        previous = normalized[-1]

    if (
        _event_hash(previous) != scan.snapshot.last_event_hash
        or emitted != scan.matched_event_count
    ):
        raise AuditReplaySnapshotChanged(
            "verified audit ledger epoch changed before replay output completed"
        )


async def _load_verified_page(
    repository: AuditReplayRepository,
    *,
    snapshot: AuditLedgerSnapshot,
    previous: AuditEventRecord | None,
    page_size: int,
) -> tuple[tuple[AuditEventRecord, ...], InvalidAuditChain | None]:
    page = await repository.load_page(
        after_sequence=_sequence(previous),
        through_sequence=snapshot.last_sequence,
        limit=page_size,
    )
    if len(page) > page_size:
        return (), InvalidAuditChain(
            reason="audit repository returned an oversized replay page",
            audit_event_id=None,
        )
    normalized, verification = normalize_and_verify_audit_chain_extension(previous, page)
    if isinstance(verification, InvalidAuditChain):
        return normalized, verification
    if normalized and normalized[-1].sequence > snapshot.last_sequence:
        return normalized, InvalidAuditChain(
            reason="audit repository returned an event beyond the frozen head",
            audit_event_id=normalized[-1].audit_event_id,
        )
    return normalized, None


def _snapshot_inconsistency(snapshot: AuditLedgerSnapshot) -> str | None:
    expected_maximum = snapshot.last_sequence or None
    if snapshot.maximum_sequence != expected_maximum:
        return "audit ledger head does not match the maximum stored sequence"
    return None


def _invalid_scan(
    snapshot: AuditLedgerSnapshot,
    replay_filter: AuditReplayFilter,
    reason: str,
    *,
    audit_event_id: str | None = None,
    resource_failure: AuditJsonResourceFailure | None = None,
) -> AuditReplayScan:
    return AuditReplayScan(
        _SCAN_TOKEN,
        snapshot=snapshot,
        report=InvalidLedgerAuditReplayReport(
            ledger=InvalidAuditReplayLedgerSummary(
                total_events=snapshot.last_sequence,
                reason=reason,
                audit_event_id=audit_event_id,
                resource_failure=resource_failure,
            ),
            replay_filter=replay_filter,
        ),
        matched_event_count=0,
        last_event=None,
    )


def _require_page_size(value: int) -> None:
    if type(value) is not int or not 1 <= value <= AUDIT_REPLAY_PAGE_SIZE:
        raise ValueError(f"audit replay page size must be between 1 and {AUDIT_REPLAY_PAGE_SIZE}")


def _sequence(record: AuditEventRecord | None) -> int:
    return 0 if record is None else record.sequence


def _event_hash(record: AuditEventRecord | None) -> str | None:
    return None if record is None else record.event_hash
