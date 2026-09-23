from __future__ import annotations

from hashlib import sha256

from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.kernel import hash_object
from ci_coordinator.persistence.canonical_row import require_bytes
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.schema import production_evidence_bundles, production_staged_grants
from ci_coordinator.production_admission import ProductionAdmissionGrant
from ci_coordinator.production_admission.current_evidence import CurrentActivationEvidence
from ci_coordinator.production_admission.evidence_lookup import decode_production_lookup
from ci_coordinator.production_admission.limits import (
    MAX_RETAINED_PRODUCTION_BUNDLES,
    MAX_RETAINED_PRODUCTION_BYTES,
)
from ci_coordinator.production_admission.relation_admission import StagedProductionEvidence

PRODUCTION_CAPACITY_LOCK = "ci-coordinator-production-evidence-capacity/v1"


async def staged_activation_matches(
    connection: AsyncConnection, authority_id: str, current: CurrentActivationEvidence
) -> bool:
    scope = current.scope_grant.subject.scope
    table = production_staged_grants
    row = (
        (
            await connection.execute(
                select(table).where(
                    table.c.installation_id == scope.installation_id,
                    table.c.repository_id == scope.repository_id,
                    table.c.authority_id == authority_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return False
    lookup = decode_production_lookup(require_bytes(row["lookup_canonical_json"], "staged lookup"))
    return (
        row["generation"] == current.scope_grant.relation.generation
        and row["bundle_digest"] == current.scope_grant.relation.evidence_bundle_digest
        and lookup.repository.scope == scope
        and hash_object(lookup.to_mapping()) == current.lookup_digest
    )


async def lock_production_evidence_capacity(connection: AsyncConnection) -> None:
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:domain, 0))"),
        {"domain": PRODUCTION_CAPACITY_LOCK},
    )


async def retain_production_evidence(
    connection: AsyncConnection, evidence: StagedProductionEvidence
) -> bool:
    """The caller holds capacity, then repository locks, for this whole transaction."""
    if type(evidence) is not StagedProductionEvidence:
        raise TypeError("retention requires admitted staged production evidence")
    table = production_evidence_bundles
    digest = evidence.relation.evidence_bundle_digest
    content_hash = sha256(evidence.canonical_bytes).hexdigest()
    existing = (
        (await connection.execute(select(table).where(table.c.bundle_digest == digest)))
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        if (
            existing["content_sha256"] != content_hash
            or existing["byte_count"] != len(evidence.canonical_bytes)
            or require_bytes(existing["canonical_json"], "retained production evidence")
            != evidence.canonical_bytes
        ):
            raise PersistenceInvariantViolation("retained production evidence identity conflicts")
        return True
    count, size = (
        await connection.execute(
            select(func.count(), func.coalesce(func.sum(table.c.byte_count), 0)).select_from(table)
        )
    ).one()
    if (
        count >= MAX_RETAINED_PRODUCTION_BUNDLES
        or size + len(evidence.canonical_bytes) > MAX_RETAINED_PRODUCTION_BYTES
    ):
        return False
    await connection.execute(
        insert(table).values(
            bundle_digest=digest,
            content_sha256=content_hash,
            canonical_json=evidence.canonical_bytes,
            byte_count=len(evidence.canonical_bytes),
            retained_at=func.clock_timestamp(),
        )
    )
    return True


async def retain_production_stage(
    connection: AsyncConnection,
    *,
    grant: ProductionAdmissionGrant,
    evidence: StagedProductionEvidence,
) -> None:
    scope = evidence.scope_grant.subject.scope
    if (
        type(grant) is not ProductionAdmissionGrant
        or grant.scope_grant(scope) != evidence.scope_grant
    ):
        raise PersistenceInvariantViolation("staged relation differs from its signed scope grant")
    table = production_staged_grants
    row = {
        "installation_id": scope.installation_id,
        "repository_id": scope.repository_id,
        "authority_id": grant.authority_id,
        "generation": evidence.relation.generation,
        "bundle_digest": evidence.relation.evidence_bundle_digest,
        "lookup_canonical_json": evidence.lookup.canonical_bytes,
    }
    existing = (
        (
            await connection.execute(
                select(table).where(
                    table.c.installation_id == scope.installation_id,
                    table.c.repository_id == scope.repository_id,
                    table.c.authority_id == grant.authority_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        stored = dict(existing)
        stored.pop("staged_at")
        stored["lookup_canonical_json"] = require_bytes(
            stored["lookup_canonical_json"], "production lookup"
        )
        if stored != row:
            raise PersistenceInvariantViolation("production staged authority conflicts")
        return
    await connection.execute(insert(table).values({**row, "staged_at": func.clock_timestamp()}))
