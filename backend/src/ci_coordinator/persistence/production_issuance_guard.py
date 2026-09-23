"""Shared locked admission for selected registration and signed-plan persistence."""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from ci_coordinator.operator_controls.resolution import resolve_active_overrides
from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile
from ci_coordinator.persistence.config_epoch_repository import (
    _PostgresConfigEpochRepository,
    admit_config_epoch_lifecycle,
)
from ci_coordinator.persistence.operator_override_repository import (
    PostgresOperatorOverrideRepository,
)
from ci_coordinator.persistence.production_cutover_state import load_production_scope_state
from ci_coordinator.persistence.schema import (
    production_admission_authorities,
    production_admission_scope_bindings,
)
from ci_coordinator.plan_issuance import IssuanceGuardRejected
from ci_coordinator.production_admission import ProductionIssuanceGuard
from ci_coordinator.production_admission.current_evidence import (
    CURRENT_PRODUCTION_OBSERVATION_SECONDS,
)


async def production_guard_rejection(
    connection: AsyncConnection,
    guard: ProductionIssuanceGuard,
    *,
    now: datetime,
    config_epochs: _PostgresConfigEpochRepository,
    operator_overrides: PostgresOperatorOverrideRepository,
    compatibility_profile: CompatibilityProfile,
) -> IssuanceGuardRejected | None:
    """Caller owns the repository lock and holds it through every protected effect."""
    if now >= guard.not_after:
        return IssuanceGuardRejected("authority_expired")
    current = guard.current_evidence
    if current is None or not current.is_current_at(now):
        return IssuanceGuardRejected("current_evidence_invalid")
    state = await load_production_scope_state(connection, guard.scope)
    if state is None or not state.admits_current(
        current, authority_id=guard.authority_id, database_now=now
    ):
        return IssuanceGuardRejected("authority_generation_changed")
    registered = await connection.scalar(
        select(production_admission_authorities.c.authority_id)
        .join(
            production_admission_scope_bindings,
            production_admission_scope_bindings.c.authority_id
            == production_admission_authorities.c.authority_id,
        )
        .where(
            production_admission_authorities.c.authority_id == guard.authority_id,
            production_admission_authorities.c.expires_at == guard.not_after,
            production_admission_authorities.c.expires_at > now,
            production_admission_scope_bindings.c.installation_id == guard.scope.installation_id,
            production_admission_scope_bindings.c.repository_id == guard.scope.repository_id,
            production_admission_scope_bindings.c.admission_subject_digest
            == guard.admission_subject_digest,
            production_admission_scope_bindings.c.config_epoch_id == guard.config_epoch_id,
            production_admission_scope_bindings.c.target_registry_hash
            == guard.target_registry_hash,
        )
        .limit(1)
    )
    if registered != guard.authority_id:
        return IssuanceGuardRejected("authority_not_registered")
    await admit_config_epoch_lifecycle(connection, compatibility_profile)
    active = await config_epochs.load_active(guard.scope)
    if active is None or active.active.epoch_id != guard.config_epoch_id:
        return IssuanceGuardRejected("config_epoch_changed")
    overrides = await resolve_active_overrides(
        operator_overrides, scope=guard.scope, subject_id=guard.reconciliation_subject_id, now=now
    )
    return IssuanceGuardRejected("override_active") if overrides.force_full_ci else None


def production_guard_time_predicates(
    guard: ProductionIssuanceGuard,
) -> tuple[ColumnElement[bool], ...]:
    current = guard.current_evidence
    if current is None:
        raise ValueError("selected persistence requires current production evidence")
    return (
        func.clock_timestamp() < guard.not_after,
        func.clock_timestamp() >= current.database_started_at,
        func.clock_timestamp()
        < current.database_started_at + timedelta(seconds=CURRENT_PRODUCTION_OBSERVATION_SECONDS),
    )
