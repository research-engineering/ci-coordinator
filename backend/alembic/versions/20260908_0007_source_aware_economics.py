from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    RevisionDeclaration,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.migration_protocol import apply_forward_declaration
from ci_coordinator.persistence.migration_result_attestation import attest_resulting_capabilities

revision: str = "20260908_0007"
down_revision: str | None = "20260907_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA: Final = "ci_coordinator"
_COLLECTION: Final = "ci_workflow_attempt_collections"
_SNAPSHOT: Final = "ci_workflow_attempt_snapshots"
_DECLARATION: Final = RevisionDeclaration(
    generation=7,
    lineage_id="ci-coordinator-postgresql/v1",
    revision_id=revision,
    parent_revision_id=down_revision,
    transition_kind="expand",
    protocol_version=1,
    capabilities=(
        CapabilityDeclaration(
            "audit-ledger/v1",
            "fe3386e46ad68c320bdf906cc242ddb9d6ad7f3a211ae39550ef9d973dbe0d76",
        ),
        CapabilityDeclaration(
            "ci-economics-evidence/v2",
            "4d0f168ceae527c6445417415fef34e7aaa14e1202c532de8608748567bc1093",
        ),
        CapabilityDeclaration(
            "config-epoch-lifecycle/v1",
            "32b47f8c1cd882db591841227bb567ce85dc7a3f9d2b35fa89b8c00f0c1af23c",
        ),
        CapabilityDeclaration(
            "config-epoch-registration-operations/v1",
            "20ac999afe91574c95e910602f74ff8352b6d5adab31c3f0a7120e14225b178b",
        ),
        CapabilityDeclaration(
            "control-plane-identity-state/v1",
            "3e0eca4e06e8665a4f545fc36fc87f2f5debd205da0a7fac8942a0a2477b8194",
        ),
        CapabilityDeclaration(
            "database-compatibility-protocol/v1",
            "e23360125c88fb7fc1dd9e13ae51f283f7a40e496aee548c71efe51da1a0965f",
        ),
        CapabilityDeclaration(
            "governance-baseline-state/v1",
            "fcd08237e4849e63aae5c24a33ebca0a64812795572a0f708b969e76d225e024",
        ),
        CapabilityDeclaration(
            "operator-override-state/v1",
            "402d4fe7fc73e871d385c9e00cde93c34bf6d5a7fdef6d3f62c27554585148e0",
        ),
        CapabilityDeclaration(
            "production-generation-cutover/v1",
            "6f8c714276233ea0773c60fbe1f03ba7b8de79d5519d632761715e5d1ef13cb0",
        ),
        CapabilityDeclaration(
            "proposal-review-registration/v1",
            "a02e8cb4121864bcc8e497409704c575ce8eb3ba28d8340fec7020048703e0dd",
        ),
        CapabilityDeclaration(
            "runtime-ingress-issuance-state/v2",
            "087559a4e11a31f18b9a3bbe45a93ced7e2af2d9a23205a4795a7eb2c07da533",
        ),
        CapabilityDeclaration(
            "runtime-shadow-reconciliation-state/v2",
            "3470a8d7ed2cb1d2d35e287ac532b1c40d9d8f25962becbc2db4bfe56f4c8af0",
        ),
        CapabilityDeclaration(
            "webhook-body-identity/v1",
            "5f193c2af0d781808fd92df0c172301a741750455114be8faa6be67424d0eee5",
        ),
    ),
    declaration_hash="025868205c9066d54a1e99a38ee194cd685121b1cbed56b55084198c500d455a",
)


def upgrade() -> None:
    _backfill_source_links()
    _replace_source_constraints()
    op.create_index(
        "uq_ci_workflow_attempt_collections_provider_attempt",
        _COLLECTION,
        ["installation_id", "repository_id", "workflow_run_id", "run_attempt"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("source_kind = 'provider_run'"),
    )
    _create_measurement_reports()
    connection = op.get_bind()
    apply_forward_declaration(
        connection,
        load_bundled_profile(),
        previous_revision_id="20260907_0006",
        proposed=_DECLARATION,
        attest_resulting_capabilities=lambda declaration: attest_resulting_capabilities(
            connection, declaration
        ),
    )


def _create_measurement_reports() -> None:
    table = "ci_job_measurement_reports"
    op.create_table(
        table,
        sa.Column("report_id", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("report_digest", sa.String(64), nullable=False),
        sa.Column("producer_claim_hash", sa.String(64), nullable=False),
        sa.Column("provider_binding_digest", sa.String(64), nullable=False),
        sa.Column("payload_canonical", sa.LargeBinary, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("report_id", name="pk_ci_job_measurement_reports"),
        sa.CheckConstraint(
            "report_id ~ '^[0-9a-f]{64}$' AND subject_id ~ '^[0-9a-f]{64}$' AND "
            "report_digest ~ '^[0-9a-f]{64}$' AND producer_claim_hash ~ '^[0-9a-f]{64}$' AND "
            "provider_binding_digest ~ '^[0-9a-f]{64}$'",
            name="ck_ci_job_measurement_reports_digests",
        ),
        sa.CheckConstraint(
            "source_kind = 'provider_run'", name="ck_ci_job_measurement_reports_source"
        ),
        sa.CheckConstraint(
            "octet_length(payload_canonical) BETWEEN 1 AND 131072",
            name="ck_ci_job_measurement_reports_payload",
        ),
        sa.CheckConstraint(
            "received_at < retain_until", name="ck_ci_job_measurement_reports_retention"
        ),
        sa.ForeignKeyConstraint(
            ["subject_id", "retain_until", "source_kind"],
            [
                "ci_coordinator.ci_workflow_attempt_collections.subject_id",
                "ci_coordinator.ci_workflow_attempt_collections.evidence_retain_until",
                "ci_coordinator.ci_workflow_attempt_collections.source_kind",
            ],
            name="fk_ci_job_measurement_reports_source",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_job_measurement_reports_source", table, ["subject_id", "report_id"], schema=_SCHEMA
    )
    op.execute(
        "CREATE TRIGGER tr_ci_job_measurement_reports_retention_guard "
        "BEFORE UPDATE OR DELETE ON ci_coordinator.ci_job_measurement_reports "
        "FOR EACH ROW EXECUTE FUNCTION ci_coordinator.guard_ci_economics_mutation()"
    )
    op.execute(sa.text(f"REVOKE ALL ON TABLE {_SCHEMA}.{table} FROM PUBLIC"))
    op.execute(sa.text(f"REVOKE ALL ON TYPE {_SCHEMA}.{table} FROM PUBLIC"))


def _backfill_source_links() -> None:
    for column in (
        sa.Column("source_kind", sa.String(32), nullable=True),
        sa.Column("legacy_subject_id", sa.String(64), nullable=True),
        sa.Column("installation_id", sa.BigInteger, nullable=True),
        sa.Column("repository_id", sa.BigInteger, nullable=True),
        sa.Column("workflow_run_id", sa.BigInteger, nullable=True),
        sa.Column("run_attempt", sa.BigInteger, nullable=True),
        sa.Column("head_sha", sa.String(40), nullable=True),
        sa.Column("provider_api_version", sa.String(10), nullable=True),
        sa.Column("source_evidence_digest", sa.String(64), nullable=True),
    ):
        op.add_column(_COLLECTION, column, schema=_SCHEMA)
    op.execute(
        "UPDATE ci_coordinator.ci_workflow_attempt_collections "
        "SET source_kind = 'reconciliation', legacy_subject_id = subject_id"
    )
    op.alter_column(_COLLECTION, "source_kind", nullable=False, schema=_SCHEMA)
    op.add_column(_SNAPSHOT, sa.Column("source_kind", sa.String(32)), schema=_SCHEMA)
    # The outer migration fence and transaction exclude runtime writers throughout backfill.
    op.execute(
        "ALTER TABLE ci_coordinator.ci_workflow_attempt_snapshots "
        "DISABLE TRIGGER tr_ci_workflow_attempt_snapshots_retention_guard"
    )
    op.execute(
        "UPDATE ci_coordinator.ci_workflow_attempt_snapshots SET source_kind = 'reconciliation'"
    )
    op.execute(
        "ALTER TABLE ci_coordinator.ci_workflow_attempt_snapshots "
        "ENABLE TRIGGER tr_ci_workflow_attempt_snapshots_retention_guard"
    )
    op.alter_column(_SNAPSHOT, "source_kind", nullable=False, schema=_SCHEMA)
    op.alter_column(_SNAPSHOT, "contract_hash", nullable=True, schema=_SCHEMA)


def _replace_source_constraints() -> None:
    op.drop_constraint("fk_ci_workflow_attempt_collections_subject", _COLLECTION, schema=_SCHEMA)
    op.create_foreign_key(
        "fk_ci_workflow_attempt_collections_legacy_subject",
        _COLLECTION,
        "reconciliation_subjects",
        ["legacy_subject_id"],
        ["subject_id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )
    op.drop_constraint("fk_ci_workflow_attempt_snapshots_collection", _SNAPSHOT, schema=_SCHEMA)
    op.drop_constraint("uq_ci_workflow_attempt_collections_retention", _COLLECTION, schema=_SCHEMA)
    op.create_unique_constraint(
        "uq_ci_workflow_attempt_collections_retention",
        _COLLECTION,
        ["subject_id", "evidence_retain_until", "source_kind"],
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "fk_ci_workflow_attempt_snapshots_collection",
        _SNAPSHOT,
        _COLLECTION,
        ["subject_id", "retain_until", "source_kind"],
        ["subject_id", "evidence_retain_until", "source_kind"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_ci_workflow_attempt_collections_source",
        _COLLECTION,
        "(source_kind = 'reconciliation' AND legacy_subject_id IS NOT NULL AND "
        "legacy_subject_id = subject_id AND installation_id IS NULL AND "
        "repository_id IS NULL AND workflow_run_id IS NULL AND run_attempt IS NULL AND "
        "head_sha IS NULL AND provider_api_version IS NULL AND source_evidence_digest IS NULL) "
        "OR (source_kind = 'provider_run' AND legacy_subject_id IS NULL AND "
        "installation_id IS NOT NULL AND repository_id IS NOT NULL AND "
        "workflow_run_id IS NOT NULL AND run_attempt IS NOT NULL AND head_sha IS NOT NULL AND "
        "provider_api_version IS NOT NULL AND source_evidence_digest IS NOT NULL AND "
        "installation_id BETWEEN 1 AND 9007199254740991 AND "
        "repository_id BETWEEN 1 AND 9007199254740991 AND "
        "workflow_run_id BETWEEN 1 AND 9007199254740991 AND "
        "run_attempt BETWEEN 1 AND 9007199254740991 AND "
        "head_sha ~ '^[0-9a-f]{40}$' AND provider_api_version ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' "
        "AND source_evidence_digest ~ '^[0-9a-f]{64}$')",
        schema=_SCHEMA,
    )
    op.drop_constraint("ck_ci_workflow_attempt_snapshots_hashes", _SNAPSHOT, schema=_SCHEMA)
    op.create_check_constraint(
        "ck_ci_workflow_attempt_snapshots_hashes",
        _SNAPSHOT,
        "subject_id ~ '^[0-9a-f]{64}$' AND head_sha ~ '^[0-9a-f]{40}$' AND "
        "snapshot_digest ~ '^[0-9a-f]{64}$'",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_ci_workflow_attempt_snapshots_source",
        _SNAPSHOT,
        "(source_kind = 'reconciliation' AND contract_hash IS NOT NULL AND "
        "contract_hash ~ '^[0-9a-f]{64}$') OR "
        "(source_kind = 'provider_run' AND contract_hash IS NULL AND planned_route = 'unknown')",
        schema=_SCHEMA,
    )


def downgrade() -> None:
    raise RuntimeError("source-aware CI economics requires forward repair or admitted restore")
