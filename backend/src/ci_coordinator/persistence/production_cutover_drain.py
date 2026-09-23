from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.production_cutover_state import production_database_now
from ci_coordinator.persistence.reconciliation_state_codec import (
    decode_result_row,
    decode_subject_row,
)
from ci_coordinator.persistence.schema import (
    issued_plan_envelopes,
    reconciliation_results,
    reconciliation_subjects,
)
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    ShadowReconciliationStateProfile,
)

_DRAIN_PAGE_SIZE = 4


async def production_local_drain_complete(
    connection: AsyncConnection,
    *,
    scope: RepositoryScope,
    database_now: datetime,
    profile: ShadowReconciliationStateProfile,
    not_after: datetime,
) -> bool:
    """Caller owns the scope lock and transaction deadline; no skipped locked rows."""
    live_plan = await connection.scalar(
        select(issued_plan_envelopes.c.record_id)
        .where(
            issued_plan_envelopes.c.installation_id == scope.installation_id,
            issued_plan_envelopes.c.repository_id == scope.repository_id,
            issued_plan_envelopes.c.production_admission_authority_id.is_not(None),
            issued_plan_envelopes.c.expires_at > database_now,
        )
        .limit(1)
    )
    if live_plan is not None:
        return False
    subjects = reconciliation_subjects
    results = reconciliation_results
    after = ""
    while True:
        if await production_database_now(connection) >= not_after:
            return False
        query = (
            select(
                subjects,
                results.c.result_canonical_json.label("_result_bytes"),
                results.c.semantic_hash.label("_result_hash"),
                results.c.revision.label("_result_revision"),
            )
            .select_from(
                subjects.outerjoin(
                    results,
                    and_(
                        results.c.subject_id == subjects.c.subject_id,
                        results.c.revision == subjects.c.revision,
                    ),
                )
            )
            .where(
                subjects.c.installation_id == scope.installation_id,
                subjects.c.repository_id == scope.repository_id,
                subjects.c.execution_origin != "full_ci",
                subjects.c.subject_id > after,
            )
            .order_by(subjects.c.subject_id)
            .limit(_DRAIN_PAGE_SIZE)
            .with_for_update(of=subjects)
        )
        page = (await connection.execute(query)).mappings().all()
        for row in page:
            subject, _contract, revision, _convergence = decode_subject_row(dict(row), profile)
            if (
                row["_result_revision"] is None
                or row["lease_token"] is not None
                or row["lease_acquired_at"] is not None
                or row["lease_expires_at"] is not None
            ):
                return False
            result_revision, outcome = decode_result_row(
                {
                    "subject_id": subject.subject_id,
                    "revision": row["_result_revision"],
                    "result_canonical_json": row["_result_bytes"],
                    "semantic_hash": row["_result_hash"],
                },
                subject,
                profile,
            )
            if result_revision != revision or outcome.state not in {
                "success",
                "failure",
                "conflict",
            }:
                return False
            after = subject.subject_id
        if len(page) < _DRAIN_PAGE_SIZE:
            return True
