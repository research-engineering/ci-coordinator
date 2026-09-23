from typing import cast

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import AuditAppendAppended, AuditEventInput, prepare_audit_event
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.ci_economics.history_gap import HistoryRecheckGap
from ci_coordinator.ci_economics.history_gap_recovery import (
    HISTORY_GAPS_REQUEUED_EVENT_TYPE,
    HistoryGapRepairInterval,
    HistoryGapRepairOutcome,
    HistoryGapRepairReceipt,
    HistoryGapRepairResult,
    RepairHistoryGaps,
    gap_repair_hints,
)
from ci_coordinator.persistence._schema_ci_history_control import ci_history_gaps
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.canonical_row import (
    decode_canonical_object,
    encode_canonical_object,
    require_bytes,
)
from ci_coordinator.persistence.ci_history_gap_store import MAX_HISTORY_GAP_BYTES
from ci_coordinator.persistence.ci_history_recheck_rows import admit_history_recheck
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    history_scope_predicate,
    load_history_dataset,
    lock_history_scope,
)

_GAP_SOURCES = TypeAdapter(tuple[HistoryRecheckGap, ...])
_MAX_RECEIPT_BYTES = 16_384
_MAX_SOURCE_BYTES = 50 * MAX_HISTORY_GAP_BYTES


def _receipt_for(
    command: RepairHistoryGaps, gaps: tuple[HistoryRecheckGap, ...]
) -> HistoryGapRepairReceipt:
    hints = gap_repair_hints(command, gaps)
    if isinstance(hints, str):
        raise ValueError("stored repair receipt has inadmissible source intervals")
    return HistoryGapRepairReceipt(
        request=command.request,
        intervals=tuple(
            HistoryGapRepairInterval(
                workflowRunId=hint.cursor.workflow_run_id,
                fromAttempt=hint.cursor.next_attempt,
                throughAttempt=hint.cursor.latest_attempt,
            )
            for hint in hints
        ),
    )


async def repair_history_gaps(
    connection: AsyncConnection, audit: _PostgresAuditEventRepository, command: RepairHistoryGaps
) -> HistoryGapRepairResult:
    command = RepairHistoryGaps.model_validate(command)

    def rejected(outcome: HistoryGapRepairOutcome) -> HistoryGapRepairResult:
        return HistoryGapRepairResult(outcome=outcome, operationId=command.operation_id)

    await lock_history_scope(connection, command.scope)
    dataset = await load_history_dataset(connection, command.scope, locked=True)
    existing = await audit._find_pair_owned(
        command.audit_key, HISTORY_GAPS_REQUEUED_EVENT_TYPE, command.scope
    )
    if existing is not None:
        payload = existing.payload
        if type(payload) is not dict or set(payload) != {"commandDigest", "receipt", "sources"}:
            raise ValueError("gap repair receipt has malformed evidence")
        if payload["commandDigest"] != command.command_digest:
            return rejected("operation_conflict")
        if type(payload["sources"]) is not list or len(payload["sources"]) != len(command.gap_ids):
            raise ValueError("gap repair receipt has an invalid source population")
        receipt = HistoryGapRepairReceipt.model_validate_json(
            encode_canonical_object(
                payload["receipt"], maximum_bytes=_MAX_RECEIPT_BYTES, context="gap repair receipt"
            )
        )
        sources = _GAP_SOURCES.validate_json(
            encode_canonical_object(
                payload["sources"], maximum_bytes=_MAX_SOURCE_BYTES, context="gap repair sources"
            )
        )
        if existing.actor != command.actor or receipt != _receipt_for(command, sources):
            raise ValueError("gap repair receipt contradicts its exact command")
        return HistoryGapRepairResult(
            outcome="replayed", operationId=command.operation_id, receipt=receipt
        )
    if dataset is None or dataset.state != "active":
        return rejected("dataset_fenced")
    if dataset.generation != command.generation:
        return rejected("generation_conflict")
    if dataset.configuration_revision != command.expected_revision:
        return rejected("revision_conflict")
    table = ci_history_gaps
    rows = (
        (
            await connection.execute(
                select(table)
                .where(
                    history_scope_predicate(table, command.scope),
                    table.c.generation == command.generation,
                    table.c.gap_id.in_(command.gap_ids),
                )
                .order_by(table.c.gap_id)
                .limit(len(command.gap_ids) + 1)
            )
        )
        .mappings()
        .all()
    )
    if tuple(row["gap_id"] for row in rows) != command.gap_ids:
        return rejected("gap_not_found")
    gaps: list[HistoryRecheckGap] = []
    for row in rows:
        gap_payload = decode_canonical_object(
            row["gap_canonical"], maximum_bytes=MAX_HISTORY_GAP_BYTES, context="history repair gap"
        )
        if gap_payload.get("schemaVersion") != "ci-economics-history-recheck-gap/v1":
            return rejected("unsupported_gap")
        gap = HistoryRecheckGap.model_validate_json(
            require_bytes(row["gap_canonical"], "repair gap")
        )
        if (
            gap.gap_id != row["gap_id"]
            or gap.configuration_revision > dataset.configuration_revision
        ):
            raise ValueError("gap repair source contradicts its immutable identity or epoch")
        gaps.append(gap)
    hints = gap_repair_hints(command, tuple(gaps))
    if isinstance(hints, str):
        return rejected(hints)
    now = await history_database_time(connection)
    if any(not dataset.configuration.selects(hint.workflow_id) for hint in hints):
        return rejected("workflow_unselected")
    if (
        now < dataset.configured_at
        or any(now < gap.run_created_at for gap in gaps)
        or any(row["recorded_at"] > now for row in rows)
    ):
        raise ValueError("gap repair source or configuration postdates admission")
    # One savepoint keeps late refusal atomic even for a caller that commits its outer transaction.
    async with connection.begin_nested() as batch:
        for hint in hints:
            outcome = await admit_history_recheck(
                connection, dataset, hint, now=now, revisit_completed=True
            )
            if outcome not in {"admitted", "replayed"}:
                await batch.rollback()
                if outcome == "capacity_reached":
                    return rejected("capacity_reached")
                raise ValueError("admitted gap source failed current queue admission")
        receipt = _receipt_for(command, tuple(gaps))
        event = prepare_audit_event(
            AuditEventInput(
                idempotency_key=command.audit_key,
                subject_type="policy-decision",
                subject_id=f"ci-history:{command.installation_id}:{command.repository_id}",
                event_type=HISTORY_GAPS_REQUEUED_EVENT_TYPE,
                created_at=now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                actor=command.actor,
                installation_id=command.installation_id,
                repository_id=command.repository_id,
                payload=cast(
                    JsonValue,
                    {
                        "commandDigest": command.command_digest,
                        "receipt": receipt.model_dump(mode="json"),
                        "sources": [gap.model_dump(mode="json") for gap in gaps],
                    },
                ),
            )
        )
        if not isinstance(
            await audit._append_pair_owned(event, command.scope), AuditAppendAppended
        ):
            raise ValueError("gap repair conflicts with its audit slot")
    return HistoryGapRepairResult(
        outcome="committed", operationId=command.operation_id, receipt=receipt
    )
