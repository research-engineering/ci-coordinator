from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.schema import (
    production_admission_scope_bindings,
    production_scope_states,
)
from ci_coordinator.production_admission.cutover_state import ProductionScopeState


async def production_database_now(connection: AsyncConnection) -> datetime:
    value = await connection.scalar(select(func.clock_timestamp()))
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise PersistenceInvariantViolation("production database time must be aware")
    return value.astimezone(UTC)


async def load_production_scope_state(
    connection: AsyncConnection, scope: RepositoryScope
) -> ProductionScopeState | None:
    binding = production_admission_scope_bindings
    states = production_scope_states
    result = await connection.execute(
        select(states, binding.c.admission_subject_digest.label("active_subject_digest"))
        .select_from(
            states.outerjoin(
                binding,
                and_(
                    binding.c.authority_id == states.c.active_authority_id,
                    binding.c.installation_id == states.c.installation_id,
                    binding.c.repository_id == states.c.repository_id,
                ),
            )
        )
        .where(
            states.c.installation_id == scope.installation_id,
            states.c.repository_id == scope.repository_id,
        )
    )
    row = result.mappings().one_or_none()
    return None if row is None else _decode_state(row)


def _decode_state(row: RowMapping) -> ProductionScopeState:
    try:
        state = ProductionScopeState(
            scope=RepositoryScope(row["installation_id"], row["repository_id"]),
            revision=row["revision"],
            generation=row["generation"],
            revoked_through_generation=row["revoked_through_generation"],
            active_authority_id=row["active_authority_id"],
            active_subject_digest=row["active_subject_digest"],
            staged_authority_id=row["staged_authority_id"],
            latch_override_id=row["latch_override_id"],
            latch_applied_at=row["latch_applied_at"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PersistenceInvariantViolation("stored production scope state is invalid") from error
    return state
