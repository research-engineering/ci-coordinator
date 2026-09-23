"""SQL statement ownership for one exclusive due-subject claim."""

from __future__ import annotations

from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql import Executable

from ci_coordinator.persistence.schema import reconciliation_results, reconciliation_subjects


def next_reconciliation_claim_statement() -> Executable:
    """Select one unresolved due row under a skip-locked write lock."""
    now = func.statement_timestamp()
    current_result = reconciliation_results.alias("current_reconciliation_result")
    return (
        select(reconciliation_subjects)
        .select_from(
            reconciliation_subjects.outerjoin(
                current_result,
                and_(
                    current_result.c.subject_id == reconciliation_subjects.c.subject_id,
                    current_result.c.revision == reconciliation_subjects.c.revision,
                ),
            )
        )
        .where(
            current_result.c.subject_id.is_(None),
            reconciliation_subjects.c.next_attempt_at <= now,
            or_(
                reconciliation_subjects.c.lease_token.is_(None),
                reconciliation_subjects.c.lease_expires_at <= now,
            ),
        )
        .order_by(
            reconciliation_subjects.c.next_attempt_at,
            reconciliation_subjects.c.subject_id,
        )
        .limit(1)
        .with_for_update(of=reconciliation_subjects, skip_locked=True)
    )
