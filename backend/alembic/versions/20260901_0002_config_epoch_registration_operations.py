"""Add operation-aware config epoch registration receipts."""

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    RevisionDeclaration,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.migration_protocol import (
    apply_forward_declaration,
    validate_pre_retention_downgrade,
)
from ci_coordinator.persistence.migration_result_attestation import (
    attest_resulting_capabilities,
)

revision: str = "20260901_0002"
down_revision: str | None = "20260716_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA: Final = "ci_coordinator"
_SAFE_INTEGER_MAX: Final = 9_007_199_254_740_991
_PREDECESSOR_CAPABILITIES: Final = (
    CapabilityDeclaration(
        "audit-ledger/v1",
        "fe3386e46ad68c320bdf906cc242ddb9d6ad7f3a211ae39550ef9d973dbe0d76",
    ),
    CapabilityDeclaration(
        "config-epoch-lifecycle/v1",
        "32b47f8c1cd882db591841227bb567ce85dc7a3f9d2b35fa89b8c00f0c1af23c",
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
        "proposal-review-registration/v1",
        "a02e8cb4121864bcc8e497409704c575ce8eb3ba28d8340fec7020048703e0dd",
    ),
    CapabilityDeclaration(
        "runtime-ingress-issuance-state/v1",
        "b7d443ade4e57c4d66ee1f36c67dcec829bcc0a1f3bb7ae6547cb9936e64f592",
    ),
    CapabilityDeclaration(
        "runtime-shadow-reconciliation-state/v1",
        "bbc8af8936bb6520a97eeb9c95de04f3a0660122e74710dade78e3791f186703",
    ),
    CapabilityDeclaration(
        "webhook-body-identity/v1",
        "5f193c2af0d781808fd92df0c172301a741750455114be8faa6be67424d0eee5",
    ),
)
_REGISTRATION_CAPABILITY: Final = CapabilityDeclaration(
    "config-epoch-registration-operations/v1",
    "20ac999afe91574c95e910602f74ff8352b6d5adab31c3f0a7120e14225b178b",
)
_PREDECESSOR: Final = RevisionDeclaration(
    generation=1,
    lineage_id="ci-coordinator-postgresql/v1",
    revision_id="20260716_0001",
    parent_revision_id=None,
    transition_kind="bootstrap",
    protocol_version=1,
    capabilities=_PREDECESSOR_CAPABILITIES,
    declaration_hash="bf041595a29ddad2f1ca8acac7a159b42d462ef94a281f7315ed0710911a6d66",
)
_SUCCESSOR: Final = RevisionDeclaration(
    generation=2,
    lineage_id="ci-coordinator-postgresql/v1",
    revision_id=revision,
    parent_revision_id=down_revision,
    transition_kind="expand",
    protocol_version=1,
    capabilities=tuple(sorted((*_PREDECESSOR_CAPABILITIES, _REGISTRATION_CAPABILITY))),
    declaration_hash="5807087ac7f7bdeedb024f0b7c69a910617425882061a398041d01e9ef8e9f8e",
)


def upgrade() -> None:
    op.create_table(
        "config_epoch_registrations",
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("operation_id", sa.String(length=256), nullable=False),
        sa.Column("epoch_id", sa.String(length=64), nullable=False),
        sa.Column("audit_event_id", sa.String(length=38), nullable=False),
        sa.Column("audit_input_hash", sa.LargeBinary(length=32), nullable=False),
        sa.CheckConstraint(
            f"installation_id >= 1 AND installation_id <= {_SAFE_INTEGER_MAX}",
            name="ck_config_epoch_registrations_installation_id_safe",
        ),
        sa.CheckConstraint(
            f"repository_id >= 1 AND repository_id <= {_SAFE_INTEGER_MAX}",
            name="ck_config_epoch_registrations_repository_id_safe",
        ),
        sa.CheckConstraint(
            "octet_length(operation_id) BETWEEN 1 AND 256",
            name="ck_config_epoch_registrations_operation_id_limit",
        ),
        sa.CheckConstraint(
            "epoch_id ~ '^[0-9a-f]{64}$'",
            name="ck_config_epoch_registrations_epoch_id_hash",
        ),
        sa.CheckConstraint(
            "audit_event_id ~ '^audit_[0-9a-f]{32}$' AND octet_length(audit_input_hash) = 32",
            name="ck_config_epoch_registrations_audit_identity",
        ),
        sa.ForeignKeyConstraint(
            ["audit_event_id"],
            [f"{_SCHEMA}.audit_events.audit_event_id"],
            name="fk_config_epoch_registrations_audit_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id", "repository_id", "epoch_id"],
            [
                f"{_SCHEMA}.config_epochs.installation_id",
                f"{_SCHEMA}.config_epochs.repository_id",
                f"{_SCHEMA}.config_epochs.epoch_id",
            ],
            name="fk_config_epoch_registrations_epoch",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "installation_id",
            "repository_id",
            "operation_id",
            name="pk_config_epoch_registrations",
        ),
        sa.UniqueConstraint(
            "audit_event_id",
            name="uq_config_epoch_registrations_audit_event",
        ),
        schema=_SCHEMA,
    )
    op.execute(
        sa.text(
            f"CREATE TRIGGER tr_config_epoch_registrations_immutable "
            f"BEFORE UPDATE OR DELETE ON {_SCHEMA}.config_epoch_registrations "
            f"FOR EACH ROW EXECUTE FUNCTION {_SCHEMA}.reject_config_epoch_mutation()"
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON TABLE {_SCHEMA}.config_epoch_registrations FROM PUBLIC"))
    # PostgreSQL creates a separately privileged composite row type for every table.
    op.execute(sa.text(f"REVOKE ALL ON TYPE {_SCHEMA}.config_epoch_registrations FROM PUBLIC"))
    bind = op.get_bind()
    apply_forward_declaration(
        bind,
        load_bundled_profile(),
        previous_revision_id=_PREDECESSOR.revision_id,
        proposed=_SUCCESSOR,
        attest_resulting_capabilities=lambda declaration: _attest_result(bind, declaration),
    )


def downgrade() -> None:
    bind = op.get_bind()
    validate_pre_retention_downgrade(
        bind,
        load_bundled_profile(),
        target=_PREDECESSOR,
    )
    retained = bind.scalar(
        sa.text("SELECT count(*) FROM ci_coordinator.config_epoch_registrations")
    )
    if retained != 0:
        raise RuntimeError("config registration receipts forbid destructive downgrade")
    op.execute(
        sa.text(
            f"DROP TRIGGER tr_config_epoch_registrations_immutable "
            f"ON {_SCHEMA}.config_epoch_registrations"
        )
    )
    op.drop_table("config_epoch_registrations", schema=_SCHEMA)
    attest_resulting_capabilities(bind, _PREDECESSOR)


def _attest_result(connection: sa.Connection, declaration: RevisionDeclaration) -> None:
    if declaration != _SUCCESSOR:
        raise RuntimeError("config registration migration declaration is not exact")
    attest_resulting_capabilities(connection, declaration)
