"""The two forward-only transitions for explicit immutable review renewal."""

from __future__ import annotations

from sqlalchemy import delete, select, text
from sqlalchemy.engine import Connection

from ci_coordinator.persistence.activity_capability import ADMINISTRATOR_ACTIVITY
from ci_coordinator.persistence.compatibility_contracts import (
    RevisionDeclaration,
    build_declaration,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.errors import DatabaseCompatibilityError
from ci_coordinator.persistence.migration_protocol import (
    _load_declaration,
    apply_forward_declaration,
)
from ci_coordinator.persistence.migration_result_attestation import attest_resulting_capabilities
from ci_coordinator.persistence.proposal_review_schema_attestation import (
    proposal_review_registration_schema_matches_contract_sync,
)
from ci_coordinator.persistence.repository_attestation_schema_attestation import (
    repository_attestation_schema_mismatches_sync,
)
from ci_coordinator.persistence.schema import repository_attestation_transactions
from ci_coordinator.persistence.schema_capabilities import (
    ANALYTICS_PURPOSE_SETTINGS,
    AUDIT_LEDGER,
    CI_ECONOMICS_EVIDENCE_V4,
    CI_HISTORY_ARCHIVE,
    CI_REPOSITORY_OBSERVATION,
    CONFIG_EPOCH_LIFECYCLE,
    CONFIG_EPOCH_LIFECYCLE_V2,
    CONFIG_EPOCH_REGISTRATION_OPERATIONS,
    CONTROL_PLANE_IDENTITY_STATE,
    DATABASE_COMPATIBILITY_PROTOCOL,
    GOVERNANCE_BASELINE_STATE,
    OPERATOR_OVERRIDE_STATE,
    PRODUCTION_GENERATION_CUTOVER,
    PROPOSAL_REVIEW_REGISTRATION,
    PROPOSAL_REVIEW_REGISTRATION_V2,
    RUNTIME_INGRESS_ISSUANCE_STATE_V2,
    SHADOW_RECONCILIATION_STATE_V2,
    WEBHOOK_BODY_IDENTITY,
)

_RETAINED = (
    ADMINISTRATOR_ACTIVITY,
    ANALYTICS_PURPOSE_SETTINGS,
    AUDIT_LEDGER,
    CI_ECONOMICS_EVIDENCE_V4,
    CI_HISTORY_ARCHIVE,
    CI_REPOSITORY_OBSERVATION,
    CONFIG_EPOCH_REGISTRATION_OPERATIONS,
    CONTROL_PLANE_IDENTITY_STATE,
    DATABASE_COMPATIBILITY_PROTOCOL,
    GOVERNANCE_BASELINE_STATE,
    OPERATOR_OVERRIDE_STATE,
    PRODUCTION_GENERATION_CUTOVER,
    RUNTIME_INGRESS_ISSUANCE_STATE_V2,
    SHADOW_RECONCILIATION_STATE_V2,
    WEBHOOK_BODY_IDENTITY,
)


def renewal_declarations() -> tuple[RevisionDeclaration, RevisionDeclaration, RevisionDeclaration]:
    profile = load_bundled_profile()
    retained = tuple(definition.declaration() for definition in _RETAINED)
    previous = build_declaration(
        profile,
        generation=15,
        revision_id="20260915_0015",
        parent_revision_id="20260915_0014",
        transition_kind="expand",
        capabilities=(
            *retained,
            CONFIG_EPOCH_LIFECYCLE.declaration(),
            PROPOSAL_REVIEW_REGISTRATION.declaration(),
        ),
    )
    contracted = build_declaration(
        profile,
        generation=16,
        revision_id="20260926_0016",
        parent_revision_id=previous.revision_id,
        transition_kind="contract",
        capabilities=retained,
    )
    expanded = build_declaration(
        profile,
        generation=17,
        revision_id="20260926_0017",
        parent_revision_id=contracted.revision_id,
        transition_kind="expand",
        capabilities=(
            *retained,
            CONFIG_EPOCH_LIFECYCLE_V2.declaration(),
            PROPOSAL_REVIEW_REGISTRATION_V2.declaration(),
        ),
    )
    return previous, contracted, expanded


def retire_pending_review_authority(connection: Connection) -> None:
    previous, contracted, _ = renewal_declarations()
    profile = load_bundled_profile()
    _require_predecessor(connection, previous)
    first_application = _load_declaration(connection, profile, contracted.revision_id) is None

    def attest(declaration: RevisionDeclaration) -> None:
        if repository_attestation_schema_mismatches_sync(
            connection
        ) or not proposal_review_registration_schema_matches_contract_sync(connection):
            raise DatabaseCompatibilityError("review cutoff predecessor schema does not match")
        if first_application:
            connection.execute(delete(repository_attestation_transactions))
            if (
                connection.scalar(select(repository_attestation_transactions).exists().select())
                is not False
            ):
                raise DatabaseCompatibilityError("review cutoff retained a pending challenge")
        attest_resulting_capabilities(connection, declaration)

    apply_forward_declaration(
        connection,
        profile,
        previous_revision_id=previous.revision_id,
        proposed=contracted,
        attest_resulting_capabilities=attest,
    )


def expand_explicit_review_authority(connection: Connection) -> None:
    _, contracted, expanded = renewal_declarations()
    profile = load_bundled_profile()
    _require_predecessor(connection, contracted)
    first_application = _load_declaration(connection, profile, expanded.revision_id) is None

    def attest(declaration: RevisionDeclaration) -> None:
        if first_application:
            if not proposal_review_registration_schema_matches_contract_sync(connection):
                raise DatabaseCompatibilityError("review renewal predecessor schema does not match")
            connection.execute(
                text(
                    "ALTER TABLE ci_coordinator.workflow_proposal_reviews "
                    "DROP CONSTRAINT uq_workflow_proposal_reviews_scope_manifest"
                )
            )
        attest_resulting_capabilities(connection, declaration)

    apply_forward_declaration(
        connection,
        profile,
        previous_revision_id=contracted.revision_id,
        proposed=expanded,
        attest_resulting_capabilities=attest,
    )


def _require_predecessor(connection: Connection, expected: RevisionDeclaration) -> None:
    actual = _load_declaration(connection, load_bundled_profile(), expected.revision_id)
    if actual != expected:
        raise DatabaseCompatibilityError(
            "review renewal requires its exact predecessor declaration"
        )
