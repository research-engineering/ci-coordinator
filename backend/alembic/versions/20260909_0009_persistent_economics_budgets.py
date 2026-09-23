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

revision: str = "20260909_0009"
down_revision: str | None = "20260909_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA: Final = "ci_coordinator"
_DECLARATION: Final = RevisionDeclaration(
    generation=9,
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
            "ci-economics-evidence/v3",
            "cf2b2db745a90355b77a8aea543f6b405a811e32dd6ff9bbc4addd8f7b1521f5",
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
    declaration_hash="d82e85445c5f7c517c4fefb37d90a0f9801c86e754176aaa6ed7f3981b4e60e8",
)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_ci_job_measurement_reports_budget_identity",
        "ci_job_measurement_reports",
        ["report_id", "subject_id", "report_digest", "retain_until"],
        schema=_SCHEMA,
    )
    _create_policies()
    _create_signals()
    for table in ("ci_economics_budget_policies", "ci_economics_budget_signals"):
        op.execute(sa.text(f"REVOKE ALL ON TABLE {_SCHEMA}.{table} FROM PUBLIC"))
        op.execute(sa.text(f"REVOKE ALL ON TYPE {_SCHEMA}.{table} FROM PUBLIC"))
    connection = op.get_bind()
    apply_forward_declaration(
        connection,
        load_bundled_profile(),
        previous_revision_id="20260909_0008",
        proposed=_DECLARATION,
        attest_resulting_capabilities=lambda declaration: attest_resulting_capabilities(
            connection, declaration
        ),
    )


def _create_policies() -> None:
    op.create_table(
        "ci_economics_budget_policies",
        sa.Column("installation_id", sa.BigInteger, nullable=False),
        sa.Column("repository_id", sa.BigInteger, nullable=False),
        sa.Column("policy_key", sa.String(128), nullable=False),
        sa.Column("revision", sa.BigInteger, nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("policy_canonical", sa.LargeBinary, nullable=False),
        sa.PrimaryKeyConstraint(
            "installation_id", "repository_id", "policy_key", name="pk_ci_economics_budget_policies"
        ),
        sa.CheckConstraint(
            "installation_id BETWEEN 1 AND 9007199254740991 AND "
            "repository_id BETWEEN 1 AND 9007199254740991 AND "
            "revision BETWEEN 1 AND 9007199254740991",
            name="ck_ci_economics_budget_policies_integers",
        ),
        sa.CheckConstraint(
            "policy_key ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'",
            name="ck_ci_economics_budget_policies_key",
        ),
        sa.CheckConstraint(
            "policy_digest ~ '^[0-9a-f]{64}$'", name="ck_ci_economics_budget_policies_digest"
        ),
        sa.CheckConstraint(
            "octet_length(policy_canonical) BETWEEN 1 AND 2048",
            name="ck_ci_economics_budget_policies_payload",
        ),
        schema=_SCHEMA,
    )


def _create_signals() -> None:
    table = "ci_economics_budget_signals"
    op.create_table(
        table,
        sa.Column("signal_id", sa.String(64), nullable=False),
        sa.Column("installation_id", sa.BigInteger, nullable=False),
        sa.Column("repository_id", sa.BigInteger, nullable=False),
        sa.Column("policy_key", sa.String(128), nullable=False),
        sa.Column("policy_revision", sa.BigInteger, nullable=False),
        sa.Column("policy_canonical", sa.LargeBinary, nullable=False),
        sa.Column("report_id", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("report_digest", sa.String(64), nullable=False),
        sa.Column("counter", sa.String(16), nullable=False),
        sa.Column("value_us", sa.BigInteger),
        sa.Column("unavailable_reason", sa.String(32)),
        sa.Column("command_exit_code", sa.BigInteger, nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("signal_id", name="pk_ci_economics_budget_signals"),
        sa.UniqueConstraint("report_id", "policy_key", name="uq_ci_economics_budget_signals_slot"),
        sa.CheckConstraint(
            "signal_id ~ '^[0-9a-f]{64}$' AND report_id ~ '^[0-9a-f]{64}$' AND "
            "subject_id ~ '^[0-9a-f]{64}$' AND report_digest ~ '^[0-9a-f]{64}$'",
            name="ck_ci_economics_budget_signals_digests",
        ),
        sa.CheckConstraint(
            "policy_revision BETWEEN 1 AND 9007199254740991 AND "
            "command_exit_code BETWEEN -9007199254740991 AND 9007199254740991",
            name="ck_ci_economics_budget_signals_integers",
        ),
        sa.CheckConstraint(
            "counter IN ('cpu_user', 'cpu_system', 'elapsed')",
            name="ck_ci_economics_budget_signals_counter",
        ),
        sa.CheckConstraint(
            "(value_us IS NOT NULL AND value_us BETWEEN 0 AND 9007199254740991 AND "
            "unavailable_reason IS NULL AND outcome IN ('breached', 'within_budget')) OR "
            "(value_us IS NULL AND unavailable_reason IS NOT NULL AND unavailable_reason IN "
            "('unsupported_platform', 'counter_error', 'out_of_range', 'incomplete_scope') AND "
            "outcome = 'insufficient_evidence')",
            name="ck_ci_economics_budget_signals_measurement",
        ),
        sa.CheckConstraint(
            "octet_length(policy_canonical) BETWEEN 1 AND 2048",
            name="ck_ci_economics_budget_signals_payload",
        ),
        sa.CheckConstraint(
            "received_at < retain_until", name="ck_ci_economics_budget_signals_retention"
        ),
        sa.ForeignKeyConstraint(
            ["installation_id", "repository_id", "policy_key"],
            [
                f"{_SCHEMA}.ci_economics_budget_policies.{name}"
                for name in ("installation_id", "repository_id", "policy_key")
            ],
            name="fk_ci_economics_budget_signals_policy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["report_id", "subject_id", "report_digest", "retain_until"],
            [
                f"{_SCHEMA}.ci_job_measurement_reports.{name}"
                for name in ("report_id", "subject_id", "report_digest", "retain_until")
            ],
            name="fk_ci_economics_budget_signals_report",
            ondelete="CASCADE",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_economics_budget_signals_scope",
        table,
        ["installation_id", "repository_id", sa.text('signal_id COLLATE "C"')],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_economics_budget_signals_policy",
        table,
        [
            "installation_id",
            "repository_id",
            "policy_key",
            "policy_revision",
            sa.text('signal_id COLLATE "C"'),
        ],
        schema=_SCHEMA,
    )
    op.execute(
        "CREATE TRIGGER tr_ci_economics_budget_signals_retention_guard "
        "BEFORE UPDATE OR DELETE ON ci_coordinator.ci_economics_budget_signals "
        "FOR EACH ROW EXECUTE FUNCTION ci_coordinator.guard_ci_economics_mutation()"
    )


def downgrade() -> None:
    raise RuntimeError("budget evidence requires forward repair or admitted restore")
