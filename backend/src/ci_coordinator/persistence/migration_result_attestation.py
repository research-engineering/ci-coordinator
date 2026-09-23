"""Synchronous resulting-capability attestation for Alembic revisions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ci_coordinator.persistence.activity_schema_attestation import (
    activity_schema_matches_contract_sync,
)
from ci_coordinator.persistence.analytics_purpose_schema_contract import (
    analytics_purpose_schema_matches,
)
from ci_coordinator.persistence.audit_schema_attestation import schema_matches_contract_sync
from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_mismatch_details_sync,
)
from ci_coordinator.persistence.ci_economics_schema_contract import (
    V1_CATALOG,
    EconomicsCatalogContract,
)
from ci_coordinator.persistence.ci_economics_v2_schema_contract import V2_CATALOG
from ci_coordinator.persistence.ci_economics_v3_schema_contract import V3_CATALOG
from ci_coordinator.persistence.ci_economics_v4_schema_contract import V4_CATALOG
from ci_coordinator.persistence.ci_history_schema_attestation import history_default_is_valid_sync
from ci_coordinator.persistence.ci_history_schema_contract import HISTORY_CATALOG
from ci_coordinator.persistence.ci_observation_schema_contract import OBSERVATION_CATALOG
from ci_coordinator.persistence.compatibility_contracts import RevisionDeclaration
from ci_coordinator.persistence.compatibility_protocol_attestation import (
    compatibility_protocol_schema_matches_contract_sync,
)
from ci_coordinator.persistence.config_epoch_registration_data_attestation import (
    config_epoch_registration_pairs_match_contract_sync,
)
from ci_coordinator.persistence.config_epoch_registration_schema_attestation import (
    config_epoch_registration_schema_matches_contract_sync,
)
from ci_coordinator.persistence.config_epoch_schema_attestation import (
    config_epoch_lifecycle_schema_matches_contract_sync,
)
from ci_coordinator.persistence.control_plane_identity_schema_attestation import (
    control_plane_identity_schema_matches_contract_sync,
)
from ci_coordinator.persistence.data_attestation import (
    application_schema_is_empty_of_public_default_privileges_sync,
    audit_ledger_bridge_constraints_are_validated_sync,
    audit_ledger_seed_is_valid_sync,
)
from ci_coordinator.persistence.database_security_attestation import (
    application_routines_are_security_invoker_sync,
    public_access_is_restricted_sync,
)
from ci_coordinator.persistence.governance_baseline_schema_attestation import (
    governance_baseline_schema_matches_contract_sync,
)
from ci_coordinator.persistence.operator_override_attestation import (
    operator_override_state_schema_matches_contract_sync,
)
from ci_coordinator.persistence.production_cutover_schema_attestation import (
    production_cutover_schema_mismatch_details_sync,
    runtime_ingress_issuance_v2_schema_matches_contract_sync,
    shadow_reconciliation_v2_schema_matches_contract_sync,
)
from ci_coordinator.persistence.proposal_review_schema_attestation import (
    proposal_review_registration_schema_matches_contract_sync,
)
from ci_coordinator.persistence.repository_attestation_schema_attestation import (
    repository_attestation_schema_mismatches_sync,
)
from ci_coordinator.persistence.runtime_state_schema_attestation import (
    runtime_ingress_issuance_state_schema_matches_contract_sync,
    webhook_body_identity_schema_matches_contract_sync,
)
from ci_coordinator.persistence.schema_capabilities import definitions_for
from ci_coordinator.persistence.shadow_reconciliation_state_schema_attestation import (
    shadow_reconciliation_state_schema_matches_contract_sync,
)

type CapabilityAttestor = Callable[[Connection], bool]


def _audit_ledger_matches(connection: Connection) -> bool:
    return (
        schema_matches_contract_sync(connection)
        and audit_ledger_bridge_constraints_are_validated_sync(connection)
        and audit_ledger_seed_is_valid_sync(connection)
    )


def _operator_override_matches(connection: Connection) -> bool:
    if not operator_override_state_schema_matches_contract_sync(connection):
        return False
    orphaned_audit = connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM ci_coordinator.audit_events AS event "
            "LEFT JOIN ci_coordinator.operator_overrides AS override_state "
            "ON override_state.audit_event_id = event.audit_event_id "
            "WHERE event.event_type = convert_to('operator_override_applied', 'UTF8') "
            "AND override_state.audit_event_id IS NULL)"
        )
    )
    return orphaned_audit is False


def _proposal_review_matches(connection: Connection) -> bool:
    return proposal_review_registration_schema_matches_contract_sync(
        connection
    ) and not repository_attestation_schema_mismatches_sync(connection)


def _config_epoch_registration_matches(connection: Connection) -> bool:
    return config_epoch_registration_schema_matches_contract_sync(
        connection
    ) and config_epoch_registration_pairs_match_contract_sync(connection)


def _ci_economics_matches(
    connection: Connection,
    contract: EconomicsCatalogContract = V1_CATALOG,
    capability_id: str = "ci-economics-evidence/v1",
) -> bool:
    mismatch_details = ci_economics_schema_mismatch_details_sync(connection, contract=contract)
    if mismatch_details:
        components = ",".join(component for component, _detail in mismatch_details)
        details = " | ".join(f"{component}: {detail}" for component, detail in mismatch_details)
        raise RuntimeError(
            "resulting capability does not attest: "
            f"{capability_id}; mismatches={components}; details={details}"
        )
    return True


def _production_cutover_matches(connection: Connection) -> bool:
    details = production_cutover_schema_mismatch_details_sync(connection)
    if details:
        raise RuntimeError(
            "resulting production-generation-cutover/v1 does not attest: "
            + " | ".join(f"{component}: {detail}" for component, detail in details)
        )
    return True


_CAPABILITY_ATTESTORS: Final[dict[str, CapabilityAttestor]] = {
    "ci-analytics-purpose-settings/v1": analytics_purpose_schema_matches,
    "administrator-activity/v1": activity_schema_matches_contract_sync,
    "audit-ledger/v1": _audit_ledger_matches,
    "config-epoch-lifecycle/v1": config_epoch_lifecycle_schema_matches_contract_sync,
    "config-epoch-registration-operations/v1": (_config_epoch_registration_matches),
    "ci-economics-evidence/v1": _ci_economics_matches,
    "ci-economics-evidence/v2": lambda connection: _ci_economics_matches(
        connection, V2_CATALOG, "ci-economics-evidence/v2"
    ),
    "ci-economics-evidence/v3": lambda connection: _ci_economics_matches(
        connection, V3_CATALOG, "ci-economics-evidence/v3"
    ),
    "ci-economics-evidence/v4": lambda connection: _ci_economics_matches(
        connection, V4_CATALOG, "ci-economics-evidence/v4"
    ),
    "ci-repository-observation/v1": lambda connection: _ci_economics_matches(
        connection, OBSERVATION_CATALOG, "ci-repository-observation/v1"
    ),
    "ci-actions-history-archive/v1": lambda connection: (
        _ci_economics_matches(connection, HISTORY_CATALOG, "ci-actions-history-archive/v1")
        and history_default_is_valid_sync(connection)
    ),
    "control-plane-identity-state/v1": control_plane_identity_schema_matches_contract_sync,
    "database-compatibility-protocol/v1": (compatibility_protocol_schema_matches_contract_sync),
    "governance-baseline-state/v1": governance_baseline_schema_matches_contract_sync,
    "operator-override-state/v1": _operator_override_matches,
    "proposal-review-registration/v1": _proposal_review_matches,
    "runtime-ingress-issuance-state/v1": (
        runtime_ingress_issuance_state_schema_matches_contract_sync
    ),
    "runtime-shadow-reconciliation-state/v1": (
        shadow_reconciliation_state_schema_matches_contract_sync
    ),
    "runtime-ingress-issuance-state/v2": runtime_ingress_issuance_v2_schema_matches_contract_sync,
    "runtime-shadow-reconciliation-state/v2": shadow_reconciliation_v2_schema_matches_contract_sync,
    "production-generation-cutover/v1": _production_cutover_matches,
    "webhook-body-identity/v1": webhook_body_identity_schema_matches_contract_sync,
}


def attest_resulting_capabilities(
    connection: Connection,
    declaration: RevisionDeclaration,
) -> None:
    """Reject a revision unless every declared local capability still attests."""
    definitions = definitions_for(declaration.capabilities)
    for definition in definitions:
        attestor = _CAPABILITY_ATTESTORS.get(definition.capability_id)
        if attestor is None:
            raise RuntimeError(
                f"resulting capability lacks an attestor: {definition.capability_id}"
            )
        if not attestor(connection):
            raise RuntimeError(f"resulting capability does not attest: {definition.capability_id}")
    if not public_access_is_restricted_sync(connection):
        raise RuntimeError("resulting application schema grants authority to PUBLIC")
    if not application_routines_are_security_invoker_sync(connection):
        raise RuntimeError("resulting application schema has a SECURITY DEFINER routine")
    if not application_schema_is_empty_of_public_default_privileges_sync(connection):
        raise RuntimeError("resulting application schema has a PUBLIC default privilege")
