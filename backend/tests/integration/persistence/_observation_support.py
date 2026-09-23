from datetime import datetime

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.ci_economics.observation import ObservationConfiguration, ObservationSnapshot
from ci_coordinator.ci_economics.observation_commands import ConfigureObservation
from ci_coordinator.ci_economics.observation_progress import ObservationScanProgress
from ci_coordinator.ci_economics.observation_schedule import initial_observation_scans
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_observation_adapters import TransactionalObservationStore
from ci_coordinator.persistence.ci_observation_codec import encode_observation_payload, encode_scan
from ci_coordinator.persistence.ci_observation_unit_of_work import PostgresObservationUnitOfWork
from ci_coordinator.persistence.schema import ci_observation_scans, ci_observation_subscriptions

SCOPE = RepositoryScope(101, 202)


def observation_command(scope: RepositoryScope = SCOPE) -> ConfigureObservation:
    return ConfigureObservation(
        scope, 0, ObservationConfiguration(True, (10,), 1), "enable", "admin"
    )


def observation_store(engine: AsyncEngine) -> TransactionalObservationStore:
    return TransactionalObservationStore(lambda: PostgresObservationUnitOfWork(engine))


async def seed_configuration(
    connection: AsyncConnection, scope: RepositoryScope, now: datetime
) -> ObservationSnapshot:
    snapshot = observation_command(scope).next_snapshot(now)
    await connection.execute(
        insert(ci_observation_subscriptions).values(
            installation_id=scope.installation_id,
            repository_id=scope.repository_id,
            revision=snapshot.revision,
            enabled=snapshot.configuration.enabled,
            snapshot_digest=snapshot.snapshot_digest,
            snapshot_canonical=encode_observation_payload(snapshot.canonical_mapping()),
            next_attempt_at=now,
            preferred_lane="recent",
            detail_truncated_until=None,
        )
    )
    await connection.execute(
        insert(ci_observation_scans),
        [
            encode_scan(ObservationScanProgress(state))
            for state in initial_observation_scans(snapshot)
        ],
    )
    return snapshot
