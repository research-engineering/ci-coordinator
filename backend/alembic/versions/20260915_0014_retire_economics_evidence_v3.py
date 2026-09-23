from collections.abc import Sequence

from alembic import op

from ci_coordinator.persistence.activity_capability import ADMINISTRATOR_ACTIVITY
from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    build_declaration,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.migration_protocol import apply_forward_declaration
from ci_coordinator.persistence.migration_result_attestation import attest_resulting_capabilities
from ci_coordinator.persistence.schema_capabilities import ANALYTICS_PURPOSE_SETTINGS

revision: str = "20260915_0014"
down_revision: str | None = "20260913_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_RETAINED_CAPABILITIES = (
    ("audit-ledger/v1", "fe3386e46ad68c320bdf906cc242ddb9d6ad7f3a211ae39550ef9d973dbe0d76"),
    (
        "ci-actions-history-archive/v1",
        "ca8c5fee9cb41a354d782619873ec107de4ae57e96835e35970c4ece9e531094",
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


def upgrade() -> None:
    connection = op.get_bind()
    profile = load_bundled_profile()
    declaration = build_declaration(
        profile,
        generation=14,
        revision_id=revision,
        parent_revision_id=down_revision,
        transition_kind="contract",
        capabilities=(
            *(CapabilityDeclaration(name, digest) for name, digest in _RETAINED_CAPABILITIES),
            ADMINISTRATOR_ACTIVITY.declaration(),
            ANALYTICS_PURPOSE_SETTINGS.declaration(),
        ),
    )
    apply_forward_declaration(
        connection,
        profile,
        previous_revision_id="20260913_0013",
        proposed=declaration,
        attest_resulting_capabilities=lambda admitted: attest_resulting_capabilities(
            connection, admitted
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Collection capability retirement requires forward repair or admitted restore"
    )
