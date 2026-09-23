from dataclasses import replace
from datetime import datetime
from typing import cast

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import AuditAppendAppended, AuditEventInput, prepare_audit_event
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.ci_economics.archive_retention import (
    ArchiveDetailRetention,
    apply_detail_policy,
)
from ci_coordinator.ci_economics.history_configuration import HistoryDataset, HistoryUsage
from ci_coordinator.ci_economics.history_retention_commands import (
    HISTORY_RETENTION_EVENT,
    ApplyHistoryRetention,
    HistoryRetentionEffect,
    HistoryRetentionPreview,
    HistoryRetentionResult,
    HistoryRetentionSelection,
)
from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence._schema_ci_history_archive import ci_history_attempts as attempts
from ci_coordinator.persistence._schema_ci_history_archive import ci_history_details as details
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.ci_history_configuration_store import (
    load_history_defaults,
    lock_history_admission,
)
from ci_coordinator.persistence.ci_history_read_codec import read_history_detail
from ci_coordinator.persistence.ci_history_retention_codec import (
    decode_history_retention,
    encode_history_retention,
)
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    history_scope_predicate,
    load_history_dataset,
    lock_history_scope,
    write_history_dataset,
)


async def preview_history_retention(
    connection: AsyncConnection, selection: HistoryRetentionSelection
) -> HistoryRetentionPreview | None:
    selection = HistoryRetentionSelection.model_validate(selection)
    async with connection.begin_nested():
        dataset = await _lock_dataset(connection, selection)
        if dataset is None:
            return None
        result = await _preview(connection, selection, dataset)
        return None if result is None else result[0]


async def apply_history_retention(
    connection: AsyncConnection,
    audit: _PostgresAuditEventRepository,
    command: ApplyHistoryRetention,
) -> HistoryRetentionResult:
    command = ApplyHistoryRetention.model_validate(command)
    selection = command.selection
    async with connection.begin_nested():
        await lock_history_admission(connection)
        await lock_history_scope(connection, selection.scope)
        current = await load_history_dataset(connection, selection.scope, locked=True)
        existing = await audit._find_pair_owned(
            command.audit_key, HISTORY_RETENTION_EVENT, selection.scope
        )
        if existing is not None:
            payload = existing.payload
            if type(payload) is not dict or set(payload) != {"commandDigest", "receipt"}:
                raise ValueError("retention receipt is malformed")
            if (
                payload["commandDigest"] != command.command_digest
                or existing.actor != command.actor
            ):
                return _conflict(command, "operation_conflict")
            receipt = HistoryRetentionResult.model_validate_json(canonical_json(payload["receipt"]))
            if (
                receipt.outcome != "committed"
                or receipt.operation_id != command.operation_id
                or receipt.preview is None
                or receipt.preview.selection != selection
                or receipt.preview.review_digest != command.reviewed_digest
                or receipt.data_revision != selection.data_revision + 1
                or current is None
                or current.data_revision < receipt.data_revision
            ):
                raise ValueError("retention receipt contradicts its command or dataset")
            return HistoryRetentionResult.model_validate(
                {**receipt.model_dump(), "outcome": "replayed"}
            )
        if current is None or not _matches(current, selection):
            return _conflict(command, "revision_conflict")
        result = await _preview(connection, selection, current)
        if result is None:
            return _conflict(command, "revision_conflict")
        preview, changes, now = result
        if preview.review_digest != command.reviewed_digest:
            return _conflict(command, "review_conflict")
        receipt = HistoryRetentionResult(
            outcome="committed",
            operationId=command.operation_id,
            preview=preview,
            dataRevision=current.data_revision + 1,
        )
        event = prepare_audit_event(
            AuditEventInput(
                idempotency_key=command.audit_key,
                subject_type="policy-decision",
                subject_id=f"ci-history:{selection.installation_id}:{selection.repository_id}",
                event_type=HISTORY_RETENTION_EVENT,
                created_at=now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                actor=command.actor,
                installation_id=selection.installation_id,
                repository_id=selection.repository_id,
                payload=cast(
                    JsonValue,
                    {
                        "commandDigest": command.command_digest,
                        "receipt": receipt.model_dump(mode="json"),
                    },
                ),
            )
        )
        removed_keys = [
            (effect.key.workflow_run_id, effect.key.run_attempt)
            for effect in preview.effects
            if effect.delete_payload
        ]
        released = 0
        if removed_keys:
            removed = (
                await connection.execute(
                    delete(details)
                    .where(
                        history_scope_predicate(details, selection.scope),
                        details.c.generation == selection.generation,
                        tuple_(details.c.workflow_run_id, details.c.run_attempt).in_(removed_keys),
                    )
                    .returning(
                        details.c.workflow_run_id,
                        details.c.run_attempt,
                        func.octet_length(details.c.detail_canonical),
                    )
                )
            ).all()
            if {(r, a) for r, a, _ in removed} != set(removed_keys):
                raise ValueError("retention deletion lost a selected child")
            released = sum(size for _, _, size in removed)
        if released != preview.released_bytes:
            raise ValueError("retention released bytes differ from reviewed bytes")
        for key, retention in changes:
            changed = await connection.execute(
                update(attempts)
                .where(
                    history_scope_predicate(attempts, selection.scope),
                    attempts.c.generation == selection.generation,
                    attempts.c.workflow_run_id == key[0],
                    attempts.c.run_attempt == key[1],
                )
                .values(**encode_history_retention(retention))
            )
            if changed.rowcount != 1:
                raise ValueError("retention update lost a selected parent")
        successor = replace(
            current,
            data_revision=current.data_revision + 1,
            usage=current.usage.release(
                HistoryUsage(attempts=0, jobs=0, gaps=0, canonicalBytes=released)
            ),
        )
        await write_history_dataset(connection, current, successor)
        if not isinstance(
            await audit._append_pair_owned(event, selection.scope), AuditAppendAppended
        ):
            raise ValueError("retention audit pair conflicts")
        return receipt


async def _lock_dataset(
    connection: AsyncConnection, selection: HistoryRetentionSelection
) -> HistoryDataset | None:
    await lock_history_admission(connection)
    await lock_history_scope(connection, selection.scope)
    dataset = await load_history_dataset(connection, selection.scope, locked=True)
    return dataset if dataset is not None and _matches(dataset, selection) else None


def _matches(dataset: HistoryDataset, selection: HistoryRetentionSelection) -> bool:
    return (
        dataset.scope == selection.scope
        and dataset.state in {"active", "paused"}
        and dataset.generation == selection.generation
        and dataset.configuration_revision == selection.configuration_revision
        and dataset.data_revision == selection.data_revision
    )


async def _preview(
    connection: AsyncConnection,
    selection: HistoryRetentionSelection,
    dataset: HistoryDataset,
) -> (
    tuple[HistoryRetentionPreview, list[tuple[tuple[int, int], ArchiveDetailRetention]], datetime]
    | None
):
    defaults = await load_history_defaults(connection)
    now = await history_database_time(connection)
    cutoff = datetime.fromisoformat(selection.imported_through)
    if defaults.revision != selection.default_revision or cutoff > now:
        return None
    if now < max(defaults.updated_at, dataset.configured_at):
        raise ValueError("retention observation precedes configuration")
    policy, reference = dataset.resolve_detail_policy(defaults)
    keys = [(key.workflow_run_id, key.run_attempt) for key in selection.keys]
    detail_bytes = (
        select(func.octet_length(details.c.detail_canonical))
        .where(
            history_scope_predicate(details, selection.scope),
            details.c.generation == selection.generation,
            details.c.workflow_run_id == attempts.c.workflow_run_id,
            details.c.run_attempt == attempts.c.run_attempt,
        )
        .scalar_subquery()
    )
    columns = [
        attempts.c.workflow_run_id,
        attempts.c.run_attempt,
        attempts.c.first_imported_at,
        *(c for c in attempts.c if c.name.startswith("detail_")),
        detail_bytes.label("payload_bytes"),
    ]
    rows = (
        (
            await connection.execute(
                select(*columns)
                .where(
                    history_scope_predicate(attempts, selection.scope),
                    attempts.c.generation == selection.generation,
                    tuple_(attempts.c.workflow_run_id, attempts.c.run_attempt).in_(keys),
                )
                .order_by(attempts.c.workflow_run_id, attempts.c.run_attempt)
                .limit(100)
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )
    if [(row["workflow_run_id"], row["run_attempt"]) for row in rows] != keys:
        return None
    effects = []
    changes = []
    for key, row in zip(selection.keys, rows, strict=True):
        prior = decode_history_retention(row)
        if row["first_imported_at"] > cutoff or (
            prior.first_imported_at is not None and prior.first_imported_at > cutoff
        ):
            return None
        size = row["payload_bytes"]
        if (prior.state == "retained") != (size is not None):
            raise ValueError("retention parent and child disagree")
        successor = (
            replace(prior, state="expired")
            if selection.action == "erase_details" and prior.state == "retained"
            else apply_detail_policy(prior, policy, reference, now=now)
            if selection.action == "apply_policy"
            else prior
        )
        deleting = prior.state == "retained" and successor.state == "expired"
        effects.append(
            HistoryRetentionEffect(
                key=key,
                before=read_history_detail(dict(row), now=now),
                after=read_history_detail(encode_history_retention(successor), now=now),
                payloadBytes=0 if size is None else size,
                deletePayload=deleting,
            )
        )
        if successor != prior:
            changes.append(((key.workflow_run_id, key.run_attempt), successor))
    return (
        HistoryRetentionPreview(
            selection=selection,
            effects=tuple(effects),
            releasedBytes=sum(effect.payload_bytes for effect in effects if effect.delete_payload),
            deletedDetails=sum(effect.delete_payload for effect in effects),
        ),
        changes,
        now,
    )


def _conflict(command: ApplyHistoryRetention, reason: str) -> HistoryRetentionResult:
    return HistoryRetentionResult.model_validate(
        {
            "outcome": reason,
            "operationId": command.operation_id,
            "preview": None,
            "dataRevision": None,
        }
    )
