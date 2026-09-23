from collections.abc import Sequence
from typing import Final

from alembic import op

from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    RevisionDeclaration,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.migration_protocol import apply_forward_declaration
from ci_coordinator.persistence.migration_result_attestation import attest_resulting_capabilities

revision: str = "20260907_0006"
down_revision: str | None = "20260906_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DECLARATION: Final = RevisionDeclaration(
    generation=6,
    lineage_id="ci-coordinator-postgresql/v1",
    revision_id=revision,
    parent_revision_id=down_revision,
    transition_kind="contract",
    protocol_version=1,
    capabilities=(
        CapabilityDeclaration(
            "audit-ledger/v1",
            "fe3386e46ad68c320bdf906cc242ddb9d6ad7f3a211ae39550ef9d973dbe0d76",
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
    declaration_hash="2b34b03151c3e14fa62b85fd28c2d5c1d26714d7cd0fd075953d08338109d56a",
)


def upgrade() -> None:
    connection = op.get_bind()
    apply_forward_declaration(
        connection,
        load_bundled_profile(),
        previous_revision_id="20260906_0005",
        proposed=_DECLARATION,
        attest_resulting_capabilities=lambda declaration: attest_resulting_capabilities(
            connection, declaration
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "economics capability retirement requires forward repair or admitted restore"
    )
