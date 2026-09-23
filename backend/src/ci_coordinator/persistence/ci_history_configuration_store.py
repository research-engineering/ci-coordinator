from datetime import datetime
from typing import cast

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import AuditAppendAppended, AuditEventInput, prepare_audit_event
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.ci_economics.history_commands import (
    HISTORY_CONFIGURED_EVENT_TYPE,
    ConfigureHistory,
    HistoryConfigurationConflict,
    HistoryConfigurationInvalid,
    HistoryConfigurationResult,
    HistoryConfigured,
)
from ci_coordinator.ci_economics.history_configuration import (
    MAX_HISTORY_DATASETS,
    HistoryDataset,
    HistoryDefaults,
)
from ci_coordinator.ci_economics.history_lifecycle import configure_history_state
from ci_coordinator.ci_economics.history_payload import HistoryDatasetPayload
from ci_coordinator.ci_economics.history_recent import configure_recent_history
from ci_coordinator.persistence._schema_ci_history_control import (
    ci_history_datasets,
    ci_history_defaults,
    ci_history_rechecks,
    ci_history_scans,
)
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.ci_history_control_codec import (
    encode_history_dataset,
    encode_history_scan,
)
from ci_coordinator.persistence.ci_history_retention_codec import decode_history_defaults
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    history_scope_predicate,
    load_history_dataset,
    load_history_scan,
    lock_history_scope,
    write_history_dataset,
    write_history_scan,
)


async def lock_history_admission(connection: AsyncConnection) -> None:
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": "ci-history-policy-admission/v1"},
    )


async def load_history_defaults(connection: AsyncConnection) -> HistoryDefaults:
    row = (await connection.execute(select(ci_history_defaults))).mappings().one_or_none()
    if row is None:
        raise ValueError("history default policy is not initialized")
    return decode_history_defaults(row)


async def configure_history(
    connection: AsyncConnection,
    audit: _PostgresAuditEventRepository,
    command: ConfigureHistory,
) -> HistoryConfigurationResult:
    command = ConfigureHistory.model_validate(command)
    scope = command.scope
    await lock_history_admission(connection)
    await lock_history_scope(connection, scope)
    prior = await load_history_dataset(connection, scope, locked=True)
    scan = await load_history_scan(connection, scope, locked=True)
    recent = await load_history_scan(connection, scope, lane="discovery", locked=True)
    if (prior is None) != (scan is None) or (prior is None) != (recent is None):
        raise ValueError("history configuration and both scans are not paired")
    existing = await audit._find_pair_owned(command.audit_key, HISTORY_CONFIGURED_EVENT_TYPE, scope)
    if existing is not None:
        payload = existing.payload
        if type(payload) is not dict or set(payload) != {"commandDigest", "snapshot"}:
            raise ValueError("history configuration receipt has malformed evidence")
        snapshot = HistoryDatasetPayload.model_validate(payload["snapshot"]).to_dataset()
        if snapshot.scope != scope:
            raise ValueError("history receipt has foreign scope")
        if payload["commandDigest"] != command.command_digest:
            return HistoryConfigurationConflict("operation_conflict")
        _validate_receipt(command, snapshot, prior)
        if existing.actor != command.actor:
            raise ValueError("history receipt actor disagrees with its command")
        return HistoryConfigured(snapshot, replayed=True)
    if (0 if prior is None else prior.configuration_revision) != command.expected_revision:
        return HistoryConfigurationConflict("revision_conflict")
    if prior is not None and prior.state not in {"active", "paused"}:
        return HistoryConfigurationConflict("dataset_fenced")
    if prior is None:
        population = (
            select(ci_history_datasets.c.installation_id).limit(MAX_HISTORY_DATASETS + 1).subquery()
        )
        count = await connection.scalar(select(func.count()).select_from(population))
        if type(count) is not int or count > MAX_HISTORY_DATASETS:
            raise ValueError("history dataset admission bound is violated")
        if count == MAX_HISTORY_DATASETS:
            return HistoryConfigurationConflict("capacity_reached")
    defaults = await load_history_defaults(connection)
    now = await history_database_time(connection)
    if now < defaults.updated_at:
        raise ValueError("history clock precedes its default policy")
    lower_bound = (
        None
        if command.initial_created_from is None
        else datetime.fromisoformat(command.initial_created_from)
    )
    if lower_bound is not None and lower_bound > now.replace(microsecond=0):
        return HistoryConfigurationInvalid()
    successor, successor_scan = configure_history_state(
        scope,
        command.configuration,
        prior=prior,
        scan=scan,
        now=now,
        initial_created_from=lower_bound,
        rescan=command.rescan,
    )
    successor_recent = configure_recent_history(successor, successor_scan, prior=recent)
    if (
        prior is not None
        and prior.configuration.workflow_ids != successor.configuration.workflow_ids
    ):
        excluded = delete(ci_history_rechecks).where(
            history_scope_predicate(ci_history_rechecks, scope),
        )
        selected = successor.configuration.workflow_ids
        if selected:
            await connection.execute(
                excluded.where(ci_history_rechecks.c.workflow_id.not_in(selected))
            )
    event = prepare_audit_event(
        AuditEventInput(
            idempotency_key=command.audit_key,
            subject_type="policy-decision",
            subject_id=f"ci-history:{scope.installation_id}:{scope.repository_id}",
            event_type=HISTORY_CONFIGURED_EVENT_TYPE,
            created_at=now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            actor=command.actor,
            installation_id=scope.installation_id,
            repository_id=scope.repository_id,
            payload=cast(
                JsonValue,
                {
                    "commandDigest": command.command_digest,
                    "snapshot": successor.canonical_mapping(),
                },
            ),
        )
    )
    appended = await audit._append_pair_owned(event, scope)
    if not isinstance(appended, AuditAppendAppended):
        raise ValueError("history configuration conflicts with its audit slot")
    if prior is None:
        await connection.execute(
            insert(ci_history_datasets).values(**encode_history_dataset(successor))
        )
        await connection.execute(
            insert(ci_history_scans).values(
                [encode_history_scan(successor_scan), encode_history_scan(successor_recent)]
            )
        )
    else:
        if scan is None or recent is None:
            raise ValueError("existing history is missing a paired scan")
        await write_history_dataset(connection, prior, successor)
        await write_history_scan(connection, scan, successor_scan)
        await write_history_scan(connection, recent, successor_recent)
    return HistoryConfigured(successor, replayed=False)


def _validate_receipt(
    command: ConfigureHistory, snapshot: HistoryDataset, current: HistoryDataset | None
) -> None:
    if (
        snapshot.configuration_revision != command.expected_revision + 1
        or snapshot.configuration != command.configuration
        or current is None
        or current.configuration_revision < snapshot.configuration_revision
        or current.data_revision < snapshot.data_revision
        or current.generation < snapshot.generation
    ):
        raise ValueError("history receipt contradicts its paired dataset")
    if current.configuration_revision == snapshot.configuration_revision and (
        current.configuration != snapshot.configuration
        or current.configured_at != snapshot.configured_at
        or current.generation != snapshot.generation
        or current.state != snapshot.state
    ):
        raise ValueError("history receipt conflicts with the current configuration")
    if current.data_revision == snapshot.data_revision and current.usage != snapshot.usage:
        raise ValueError("history receipt conflicts with current data accounting")
