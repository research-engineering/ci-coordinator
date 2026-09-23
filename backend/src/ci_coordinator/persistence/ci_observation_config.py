from typing import cast

from sqlalchemy import Insert, Update, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import AuditAppendAppended, AuditEventInput, prepare_audit_event
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.ci_economics.observation import MAX_OBSERVATION_REPOSITORIES
from ci_coordinator.ci_economics.observation_commands import (
    OBSERVATION_CONFIGURED_EVENT_TYPE,
    ConfigureObservation,
    ObservationCommitted,
    ObservationConflict,
    ObservationWriteResult,
)
from ci_coordinator.ci_economics.observation_payload import ObservationSnapshotPayload
from ci_coordinator.ci_economics.observation_progress import ObservationScanProgress
from ci_coordinator.ci_economics.observation_schedule import initial_observation_scans
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.ci_observation_codec import (
    decode_subscription,
    encode_observation_payload,
    encode_scan,
)
from ci_coordinator.persistence.ci_observation_lock import (
    load_locked_subscription,
    lock_observation_quota,
    lock_observation_scope,
    observation_database_time,
    observation_scope_predicate,
)
from ci_coordinator.persistence.schema import ci_observation_scans, ci_observation_subscriptions


async def configure_observation(
    connection: AsyncConnection,
    audit: _PostgresAuditEventRepository,
    command: ConfigureObservation,
) -> ObservationWriteResult:
    if type(command) is not ConfigureObservation:
        raise TypeError("observation write requires exact command")
    await lock_observation_quota(connection)
    await lock_observation_scope(connection, command.scope)
    row = await load_locked_subscription(connection, command.scope)
    prior = None if row is None else decode_subscription(row)
    existing = await audit._find_pair_owned(
        command.audit_key, OBSERVATION_CONFIGURED_EVENT_TYPE, command.scope
    )
    if existing is not None:
        payload = existing.payload
        if type(payload) is not dict or set(payload) != {"commandDigest", "snapshot"}:
            raise ValueError("observation operation has malformed audit evidence")
        snapshot = ObservationSnapshotPayload.model_validate(payload["snapshot"]).to_snapshot()
        if snapshot.scope != command.scope:
            raise ValueError("observation operation has foreign audit scope")
        if payload["commandDigest"] != command.command_digest:
            return ObservationConflict("operation_conflict")
        if (
            snapshot.revision != command.expected_revision + 1
            or snapshot.configuration != command.configuration
            or existing.actor != command.actor
            or prior is None
            or prior.revision < snapshot.revision
        ):
            raise ValueError("observation receipt contradicts its paired state")
        if prior.revision == snapshot.revision and prior != snapshot:
            raise ValueError("observation receipt and current snapshot disagree")
        return ObservationCommitted(snapshot, replayed=True)
    if (0 if prior is None else prior.revision) != command.expected_revision:
        return ObservationConflict("revision_conflict")
    table = ci_observation_subscriptions
    if prior is None:
        population = (
            select(table.c.installation_id).limit(MAX_OBSERVATION_REPOSITORIES + 1).subquery()
        )
        count = await connection.scalar(select(func.count()).select_from(population))
        if type(count) is not int or count > MAX_OBSERVATION_REPOSITORIES:
            raise ValueError("observation configuration quota is violated")
        if count == MAX_OBSERVATION_REPOSITORIES:
            return ObservationConflict("capacity_reached")
    now = await observation_database_time(connection)
    if prior is not None and now < prior.configured_at:
        raise ValueError("observation database clock precedes its current configuration")
    snapshot = command.next_snapshot(now)
    event = prepare_audit_event(
        AuditEventInput(
            idempotency_key=command.audit_key,
            subject_type="policy-decision",
            subject_id=f"ci-observation:{command.scope.installation_id}:{command.scope.repository_id}",
            event_type=OBSERVATION_CONFIGURED_EVENT_TYPE,
            created_at=now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            actor=command.actor,
            installation_id=command.scope.installation_id,
            repository_id=command.scope.repository_id,
            payload=cast(
                JsonValue,
                {"commandDigest": command.command_digest, "snapshot": snapshot.canonical_mapping()},
            ),
        )
    )
    # Scope locking also fences every old lane before taking the audit-head lock.
    scans = ci_observation_scans
    await connection.execute(
        select(scans.c.lane)
        .where(
            scans.c.installation_id == command.scope.installation_id,
            scans.c.repository_id == command.scope.repository_id,
        )
        .order_by(scans.c.lane)
        .with_for_update()
    )
    appended = await audit._append_pair_owned(event, command.scope)
    if not isinstance(appended, AuditAppendAppended):
        raise ValueError("observation operation contradicts its audit slot")
    values = dict(
        revision=snapshot.revision,
        enabled=snapshot.configuration.enabled,
        snapshot_digest=snapshot.snapshot_digest,
        snapshot_canonical=encode_observation_payload(snapshot.canonical_mapping()),
        next_attempt_at=now,
        preferred_lane="recent",
    )
    statement: Insert | Update
    if prior is None:
        statement = insert(table).values(
            **values,
            installation_id=command.scope.installation_id,
            repository_id=command.scope.repository_id,
            detail_truncated_until=None,
        )
    else:
        statement = (
            update(table)
            .where(
                observation_scope_predicate(command.scope),
                table.c.revision == command.expected_revision,
            )
            .values(**values)
        )
    revision = await connection.scalar(statement.returning(table.c.revision))
    if revision != snapshot.revision:
        raise ValueError("observation configuration CAS did not write its paired revision")
    await connection.execute(
        delete(scans).where(
            scans.c.installation_id == command.scope.installation_id,
            scans.c.repository_id == command.scope.repository_id,
        )
    )
    await connection.execute(
        insert(scans),
        [
            encode_scan(ObservationScanProgress(state))
            for state in initial_observation_scans(snapshot)
        ],
    )
    return ObservationCommitted(snapshot, replayed=False)
