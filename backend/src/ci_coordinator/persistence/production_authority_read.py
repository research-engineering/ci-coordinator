"""Small retained receipt projections; bundle replay is never a planning hot-path read."""

from hashlib import sha256
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.canonical_row import require_bytes
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.production_cutover_state import (
    load_production_scope_state,
    production_database_now,
)
from ci_coordinator.persistence.repository_scope_lock import lock_repository_scope
from ci_coordinator.persistence.schema import (
    production_admission_authorities,
    production_evidence_bundles,
    production_staged_grants,
)
from ci_coordinator.production_admission.evidence_lookup import decode_production_lookup
from ci_coordinator.production_admission.limits import MAX_PRODUCTION_STAGE_BYTES
from ci_coordinator.production_admission.ports import RetainedProductionAuthority


async def load_production_evidence(
    connection: AsyncConnection, scope: RepositoryScope, authority_id: str
) -> bytes | None:
    grants, bundles = production_staged_grants, production_evidence_bundles
    row = (
        await connection.execute(
            select(bundles.c.canonical_json, bundles.c.content_sha256)
            .select_from(grants.join(bundles, grants.c.bundle_digest == bundles.c.bundle_digest))
            .where(
                grants.c.installation_id == scope.installation_id,
                grants.c.repository_id == scope.repository_id,
                grants.c.authority_id == authority_id,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    content = require_bytes(row[0], "production evidence")
    if not 1 <= len(content) <= MAX_PRODUCTION_STAGE_BYTES or sha256(content).hexdigest() != row[1]:
        raise PersistenceInvariantViolation("production evidence bytes contradict their identity")
    return content


async def load_retained_production_authority(
    connection: AsyncConnection, scope: RepositoryScope, *, purpose: Literal["active", "staged"]
) -> RetainedProductionAuthority | None:
    if type(scope) is not RepositoryScope or purpose not in {"active", "staged"}:
        raise ValueError("production authority read requires an exact scope and purpose")
    await lock_repository_scope(connection, scope)
    started_at = await production_database_now(connection)
    state = await load_production_scope_state(connection, scope)
    if state is None:
        return None
    authority_id = state.active_authority_id if purpose == "active" else state.staged_authority_id
    if authority_id is None:
        return None
    if purpose == "active" and (
        state.latch_override_id is not None or state.revoked_through_generation >= state.generation
    ):
        return None
    grants = production_staged_grants
    authorities = production_admission_authorities
    row = (
        await connection.execute(
            select(
                authorities.c.envelope_canonical_json,
                grants.c.lookup_canonical_json,
                grants.c.generation,
            )
            .select_from(
                grants.join(authorities, authorities.c.authority_id == grants.c.authority_id)
            )
            .where(
                grants.c.installation_id == scope.installation_id,
                grants.c.repository_id == scope.repository_id,
                grants.c.authority_id == authority_id,
            )
        )
    ).one_or_none()
    if row is None or row[2] != state.generation + (purpose == "staged"):
        raise PersistenceInvariantViolation(
            "retained production authority lacks its exact generation"
        )
    envelope = require_bytes(row[0], "production receipt")
    lookup = decode_production_lookup(require_bytes(row[1], "production lookup"))
    if lookup.repository.scope != scope:
        raise PersistenceInvariantViolation("retained production lookup crosses repository scope")
    return RetainedProductionAuthority(state, envelope, lookup, started_at)
