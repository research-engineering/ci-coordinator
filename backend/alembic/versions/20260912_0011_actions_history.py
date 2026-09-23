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

revision: str = "20260912_0011"
down_revision: str | None = "20260910_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA: Final = "ci_coordinator"
_SCOPE: Final = ("installation_id", "repository_id")
_ATTEMPT: Final = (*_SCOPE, "generation", "workflow_run_id", "run_attempt")
_TABLES: Final = (
    "ci_history_defaults",
    "ci_history_datasets",
    "ci_history_scans",
    "ci_history_attempts",
    "ci_history_jobs",
    "ci_history_details",
    "ci_history_gaps",
    "ci_history_rechecks",
    "ci_history_delivery_inbox",
)
_DECLARATION: Final = RevisionDeclaration(
    generation=11,
    lineage_id="ci-coordinator-postgresql/v1",
    revision_id=revision,
    parent_revision_id=down_revision,
    transition_kind="expand",
    protocol_version=1,
    capabilities=tuple(
        CapabilityDeclaration(capability, digest)
        for capability, digest in (
            ("audit-ledger/v1", "fe3386e46ad68c320bdf906cc242ddb9d6ad7f3a211ae39550ef9d973dbe0d76"),
            (
                "ci-actions-history-archive/v1",
                "ca8c5fee9cb41a354d782619873ec107de4ae57e96835e35970c4ece9e531094",
            ),
            (
                "ci-economics-evidence/v3",
                "cf2b2db745a90355b77a8aea543f6b405a811e32dd6ff9bbc4addd8f7b1521f5",
            ),
            (
                "ci-repository-observation/v1",
                "9bc5774fc83c29990eb49c224e7587320fe75fd4b27665cd50415a0d8b1c8ed2",
            ),
            (
                "config-epoch-lifecycle/v1",
                "32b47f8c1cd882db591841227bb567ce85dc7a3f9d2b35fa89b8c00f0c1af23c",
            ),
            (
                "config-epoch-registration-operations/v1",
                "20ac999afe91574c95e910602f74ff8352b6d5adab31c3f0a7120e14225b178b",
            ),
            (
                "control-plane-identity-state/v1",
                "3e0eca4e06e8665a4f545fc36fc87f2f5debd205da0a7fac8942a0a2477b8194",
            ),
            (
                "database-compatibility-protocol/v1",
                "e23360125c88fb7fc1dd9e13ae51f283f7a40e496aee548c71efe51da1a0965f",
            ),
            (
                "governance-baseline-state/v1",
                "fcd08237e4849e63aae5c24a33ebca0a64812795572a0f708b969e76d225e024",
            ),
            (
                "operator-override-state/v1",
                "402d4fe7fc73e871d385c9e00cde93c34bf6d5a7fdef6d3f62c27554585148e0",
            ),
            (
                "production-generation-cutover/v1",
                "6f8c714276233ea0773c60fbe1f03ba7b8de79d5519d632761715e5d1ef13cb0",
            ),
            (
                "proposal-review-registration/v1",
                "a02e8cb4121864bcc8e497409704c575ce8eb3ba28d8340fec7020048703e0dd",
            ),
            (
                "runtime-ingress-issuance-state/v2",
                "087559a4e11a31f18b9a3bbe45a93ced7e2af2d9a23205a4795a7eb2c07da533",
            ),
            (
                "runtime-shadow-reconciliation-state/v2",
                "3470a8d7ed2cb1d2d35e287ac532b1c40d9d8f25962becbc2db4bfe56f4c8af0",
            ),
            (
                "webhook-body-identity/v1",
                "5f193c2af0d781808fd92df0c172301a741750455114be8faa6be67424d0eee5",
            ),
        )
    ),
    declaration_hash="020daec7a681da9b0a741f109942cf9b9d9d0dfa8f0d728ed5707d305da0aa0c",
)


def upgrade() -> None:
    _create_defaults()
    _create_datasets()
    _create_scans()
    _create_attempts()
    _create_jobs()
    _create_details()
    _create_gaps()
    _create_rechecks()
    _create_delivery_inbox()
    for table in _TABLES:
        op.execute(sa.text(f"REVOKE ALL ON TABLE {_SCHEMA}.{table} FROM PUBLIC"))
        op.execute(sa.text(f"REVOKE ALL ON TYPE {_SCHEMA}.{table} FROM PUBLIC"))
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO ci_coordinator.ci_history_defaults "
            "(singleton, revision, detail_policy_canonical, updated_at) "
            "VALUES (true, 1, convert_to(:policy, 'UTF8'), clock_timestamp())"
        ),
        {"policy": '{"anchor":"first_successful_detail_import","days":365,"mode":"days"}'},
    )
    apply_forward_declaration(
        connection,
        load_bundled_profile(),
        previous_revision_id="20260910_0010",
        proposed=_DECLARATION,
        attest_resulting_capabilities=lambda declaration: attest_resulting_capabilities(
            connection, declaration
        ),
    )


def _create_defaults() -> None:
    table = "ci_history_defaults"
    op.create_table(
        table,
        sa.Column("singleton", sa.Boolean, nullable=False),
        sa.Column("revision", sa.BigInteger, nullable=False),
        sa.Column("detail_policy_canonical", sa.LargeBinary, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("singleton", name=f"pk_{table}"),
        sa.CheckConstraint("singleton IS TRUE", name=f"ck_{table}_singleton"),
        sa.CheckConstraint("revision BETWEEN 1 AND 9007199254740991", name=f"ck_{table}_revision"),
        sa.CheckConstraint(
            "octet_length(detail_policy_canonical) BETWEEN 1 AND 1024", name=f"ck_{table}_payload"
        ),
        schema=_SCHEMA,
    )


def _create_datasets() -> None:
    table = "ci_history_datasets"
    op.create_table(
        table,
        *(
            sa.Column(name, sa.BigInteger, nullable=False)
            for name in (*_SCOPE, "generation", "configuration_revision", "data_revision")
        ),
        sa.Column("configured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("configuration_canonical", sa.LargeBinary, nullable=False),
        *(
            sa.Column(name, sa.BigInteger, nullable=False)
            for name in ("attempt_count", "job_count", "gap_count", "canonical_bytes")
        ),
        sa.PrimaryKeyConstraint(*_SCOPE, name=f"pk_{table}"),
        sa.CheckConstraint(
            "installation_id BETWEEN 1 AND 9007199254740991 AND "
            "repository_id BETWEEN 1 AND 9007199254740991 AND "
            "generation BETWEEN 1 AND 9007199254740991 AND "
            "configuration_revision BETWEEN 1 AND 9007199254740991 AND "
            "data_revision BETWEEN 1 AND 9007199254740991",
            name=f"ck_{table}_identity",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'paused', 'erasing', 'erased')", name=f"ck_{table}_state"
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND 9007199254740991 AND "
            "job_count BETWEEN 0 AND 9007199254740991 AND "
            "gap_count BETWEEN 0 AND 9007199254740991 AND "
            "canonical_bytes BETWEEN 0 AND 9007199254740991",
            name=f"ck_{table}_usage",
        ),
        sa.CheckConstraint(
            "octet_length(configuration_canonical) BETWEEN 1 AND 4096", name=f"ck_{table}_payload"
        ),
        schema=_SCHEMA,
    )


def _create_scans() -> None:
    table = "ci_history_scans"
    op.create_table(
        table,
        *(
            sa.Column(name, sa.BigInteger, nullable=False)
            for name in (*_SCOPE, "generation", "configuration_revision", "revision")
        ),
        sa.Column("lane", sa.String(16), nullable=False),
        sa.Column("state_canonical", sa.LargeBinary, nullable=False),
        sa.Column("traversal_complete", sa.Boolean, nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_worker_id", sa.String(64)),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint(*_SCOPE, "lane", name=f"pk_{table}"),
        sa.CheckConstraint("lane IN ('backfill', 'discovery')", name=f"ck_{table}_lane"),
        _dataset_fk(table),
        sa.CheckConstraint(
            "generation BETWEEN 1 AND 9007199254740991 AND "
            "configuration_revision BETWEEN 1 AND 9007199254740991 AND "
            "revision BETWEEN 1 AND 9007199254740991",
            name=f"ck_{table}_revision",
        ),
        sa.CheckConstraint(
            "octet_length(state_canonical) BETWEEN 1 AND 65536", name=f"ck_{table}_payload"
        ),
        sa.CheckConstraint(
            "(lease_worker_id IS NULL AND lease_token IS NULL AND "
            "lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_worker_id IS NOT NULL AND lease_token IS NOT NULL AND "
            "lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL AND "
            "lease_worker_id ~ '^[0-9a-f]{64}$' AND lease_token ~ '^[0-9a-f]{64}$' AND "
            "lease_acquired_at < lease_expires_at)",
            name=f"ck_{table}_lease",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_history_scans_due",
        table,
        ["lane", "next_attempt_at", *_SCOPE],
        schema=_SCHEMA,
    )


def _create_attempts() -> None:
    table = "ci_history_attempts"
    op.create_table(
        table,
        *(sa.Column(name, sa.BigInteger, nullable=False) for name in _ATTEMPT),
        sa.Column("head_sha", sa.String(40), nullable=False),
        sa.Column("workflow_id", sa.BigInteger, nullable=False),
        sa.Column("run_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("header_canonical", sa.LargeBinary, nullable=False),
        sa.Column("statistics_digest", sa.String(64), nullable=False),
        sa.Column("job_count", sa.BigInteger, nullable=False),
        sa.Column("statistics_bytes", sa.BigInteger, nullable=False),
        sa.Column("has_conflict", sa.Boolean, nullable=False),
        sa.Column("first_imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail_state", sa.String(16), nullable=False),
        sa.Column("detail_first_imported_at", sa.DateTime(timezone=True)),
        sa.Column("detail_policy_canonical", sa.LargeBinary),
        sa.Column("detail_policy_source", sa.String(32)),
        sa.Column("detail_policy_revision", sa.BigInteger),
        sa.Column("detail_expires_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint(*_ATTEMPT, name=f"pk_{table}"),
        _dataset_fk(table),
        sa.CheckConstraint(
            "generation BETWEEN 1 AND 9007199254740991 AND "
            "workflow_run_id BETWEEN 1 AND 9007199254740991 AND "
            "run_attempt BETWEEN 1 AND 9007199254740991 AND "
            "workflow_id BETWEEN 1 AND 9007199254740991 AND job_count BETWEEN 0 AND 2000",
            name=f"ck_{table}_integers",
        ),
        sa.CheckConstraint(
            "head_sha ~ '^[0-9a-f]{40}$' AND statistics_digest ~ '^[0-9a-f]{64}$'",
            name=f"ck_{table}_digests",
        ),
        sa.CheckConstraint(
            "octet_length(header_canonical) BETWEEN 1 AND 8192 AND "
            "statistics_bytes BETWEEN 1 AND 8388608",
            name=f"ck_{table}_payload",
        ),
        sa.CheckConstraint(
            "(detail_state = 'not_imported' AND detail_first_imported_at IS NULL AND "
            "detail_policy_canonical IS NULL AND detail_policy_source IS NULL AND "
            "detail_policy_revision IS NULL AND detail_expires_at IS NULL) OR "
            "(detail_state IN ('retained', 'expired') AND detail_first_imported_at IS NOT NULL AND "
            "detail_policy_canonical IS NOT NULL AND detail_policy_revision IS NOT NULL AND "
            "detail_policy_source IS NOT NULL AND "
            "detail_policy_source IN ('service_default', 'repository_override') AND "
            "detail_policy_revision BETWEEN 1 AND 9007199254740991 AND "
            "octet_length(detail_policy_canonical) BETWEEN 1 AND 1024 AND "
            "(detail_expires_at IS NULL OR detail_expires_at > detail_first_imported_at))",
            name=f"ck_{table}_detail",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_history_attempts_time",
        table,
        [*_SCOPE, "generation", "run_created_at", "workflow_run_id", "run_attempt"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_history_attempts_workflow",
        table,
        [*_SCOPE, "generation", "workflow_id", "run_created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_history_attempts_detail_expiry",
        table,
        [*_SCOPE, "generation", "detail_expires_at"],
        schema=_SCHEMA,
        postgresql_where=sa.text("detail_state = 'retained'"),
    )


def _create_jobs() -> None:
    table = "ci_history_jobs"
    op.create_table(
        table,
        *(
            sa.Column(name, sa.BigInteger, nullable=False)
            for name in (*_ATTEMPT, "provider_job_id")
        ),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("conclusion", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("job_canonical", sa.LargeBinary, nullable=False),
        sa.PrimaryKeyConstraint(*_ATTEMPT, "provider_job_id", name=f"pk_{table}"),
        _attempt_fk(table),
        sa.CheckConstraint(
            "provider_job_id BETWEEN 1 AND 9007199254740991", name=f"ck_{table}_identity"
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 512", name=f"ck_{table}_name"),
        sa.CheckConstraint(
            "conclusion IN ('success', 'failure', 'cancelled', 'timed_out', 'skipped', "
            "'neutral', 'action_required', 'startup_failure', 'stale')",
            name=f"ck_{table}_conclusion",
        ),
        sa.CheckConstraint(
            "octet_length(job_canonical) BETWEEN 1 AND 32768", name=f"ck_{table}_payload"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_history_jobs_name", table, [*_SCOPE, "generation", "name"], schema=_SCHEMA
    )


def _create_details() -> None:
    table = "ci_history_details"
    op.create_table(
        table,
        *(sa.Column(name, sa.BigInteger, nullable=False) for name in _ATTEMPT),
        sa.Column("detail_canonical", sa.LargeBinary, nullable=False),
        sa.PrimaryKeyConstraint(*_ATTEMPT, name=f"pk_{table}"),
        _attempt_fk(table),
        sa.CheckConstraint(
            "octet_length(detail_canonical) BETWEEN 1 AND 8388608", name=f"ck_{table}_payload"
        ),
        schema=_SCHEMA,
    )


def _create_gaps() -> None:
    table = "ci_history_gaps"
    op.create_table(
        table,
        *(sa.Column(name, sa.BigInteger, nullable=False) for name in (*_SCOPE, "generation")),
        sa.Column("gap_id", sa.String(64), nullable=False),
        sa.Column("gap_canonical", sa.LargeBinary, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(*_SCOPE, "generation", "gap_id", name=f"pk_{table}"),
        _dataset_fk(table),
        sa.CheckConstraint(
            "generation BETWEEN 1 AND 9007199254740991 AND gap_id ~ '^[0-9a-f]{64}$'",
            name=f"ck_{table}_identity",
        ),
        sa.CheckConstraint(
            "octet_length(gap_canonical) BETWEEN 1 AND 4096", name=f"ck_{table}_payload"
        ),
        schema=_SCHEMA,
    )


def _create_rechecks() -> None:
    table = "ci_history_rechecks"
    op.create_table(
        table,
        *(
            sa.Column(name, sa.BigInteger, nullable=False)
            for name in (*_SCOPE, "generation", "workflow_run_id", "workflow_id")
        ),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("revision", sa.BigInteger, nullable=False),
        sa.Column("state_canonical", sa.LargeBinary, nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acquisition_count", sa.BigInteger, nullable=False),
        sa.Column("lease_worker_id", sa.String(64)),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint(*_SCOPE, "generation", "workflow_run_id", name=f"pk_{table}"),
        _dataset_fk(table),
        sa.CheckConstraint(
            "generation BETWEEN 1 AND 9007199254740991 AND "
            "workflow_run_id BETWEEN 1 AND 9007199254740991 AND "
            "workflow_id BETWEEN 1 AND 9007199254740991 AND "
            "revision BETWEEN 1 AND 9007199254740991",
            name=f"ck_{table}_identity",
        ),
        sa.CheckConstraint("source IN ('recent', 'repair')", name=f"ck_{table}_source"),
        sa.CheckConstraint(
            "acquisition_count BETWEEN 0 AND 3 AND revision > acquisition_count AND "
            "(acquisition_count < 3 OR lease_expires_at IS NOT NULL)",
            name=f"ck_{table}_attempts",
        ),
        sa.CheckConstraint(
            "octet_length(state_canonical) BETWEEN 1 AND 4096", name=f"ck_{table}_payload"
        ),
        sa.CheckConstraint(
            "(lease_worker_id IS NULL AND lease_token IS NULL AND "
            "lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_worker_id IS NOT NULL AND lease_token IS NOT NULL AND "
            "lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL AND "
            "lease_worker_id ~ '^[0-9a-f]{64}$' AND lease_token ~ '^[0-9a-f]{64}$' AND "
            "acquisition_count >= 1 AND next_attempt_at <= lease_acquired_at AND "
            "lease_expires_at - lease_acquired_at = interval '60 seconds')",
            name=f"ck_{table}_lease",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_history_rechecks_due",
        table,
        ["source", "next_attempt_at", *_SCOPE, "workflow_run_id"],
        schema=_SCHEMA,
    )


def _create_delivery_inbox() -> None:
    table = "ci_history_delivery_inbox"
    op.create_table(
        table,
        sa.Column("delivery_id", sa.String(128), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        *(
            sa.Column(name, sa.BigInteger, nullable=False)
            for name in (*_SCOPE, "workflow_run_id", "run_attempt", "workflow_id")
        ),
        sa.Column("run_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_generation", sa.BigInteger, nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("delivery_id", name=f"pk_{table}"),
        sa.ForeignKeyConstraint(
            ["delivery_id"],
            [f"{_SCHEMA}.ci_workflow_observations.delivery_id"],
            name=f"fk_{table}_source",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "octet_length(delivery_id) BETWEEN 1 AND 128 AND source_fingerprint ~ '^[0-9a-f]{64}$'",
            name=f"ck_{table}_source",
        ),
        sa.CheckConstraint(
            "installation_id BETWEEN 1 AND 9007199254740991 AND "
            "repository_id BETWEEN 1 AND 9007199254740991 AND "
            "workflow_run_id BETWEEN 1 AND 9007199254740991 AND "
            "run_attempt BETWEEN 1 AND 9007199254740991 AND "
            "workflow_id BETWEEN 1 AND 9007199254740991 AND "
            "delivered_generation BETWEEN 0 AND 9007199254740991",
            name=f"ck_{table}_identity",
        ),
        sa.CheckConstraint(
            "source_retain_until = source_recorded_at + INTERVAL '90 days'",
            name=f"ck_{table}_retention",
        ),
        sa.CheckConstraint(
            "(delivered_generation = 0 AND delivered_at IS NULL) OR "
            "(delivered_generation > 0 AND delivered_at IS NOT NULL AND "
            "source_recorded_at <= delivered_at AND delivered_at < source_retain_until)",
            name=f"ck_{table}_receipt",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_history_delivery_inbox_pending",
        table,
        [*_SCOPE, "delivered_generation", "delivery_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_history_delivery_inbox_workflow",
        table,
        [*_SCOPE, "workflow_id", "delivered_generation", "delivery_id"],
        schema=_SCHEMA,
    )


def _dataset_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        _SCOPE,
        [f"{_SCHEMA}.ci_history_datasets.{name}" for name in _SCOPE],
        name=f"fk_{table}_dataset",
        ondelete="RESTRICT",
    )


def _attempt_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        _ATTEMPT,
        [f"{_SCHEMA}.ci_history_attempts.{name}" for name in _ATTEMPT],
        name=f"fk_{table}_attempt",
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    raise RuntimeError("Actions history requires forward repair or admitted restore")
