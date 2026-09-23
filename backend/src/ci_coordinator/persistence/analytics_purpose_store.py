from collections.abc import Callable, Mapping
from typing import cast

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import AuditAppendAppended, AuditEventInput, prepare_audit_event
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.ci_economics.analytics_configuration import (
    MAX_PURPOSE_BYTES,
    PURPOSE_CONFIGURED_EVENT,
    ConfigurePurposeSettings,
    PurposeSettingsConflict,
    PurposeSettingsQuery,
    PurposeSettingsResult,
    PurposeSettingsSaved,
    PurposeSettingsSnapshot,
)
from ci_coordinator.ci_economics.archive_analytics_models import AnalyticsUnavailable
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence._schema_analytics_purpose import analytics_purpose_settings as table
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.canonical_row import require_bytes
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    history_scope_predicate,
    load_history_dataset,
    lock_history_scope,
)
from ci_coordinator.persistence.ci_history_unit_of_work import PostgresPurposeUnitOfWork
from ci_coordinator.persistence.errors import PersistenceError


async def load_purpose_settings(
    connection: AsyncConnection, query: PurposeSettingsQuery
) -> PurposeSettingsSnapshot:
    row = (
        (await connection.execute(select(table).where(history_scope_predicate(table, query.scope))))
        .mappings()
        .one_or_none()
    )
    return decode_purpose_settings(row, query)


def decode_purpose_settings(
    row: Mapping[str, object] | RowMapping | None, query: PurposeSettingsQuery
) -> PurposeSettingsSnapshot:
    if row is not None:
        payload = require_bytes(row["snapshot_canonical"], "purpose settings")
        if not 1 <= len(payload) <= MAX_PURPOSE_BYTES:
            raise ValueError("purpose snapshot byte bound differs")
        snapshot = PurposeSettingsSnapshot.model_validate_json(payload)
        if (
            snapshot.mapping is None
            or snapshot.scope != query.scope
            or snapshot.generation != row["generation"]
            or snapshot.revision != row["revision"]
            or canonical_json(snapshot.model_dump(mode="json")) != payload
            or snapshot.generation > query.generation
        ):
            raise ValueError("purpose snapshot differs from its stored identity")
        if snapshot.generation == query.generation:
            return snapshot
    return PurposeSettingsSnapshot(**query.model_dump(), revision=0, mapping=None)


async def configure_purpose_settings(
    connection: AsyncConnection,
    audit: _PostgresAuditEventRepository,
    command: ConfigurePurposeSettings,
) -> PurposeSettingsResult:
    command = ConfigurePurposeSettings.model_validate(command)
    await lock_history_scope(connection, command.scope)
    dataset = await load_history_dataset(connection, command.scope, locked=True)
    if dataset is None or dataset.state not in {"active", "paused"}:
        return PurposeSettingsConflict("dataset_fenced")
    if dataset.generation != command.generation:
        return PurposeSettingsConflict("generation_changed")
    query = PurposeSettingsQuery(
        installation_id=command.installation_id,
        repository_id=command.repository_id,
        generation=command.generation,
    )
    current = await load_purpose_settings(connection, query)
    existing = await audit._find_pair_owned(
        command.audit_key, PURPOSE_CONFIGURED_EVENT, command.scope
    )
    successor = command.successor()
    if existing is not None:
        payload = existing.payload
        if type(payload) is not dict or set(payload) != {"commandDigest", "snapshot"}:
            raise ValueError("purpose receipt has malformed evidence")
        if payload["commandDigest"] != command.command_digest:
            return PurposeSettingsConflict("operation_conflict")
        snapshot = PurposeSettingsSnapshot.model_validate_json(canonical_json(payload["snapshot"]))
        if (
            existing.actor != command.actor
            or snapshot != successor
            or current.revision < snapshot.revision
            or (current.revision == snapshot.revision and current != snapshot)
        ):
            raise ValueError("purpose receipt contradicts committed settings")
        return PurposeSettingsSaved(snapshot, replayed=True)
    if current.revision != command.expected_revision:
        return PurposeSettingsConflict("revision_conflict")
    encoded = canonical_json(successor.model_dump(mode="json"))
    if len(encoded) > MAX_PURPOSE_BYTES:
        raise ValueError("purpose snapshot exceeds storage bound")
    now = await history_database_time(connection)
    event = prepare_audit_event(
        AuditEventInput(
            idempotency_key=command.audit_key,
            subject_type="policy-decision",
            subject_id=f"ci-analytics-purpose:{command.installation_id}:{command.repository_id}",
            event_type=PURPOSE_CONFIGURED_EVENT,
            created_at=now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            actor=command.actor,
            installation_id=command.installation_id,
            repository_id=command.repository_id,
            payload=cast(
                JsonValue,
                {
                    "commandDigest": command.command_digest,
                    "snapshot": successor.model_dump(mode="json"),
                },
            ),
        )
    )
    if not isinstance(await audit._append_pair_owned(event, command.scope), AuditAppendAppended):
        raise ValueError("purpose configuration conflicts with its audit slot")
    values = {
        "generation": successor.generation,
        "revision": successor.revision,
        "snapshot_canonical": encoded,
    }
    present = await connection.scalar(
        select(table.c.revision).where(history_scope_predicate(table, command.scope))
    )
    if present is None:
        await connection.execute(
            insert(table).values(
                installation_id=command.installation_id,
                repository_id=command.repository_id,
                **values,
            )
        )
    else:
        await connection.execute(
            update(table).where(history_scope_predicate(table, command.scope)).values(**values)
        )
    return PurposeSettingsSaved(successor, replayed=False)


class TransactionalPurposeSettingsStore:
    def __init__(self, unit_of_work: Callable[[], PostgresPurposeUnitOfWork]) -> None:
        self._unit_of_work = unit_of_work

    async def read_settings(
        self, query: PurposeSettingsQuery
    ) -> PurposeSettingsSnapshot | AnalyticsUnavailable:
        query = PurposeSettingsQuery.model_validate(query)
        try:
            async with self._unit_of_work() as transaction:
                connection = transaction.history_connection
                await _limits(connection)
                dataset = await load_history_dataset(connection, query.scope)
                if dataset is None or dataset.state not in {"active", "paused"}:
                    return AnalyticsUnavailable(reason="dataset_unavailable")
                if dataset.generation != query.generation:
                    return AnalyticsUnavailable(reason="generation_changed")
                snapshot = await load_purpose_settings(connection, query)
                if await load_history_dataset(connection, query.scope) != dataset:
                    return AnalyticsUnavailable(reason="snapshot_changed")
                return snapshot
        except (SQLAlchemyError, PersistenceError, ValueError, TypeError) as error:
            raise CiEconomicsStoreUnavailable("purpose settings unavailable") from error

    async def configure_settings(self, command: ConfigurePurposeSettings) -> PurposeSettingsResult:
        try:
            async with self._unit_of_work() as transaction:
                await _limits(transaction.history_connection)
                result = await configure_purpose_settings(
                    transaction.history_connection, transaction.history_audit, command
                )
                if isinstance(result, PurposeSettingsSaved) and not result.replayed:
                    await transaction.commit()
                return result
        except (SQLAlchemyError, PersistenceError, ValueError, TypeError) as error:
            raise CiEconomicsStoreUnavailable("purpose configuration unavailable") from error


async def _limits(connection: AsyncConnection) -> None:
    for setting in ("statement_timeout", "lock_timeout"):
        await connection.execute(select(func.set_config(setting, "5000", True)))
