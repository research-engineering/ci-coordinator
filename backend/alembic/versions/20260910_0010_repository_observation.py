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

revision: str = "20260910_0010"
down_revision: str | None = "20260909_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA: Final = "ci_coordinator"
_DECLARATION: Final = RevisionDeclaration(
    generation=10,
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
            "ci-repository-observation/v1",
            "9bc5774fc83c29990eb49c224e7587320fe75fd4b27665cd50415a0d8b1c8ed2",
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
    declaration_hash="63010cd9533faa05ec6fa18ea6bc1d2e8a439e122d04116206ce1e6ccc96629a",
)


def upgrade() -> None:
    _create_subscriptions()
    _create_scans()
    _create_gaps()
    for table in ("ci_observation_subscriptions", "ci_observation_scans", "ci_observation_gaps"):
        op.execute(sa.text(f"REVOKE ALL ON TABLE {_SCHEMA}.{table} FROM PUBLIC"))
        op.execute(sa.text(f"REVOKE ALL ON TYPE {_SCHEMA}.{table} FROM PUBLIC"))
    connection = op.get_bind()
    apply_forward_declaration(
        connection,
        load_bundled_profile(),
        previous_revision_id="20260909_0009",
        proposed=_DECLARATION,
        attest_resulting_capabilities=lambda declaration: attest_resulting_capabilities(
            connection, declaration
        ),
    )


def _create_subscriptions() -> None:
    table = "ci_observation_subscriptions"
    op.create_table(
        table,
        sa.Column("installation_id", sa.BigInteger, nullable=False),
        sa.Column("repository_id", sa.BigInteger, nullable=False),
        sa.Column("revision", sa.BigInteger, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("snapshot_digest", sa.String(64), nullable=False),
        sa.Column("snapshot_canonical", sa.LargeBinary, nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("preferred_lane", sa.String(8), nullable=False),
        sa.Column("detail_truncated_until", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("installation_id", "repository_id", name=f"pk_{table}"),
        sa.CheckConstraint(
            "installation_id BETWEEN 1 AND 9007199254740991 AND "
            "repository_id BETWEEN 1 AND 9007199254740991 AND "
            "revision BETWEEN 1 AND 9007199254740991",
            name=f"ck_{table}_integers",
        ),
        sa.CheckConstraint("snapshot_digest ~ '^[0-9a-f]{64}$'", name=f"ck_{table}_digest"),
        sa.CheckConstraint(
            "octet_length(snapshot_canonical) BETWEEN 1 AND 2048", name=f"ck_{table}_payload"
        ),
        sa.CheckConstraint("preferred_lane IN ('recent', 'backfill')", name=f"ck_{table}_lane"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_observation_subscriptions_due",
        table,
        ["enabled", "next_attempt_at", "installation_id", "repository_id"],
        schema=_SCHEMA,
    )


def _create_scans() -> None:
    table = "ci_observation_scans"
    op.create_table(
        table,
        sa.Column("installation_id", sa.BigInteger, nullable=False),
        sa.Column("repository_id", sa.BigInteger, nullable=False),
        sa.Column("lane", sa.String(8), nullable=False),
        sa.Column("config_revision", sa.BigInteger, nullable=False),
        sa.Column("revision", sa.BigInteger, nullable=False),
        sa.Column("state_canonical", sa.LargeBinary, nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_completed_through", sa.DateTime(timezone=True)),
        sa.Column("last_page_at", sa.DateTime(timezone=True)),
        sa.Column("pages_seen", sa.BigInteger, nullable=False),
        sa.Column("sources_registered", sa.BigInteger, nullable=False),
        sa.Column("last_outcome", sa.String(32)),
        sa.PrimaryKeyConstraint("installation_id", "repository_id", "lane", name=f"pk_{table}"),
        sa.ForeignKeyConstraint(
            ["installation_id", "repository_id"],
            [
                f"{_SCHEMA}.ci_observation_subscriptions.{name}"
                for name in ("installation_id", "repository_id")
            ],
            name=f"fk_{table}_subscription",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("lane IN ('recent', 'backfill')", name=f"ck_{table}_lane"),
        sa.CheckConstraint(
            "config_revision BETWEEN 1 AND 9007199254740991 AND "
            "revision BETWEEN 1 AND 9007199254740991 AND "
            "pages_seen BETWEEN 0 AND 9007199254740991 AND "
            "sources_registered BETWEEN 0 AND 9007199254740991",
            name=f"ck_{table}_integers",
        ),
        sa.CheckConstraint(
            "octet_length(state_canonical) BETWEEN 1 AND 2048", name=f"ck_{table}_payload"
        ),
        sa.CheckConstraint(
            "last_outcome IS NULL OR last_outcome IN "
            "('page_recorded', 'capacity_reached', 'provider_unavailable', "
            "'provider_binding_mismatch', 'provider_malformed', 'provider_incomplete', "
            "'provider_not_terminal', 'provider_unstable', 'access_unavailable', 'timed_out')",
            name=f"ck_{table}_outcome",
        ),
        sa.CheckConstraint(
            "last_completed_through IS NULL OR "
            "last_completed_through = date_trunc('second', last_completed_through)",
            name=f"ck_{table}_completed_precision",
        ),
        schema=_SCHEMA,
    )


def _create_gaps() -> None:
    table = "ci_observation_gaps"
    op.create_table(
        table,
        sa.Column("gap_id", sa.String(64), nullable=False),
        sa.Column("installation_id", sa.BigInteger, nullable=False),
        sa.Column("repository_id", sa.BigInteger, nullable=False),
        sa.Column("gap_canonical", sa.LargeBinary, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("gap_id", name=f"pk_{table}"),
        sa.ForeignKeyConstraint(
            ["installation_id", "repository_id"],
            [
                f"{_SCHEMA}.ci_observation_subscriptions.{name}"
                for name in ("installation_id", "repository_id")
            ],
            name=f"fk_{table}_subscription",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("gap_id ~ '^[0-9a-f]{64}$'", name=f"ck_{table}_digest"),
        sa.CheckConstraint(
            "octet_length(gap_canonical) BETWEEN 1 AND 2048", name=f"ck_{table}_payload"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_observation_gaps_scope",
        table,
        ["installation_id", "repository_id", sa.text('gap_id COLLATE "C"')],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ci_observation_gaps_expiry",
        table,
        ["installation_id", "repository_id", "expires_at"],
        schema=_SCHEMA,
    )


def downgrade() -> None:
    raise RuntimeError("repository observation requires forward repair or admitted restore")
