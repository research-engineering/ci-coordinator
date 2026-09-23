from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from ci_coordinator.ci_economics import (
    CollectionClaimAcquired,
    CollectionState,
    acquire_collection_claim,
    complete_collection_claim,
    defer_collection_claim,
    expire_collection_state,
    initial_collection_state,
    reject_collection_claim,
)
from ci_coordinator.ci_economics.collection import CollectionStatus
from ci_coordinator.ci_economics.profile import load_bundled_ci_economics_profile
from ci_coordinator.ci_economics.sources import ReconciliationCollectionSource
from ci_coordinator.persistence import migration_result_attestation
from ci_coordinator.persistence.ci_economics_collection_codec import encode_collection_state
from ci_coordinator.persistence.ci_economics_repository import (
    _provider_snapshot_header,
    _snapshot_job_row,
)
from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_economics_v2_schema_contract import V2_CATALOG
from ci_coordinator.persistence.database_security_attestation import (
    public_access_is_restricted_sync,
)
from ci_coordinator.persistence.reconciliation_subject_codec import (
    decode_subject_row,
    encode_subject_row,
)
from ci_coordinator.persistence.schema import (
    ci_workflow_attempt_collections,
    ci_workflow_attempt_snapshot_jobs,
    ci_workflow_attempt_snapshots,
    reconciliation_subjects,
)
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    load_bundled_shadow_reconciliation_state_profile,
)
from ci_coordinator.reconciliation import initial_convergence_state

from ._ci_economics_support import _CONTRACT, _RECONCILIATION_POLICY, snapshot, subject
from .conftest import alembic_config

pytestmark = pytest.mark.persistence
_PREDECESSOR = "20260906_0005"
_SUCCESSOR = "20260908_0007"
_SOURCE_COLUMNS = {
    "source_kind",
    "legacy_subject_id",
    "installation_id",
    "repository_id",
    "workflow_run_id",
    "run_attempt",
    "head_sha",
    "provider_api_version",
    "source_evidence_digest",
}
_STATUSES: tuple[CollectionStatus, ...] = (
    "pending",
    "leased",
    "deferred",
    "captured",
    "terminal_unavailable",
    "expired",
)


@pytest.mark.parametrize("reject_v2", [False, True], ids=["commit", "rollback-both-revisions"])
def test_source_backfill_is_atomic_and_preserves_every_legacy_state_and_evidence_byte(
    unmigrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    reject_v2: bool,
) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, _PREDECESSOR)
    engine = create_engine(unmigrated_database_url)
    try:
        with engine.begin() as connection:
            now = connection.scalar(text("SELECT statement_timestamp()"))
            assert isinstance(now, datetime)
            for run, status in enumerate(_STATUSES, start=100):
                _seed_legacy(connection, run, status, now)
            for run in (100, 103):
                _seed_legacy(
                    connection, run, "pending", now - timedelta(hours=1), alternate_base=True
                )
            assert ci_economics_schema_matches_contract_sync(connection)
            assert not ci_economics_schema_matches_contract_sync(connection, contract=V2_CATALOG)
            collections = _rows(connection, "ci_workflow_attempt_collections")
            snapshots = _rows(connection, "ci_workflow_attempt_snapshots")
            jobs = _rows(connection, "ci_workflow_attempt_snapshot_jobs")
            reconciliations = _rows(connection, "reconciliation_subjects")
            assert {row["status"] for row in collections} == set(_STATUSES)
            assert len(snapshots) == len(jobs) == 1
            assert len(collections) == len(reconciliations) == len(_STATUSES) + 2
            decoded = [
                decode_subject_row(row, load_bundled_shadow_reconciliation_state_profile())
                for row in reconciliations
            ]
            for run in (100, 103):
                sources = [
                    (identity, convergence)
                    for identity, _contract, _revision, convergence in decoded
                    if identity.workflow_run_id == run
                ]
                assert len(sources) == 2
                assert len({identity.subject_id for identity, _ in sources}) == 2
                assert len({state.created_at for _, state in sources}) == 2
                assert (
                    len({(identity.head_sha, identity.run_attempt) for identity, _ in sources}) == 1
                )

        if reject_v2:
            original_attestor = migration_result_attestation._CAPABILITY_ATTESTORS[
                "ci-economics-evidence/v2"
            ]

            def reject_result(_connection: Connection) -> bool:
                raise RuntimeError("injected v2 attestation rejection")

            monkeypatch.setitem(
                migration_result_attestation._CAPABILITY_ATTESTORS,
                "ci-economics-evidence/v2",
                reject_result,
            )
            with pytest.raises(RuntimeError, match="injected v2 attestation rejection"):
                command.upgrade(config, _SUCCESSOR)
            with engine.connect() as connection:
                assert connection.scalar(
                    text("SELECT version_num FROM public.alembic_version")
                ) == (_PREDECESSOR)
                assert ci_economics_schema_matches_contract_sync(connection)
                assert _rows(connection, "ci_workflow_attempt_collections") == collections
                assert _rows(connection, "ci_workflow_attempt_snapshots") == snapshots
                assert _rows(connection, "ci_workflow_attempt_snapshot_jobs") == jobs
                assert _rows(connection, "reconciliation_subjects") == reconciliations
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM "
                            "ci_coordinator.database_compatibility_declarations "
                            "WHERE generation >= 6"
                        )
                    )
                    == 0
                )
            monkeypatch.setitem(
                migration_result_attestation._CAPABILITY_ATTESTORS,
                "ci-economics-evidence/v2",
                original_attestor,
            )

        command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM public.alembic_version")) == (
                _SUCCESSOR
            )
            assert ci_economics_schema_matches_contract_sync(connection, contract=V2_CATALOG)
            assert not ci_economics_schema_matches_contract_sync(connection)
            assert public_access_is_restricted_sync(connection)
            after = _rows(connection, "ci_workflow_attempt_collections")
            assert len(after) == len(collections)
            for before, saved in zip(collections, after, strict=True):
                assert {key: saved[key] for key in before} == before
                assert set(saved) == set(before) | _SOURCE_COLUMNS
                assert saved["source_kind"] == "reconciliation"
                assert saved["legacy_subject_id"] == before["subject_id"]
                assert all(
                    saved[key] is None
                    for key in _SOURCE_COLUMNS - {"source_kind", "legacy_subject_id"}
                )
            after_snapshots = _rows(connection, "ci_workflow_attempt_snapshots")
            assert after_snapshots == [{**snapshots[0], "source_kind": "reconciliation"}]
            assert _rows(connection, "ci_workflow_attempt_snapshot_jobs") == jobs
            assert _rows(connection, "reconciliation_subjects") == reconciliations
            assert [
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT revision_id, transition_kind FROM "
                        "ci_coordinator.database_compatibility_declarations "
                        "WHERE generation >= 6 ORDER BY generation"
                    )
                )
            ] == [("20260907_0006", "contract"), (_SUCCESSOR, "expand")]
            capabilities = set(
                connection.scalars(
                    text(
                        "SELECT capability_id FROM "
                        "ci_coordinator.database_compatibility_capabilities "
                        "WHERE revision_id = :revision"
                    ),
                    {"revision": _SUCCESSOR},
                )
            )
            assert "ci-economics-evidence/v2" in capabilities
            assert "ci-economics-evidence/v1" not in capabilities

        command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert _rows(connection, "ci_workflow_attempt_collections") == after
            assert _rows(connection, "ci_workflow_attempt_snapshots") == after_snapshots
            assert _rows(connection, "ci_workflow_attempt_snapshot_jobs") == jobs
            assert _rows(connection, "reconciliation_subjects") == reconciliations

        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ci_coordinator.ci_workflow_attempt_snapshots "
                    "SET snapshot_digest = snapshot_digest"
                )
            )
        with pytest.raises(RuntimeError, match="forward repair"):
            command.downgrade(config, _PREDECESSOR)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM public.alembic_version")) == (
                _SUCCESSOR
            )
    finally:
        engine.dispose()


def _seed_legacy(
    connection: Connection,
    run: int,
    status: CollectionStatus,
    now: datetime,
    *,
    alternate_base: bool = False,
) -> None:
    source = ReconciliationCollectionSource(
        subject(run, base_sha=("b" if alternate_base else "a") * 40), _CONTRACT
    )
    anchor = now - timedelta(days=91) if status == "expired" else now - timedelta(seconds=5)
    connection.execute(
        reconciliation_subjects.insert().values(
            encode_subject_row(
                source.subject,
                source.contract,
                initial_convergence_state(anchor, _RECONCILIATION_POLICY),
                load_bundled_shadow_reconciliation_state_profile(),
            )
        )
    )
    state = _state(source, status, anchor)
    connection.execute(
        ci_workflow_attempt_collections.insert().values(encode_collection_state(state))
    )
    if status == "captured":
        evidence = snapshot(source.subject, anchor)
        header = _provider_snapshot_header(evidence, source, state.evidence_retain_until)
        del header["source_kind"]
        connection.execute(ci_workflow_attempt_snapshots.insert().values(header))
        connection.execute(
            ci_workflow_attempt_snapshot_jobs.insert().values(
                _snapshot_job_row(source.source_id, evidence.jobs[0])
            )
        )


def _state(
    source: ReconciliationCollectionSource, status: CollectionStatus, now: datetime
) -> CollectionState:
    policy = load_bundled_ci_economics_profile().collection_policy
    pending = initial_collection_state(source.source_id, now, now, policy)
    if status == "pending":
        return pending
    acquired = acquire_collection_claim(pending, source, worker_id="f" * 64, now=now, policy=policy)
    assert isinstance(acquired, CollectionClaimAcquired)
    if status == "leased":
        return acquired.state
    if status == "deferred":
        result = defer_collection_claim(
            acquired.state, acquired.claim, now, "provider_unavailable", policy
        )
    elif status == "captured":
        result = complete_collection_claim(acquired.state, acquired.claim, now)
    else:
        result = reject_collection_claim(acquired.state, acquired.claim, now)
    assert isinstance(result, CollectionState)
    if status == "expired":
        expired = expire_collection_state(result, result.evidence_retain_until)
        assert isinstance(expired, CollectionState)
        return expired
    return result


def _rows(connection: Connection, table: str) -> list[dict[str, object]]:
    assert table in {
        "ci_workflow_attempt_collections",
        "ci_workflow_attempt_snapshots",
        "ci_workflow_attempt_snapshot_jobs",
        "reconciliation_subjects",
    }
    return [
        dict(row)
        for row in connection.execute(
            text(f"SELECT * FROM ci_coordinator.{table} ORDER BY subject_id")
        ).mappings()
    ]
