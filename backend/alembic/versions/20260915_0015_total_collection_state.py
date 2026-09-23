from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ci_coordinator.persistence.activity_capability import ADMINISTRATOR_ACTIVITY
from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_economics_v3_schema_contract import V3_CATALOG
from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    build_declaration,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.migration_protocol import apply_forward_declaration
from ci_coordinator.persistence.migration_result_attestation import attest_resulting_capabilities
from ci_coordinator.persistence.schema_capabilities import (
    ANALYTICS_PURPOSE_SETTINGS,
    CI_ECONOMICS_EVIDENCE_V4,
)

revision: str = "20260915_0015"
down_revision: str | None = "20260915_0014"
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

_TABLE = "ci_coordinator.ci_workflow_attempt_collections"
_PREDICATES = {
    "lease": (
        "(lease_owner_id IS NULL AND lease_token IS NULL AND lease_acquired_at IS NULL AND "
        "lease_expires_at IS NULL) OR (lease_owner_id ~ '^[0-9a-f]{64}$' AND "
        "lease_token ~ '^[0-9a-f]{64}$' AND lease_acquired_at IS NOT NULL AND "
        "lease_expires_at IS NOT NULL AND lease_acquired_at >= source_created_at AND "
        "lease_expires_at > lease_acquired_at AND lease_expires_at < deadline_at)"
    ),
    "state_shape": (
        "(status = 'pending' AND revision = 0 AND attempt_count = 0 AND "
        "next_attempt_at IS NOT NULL AND lease_token IS NULL AND last_failure_reason IS NULL AND "
        "final_outcome IS NULL AND terminal_reason IS NULL AND completed_at IS NULL AND "
        "expired_at IS NULL) OR "
        "(status = 'deferred' AND attempt_count > 0 AND next_attempt_at IS NOT NULL AND "
        "lease_token IS NULL AND last_failure_reason IS NOT NULL AND final_outcome IS NULL AND "
        "terminal_reason IS NULL AND completed_at IS NULL AND expired_at IS NULL) OR "
        "(status = 'leased' AND attempt_count > 0 AND next_attempt_at IS NULL AND "
        "lease_token IS NOT NULL AND final_outcome IS NULL AND terminal_reason IS NULL AND "
        "completed_at IS NULL AND expired_at IS NULL) OR "
        "(status = 'captured' AND next_attempt_at IS NULL AND lease_token IS NULL AND "
        "final_outcome = 'captured' AND terminal_reason IS NULL AND completed_at IS NOT NULL AND "
        "completed_at < evidence_retain_until AND expired_at IS NULL) OR "
        "(status = 'terminal_unavailable' AND next_attempt_at IS NULL AND lease_token IS NULL AND "
        "final_outcome = 'terminal_unavailable' AND terminal_reason IS NOT NULL AND "
        "completed_at IS NOT NULL AND expired_at IS NULL) OR "
        "(status = 'expired' AND next_attempt_at IS NULL AND lease_token IS NULL AND "
        "completed_at IS NOT NULL AND expired_at IS NOT NULL AND "
        "((final_outcome = 'captured' AND terminal_reason IS NULL AND "
        "completed_at < evidence_retain_until) OR "
        "(final_outcome = 'terminal_unavailable' AND terminal_reason IS NOT NULL)))"
    ),
}


def upgrade() -> None:
    connection = op.get_bind()
    if not ci_economics_schema_matches_contract_sync(connection, contract=V3_CATALOG):
        raise RuntimeError("Collection predecessor catalog does not match economics v3")
    invalid = " OR ".join(f"({predicate}) IS NOT TRUE" for predicate in _PREDICATES.values())
    invalid_row = (
        sa.select(sa.literal(1))
        .select_from(sa.table("ci_workflow_attempt_collections", schema="ci_coordinator"))
        .where(sa.text(invalid))
        .limit(1)
    )
    if connection.scalar(invalid_row) is not None:
        raise RuntimeError(
            "Existing collection violates total state admission; "
            "migration rejected without data repair"
        )
    # One ALTER validates both constraints while the outer migration transaction holds the fence.
    changes: list[str] = []
    for suffix, predicate in _PREDICATES.items():
        name = f"ck_ci_workflow_attempt_collections_{suffix}"
        changes.extend(
            (f"DROP CONSTRAINT {name}", f"ADD CONSTRAINT {name} CHECK (({predicate}) IS TRUE)")
        )
    op.execute(sa.text(f"ALTER TABLE {_TABLE} " + ", ".join(changes)))
    profile = load_bundled_profile()
    declaration = build_declaration(
        profile,
        generation=15,
        revision_id=revision,
        parent_revision_id=down_revision,
        transition_kind="expand",
        capabilities=(
            *(CapabilityDeclaration(name, digest) for name, digest in _RETAINED_CAPABILITIES),
            ADMINISTRATOR_ACTIVITY.declaration(),
            ANALYTICS_PURPOSE_SETTINGS.declaration(),
            CI_ECONOMICS_EVIDENCE_V4.declaration(),
        ),
    )
    apply_forward_declaration(
        connection,
        profile,
        previous_revision_id="20260915_0014",
        proposed=declaration,
        attest_resulting_capabilities=lambda admitted: attest_resulting_capabilities(
            connection, admitted
        ),
    )


def downgrade() -> None:
    raise RuntimeError("Total collection admission requires forward repair or admitted restore")
