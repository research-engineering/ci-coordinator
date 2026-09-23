from __future__ import annotations

from typing import cast

from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Dialect
from sqlalchemy.sql.elements import ClauseElement

from ci_coordinator.persistence.reconciliation_queries import (
    next_reconciliation_claim_statement,
)


def test_claim_query_is_due_terminal_free_and_exclusively_skip_locked() -> None:
    dialect_factory = cast(type[Dialect], postgresql.dialect)
    sql = str(
        cast(ClauseElement, next_reconciliation_claim_statement()).compile(
            dialect=dialect_factory(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "LEFT OUTER JOIN ci_coordinator.reconciliation_results" in sql
    assert "current_reconciliation_result.subject_id IS NULL" in sql
    assert "reconciliation_subjects.next_attempt_at <=" in sql
    assert "reconciliation_subjects.lease_token IS NULL" in sql
    assert "reconciliation_subjects.lease_expires_at <=" in sql
    assert "ORDER BY ci_coordinator.reconciliation_subjects.next_attempt_at" in sql
    assert "ci_coordinator.reconciliation_subjects.subject_id" in sql
    assert "LIMIT 1" in sql
    assert "FOR UPDATE OF reconciliation_subjects SKIP LOCKED" in sql
    assert "reconciliation_subjects.next_attempt_at <= statement_timestamp()" in sql
    assert "reconciliation_subjects.lease_expires_at <= statement_timestamp()" in sql
