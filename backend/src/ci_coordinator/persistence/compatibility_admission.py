"""Composition of declared capabilities with independent database facts."""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.activity_schema_attestation import activity_schema_matches_contract
from ci_coordinator.persistence.analytics_purpose_schema_contract import (
    analytics_purpose_schema_matches,
)
from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract,
)
from ci_coordinator.persistence.ci_economics_v2_schema_contract import V2_CATALOG
from ci_coordinator.persistence.ci_economics_v3_schema_contract import V3_CATALOG
from ci_coordinator.persistence.ci_economics_v4_schema_contract import V4_CATALOG
from ci_coordinator.persistence.ci_history_schema_attestation import (
    ci_history_schema_matches_contract,
)
from ci_coordinator.persistence.ci_observation_schema_contract import OBSERVATION_CATALOG
from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    CompatibilityContractError,
)
from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile
from ci_coordinator.persistence.compatibility_repository import (
    CurrentCompatibility,
    admit_current_capabilities,
)
from ci_coordinator.persistence.config_epoch_registration_schema_attestation import (
    config_epoch_registration_schema_matches_contract,
)
from ci_coordinator.persistence.config_epoch_schema_attestation import (
    config_epoch_lifecycle_schema_matches_contract,
)
from ci_coordinator.persistence.data_attestation import (
    application_schema_is_empty_of_public_default_privileges,
    audit_ledger_bridge_constraints_are_validated,
    audit_ledger_seed_is_valid,
)
from ci_coordinator.persistence.errors import (
    DatabaseCapabilityUnavailable,
    DatabaseCompatibilityError,
)
from ci_coordinator.persistence.governance_baseline_schema_attestation import (
    governance_baseline_schema_matches_contract,
)
from ci_coordinator.persistence.principal_attestation import runtime_principal_is_restricted
from ci_coordinator.persistence.production_cutover_schema_attestation import (
    production_cutover_schema_matches_contract,
    runtime_ingress_issuance_v2_schema_matches_contract,
    shadow_reconciliation_v2_schema_matches_contract,
)
from ci_coordinator.persistence.proposal_review_schema_attestation import (
    proposal_review_registration_schema_matches_contract,
)
from ci_coordinator.persistence.repository_attestation_schema_attestation import (
    repository_attestation_schema_matches_contract,
)
from ci_coordinator.persistence.runtime_state_schema_attestation import (
    runtime_ingress_issuance_state_schema_matches_contract,
    webhook_body_identity_schema_matches_contract,
)
from ci_coordinator.persistence.schema_attestation import (
    application_routines_are_security_invoker,
    compatibility_protocol_schema_matches_contract,
    public_access_is_restricted,
    schema_matches_contract,
)
from ci_coordinator.persistence.schema_capabilities import definitions_for
from ci_coordinator.persistence.shadow_reconciliation_principal_attestation import (
    shadow_reconciliation_runtime_principal_is_restricted,
    shadow_reconciliation_v2_runtime_principal_is_restricted,
)
from ci_coordinator.persistence.shadow_reconciliation_state_schema_attestation import (
    shadow_reconciliation_state_schema_matches_contract,
)


async def admit_schema_dependent_operation(
    connection: AsyncConnection,
    profile: CompatibilityProfile,
    required: tuple[CapabilityDeclaration, ...],
) -> CurrentCompatibility:
    """Admit an operation only after declaration, schema, data, and public-ACL facts agree."""
    current = await admit_current_capabilities(connection, profile, required)
    try:
        definitions = definitions_for(required)
    except CompatibilityContractError as error:
        raise DatabaseCapabilityUnavailable(
            "required database capability has no exact local definition"
        ) from error
    capability_ids = {definition.capability_id for definition in definitions}
    try:
        if "ci-analytics-purpose-settings/v1" in capability_ids and not (
            await connection.run_sync(analytics_purpose_schema_matches)
        ):
            raise DatabaseCapabilityUnavailable("analytics purpose schema facts do not match")
        if "administrator-activity/v1" in capability_ids and not (
            await activity_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable("administrator activity schema facts do not match")
        if "audit-ledger/v1" in capability_ids and not await schema_matches_contract(connection):
            raise DatabaseCapabilityUnavailable("audit ledger schema facts do not match")
        if (
            "audit-ledger/v1" in capability_ids
            and not await audit_ledger_bridge_constraints_are_validated(connection)
        ):
            raise DatabaseCapabilityUnavailable(
                "audit ledger data-domain bridge constraints are not validated"
            )
        if "audit-ledger/v1" in capability_ids and not await audit_ledger_seed_is_valid(connection):
            raise DatabaseCapabilityUnavailable("audit ledger seed facts do not match")
        if (
            "database-compatibility-protocol/v1" in capability_ids
            and not await compatibility_protocol_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable("database compatibility schema facts do not match")
        if (
            "config-epoch-lifecycle/v1" in capability_ids
            and not await config_epoch_lifecycle_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable("config epoch lifecycle schema facts do not match")
        if (
            "config-epoch-registration-operations/v1" in capability_ids
            and not await config_epoch_registration_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable(
                "config epoch registration schema facts do not match"
            )
        if (
            "runtime-ingress-issuance-state/v1" in capability_ids
            and not await runtime_ingress_issuance_state_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable(
                "runtime ingress issuance schema facts do not match"
            )
        if (
            "webhook-body-identity/v1" in capability_ids
            and not await webhook_body_identity_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable("webhook body identity schema facts do not match")
        if (
            "runtime-ingress-issuance-state/v2" in capability_ids
            and not await runtime_ingress_issuance_v2_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable(
                "runtime ingress issuance v2 schema facts do not match"
            )
        if (
            "runtime-shadow-reconciliation-state/v2" in capability_ids
            and not await shadow_reconciliation_v2_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable(
                "shadow reconciliation v2 schema facts do not match"
            )
        if (
            "runtime-shadow-reconciliation-state/v2" in capability_ids
            and not await shadow_reconciliation_v2_runtime_principal_is_restricted(connection)
        ):
            raise DatabaseCapabilityUnavailable(
                "shadow reconciliation v2 principal is not restricted"
            )
        if (
            "production-generation-cutover/v1" in capability_ids
            and not await production_cutover_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable("production cutover schema facts do not match")
        if (
            "runtime-shadow-reconciliation-state/v1" in capability_ids
            and not await shadow_reconciliation_state_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable(
                "shadow reconciliation state schema facts do not match"
            )
        if (
            "runtime-shadow-reconciliation-state/v1" in capability_ids
            and not await shadow_reconciliation_runtime_principal_is_restricted(connection)
        ):
            raise DatabaseCapabilityUnavailable(
                "shadow reconciliation runtime principal is not capability-restricted"
            )
        if (
            "proposal-review-registration/v1" in capability_ids
            and not await proposal_review_registration_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable(
                "proposal review registration schema facts do not match"
            )
        if (
            "proposal-review-registration/v1" in capability_ids
            and not await repository_attestation_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable(
                "repository attestation transaction schema facts do not match"
            )
        if (
            "governance-baseline-state/v1" in capability_ids
            and not await governance_baseline_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable("governance baseline schema facts do not match")
        if (
            "ci-economics-evidence/v1" in capability_ids
            and not await ci_economics_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable("CI economics schema facts do not match")
        if "ci-economics-evidence/v2" in capability_ids and not (
            await ci_economics_schema_matches_contract(connection, contract=V2_CATALOG)
        ):
            raise DatabaseCapabilityUnavailable("CI economics schema facts do not match (v2)")
        if "ci-economics-evidence/v3" in capability_ids and not (
            await ci_economics_schema_matches_contract(connection, contract=V3_CATALOG)
        ):
            raise DatabaseCapabilityUnavailable("CI economics schema facts do not match (v3)")
        if "ci-economics-evidence/v4" in capability_ids and not (
            await ci_economics_schema_matches_contract(connection, contract=V4_CATALOG)
        ):
            raise DatabaseCapabilityUnavailable("CI economics schema facts do not match (v4)")
        if "ci-repository-observation/v1" in capability_ids and not (
            await ci_economics_schema_matches_contract(connection, contract=OBSERVATION_CATALOG)
        ):
            raise DatabaseCapabilityUnavailable(
                "CI repository observation schema facts do not match"
            )
        if "ci-actions-history-archive/v1" in capability_ids and not (
            await ci_history_schema_matches_contract(connection)
        ):
            raise DatabaseCapabilityUnavailable("CI actions history schema or default is invalid")
        if not await public_access_is_restricted(connection):
            raise DatabaseCapabilityUnavailable("PUBLIC has application-schema privileges")
        if not await application_routines_are_security_invoker(connection):
            raise DatabaseCapabilityUnavailable(
                "application-schema SECURITY DEFINER routine is forbidden"
            )
        if not await application_schema_is_empty_of_public_default_privileges(connection):
            raise DatabaseCapabilityUnavailable("PUBLIC has application-schema default privileges")
        if not await runtime_principal_is_restricted(connection, profile):
            raise DatabaseCapabilityUnavailable(
                "runtime database principal is not capability-restricted"
            )
    except SQLAlchemyError as error:
        raise DatabaseCompatibilityError(
            "could not collect database capability attestation facts"
        ) from error
    return current
