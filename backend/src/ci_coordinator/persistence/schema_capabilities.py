"""Pure capability definitions required by schema-dependent operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256

from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    CompatibilityContractError,
    required_capabilities,
)
from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    capability_id: str
    schema_requirements: tuple[str, ...]
    read_domain_id: str
    write_domain_id: str
    codec_id: str
    backfill_state_id: str
    bridge_constraint_ids: tuple[str, ...]
    routine_requirements: tuple[str, ...]
    privilege_requirements: tuple[str, ...]

    def declaration(self) -> CapabilityDeclaration:
        return CapabilityDeclaration(
            capability_id=self.capability_id,
            descriptor_hash=capability_descriptor_hash(self),
        )


def capability_descriptor_hash(definition: CapabilityDefinition) -> str:
    projection = {
        "capabilityId": definition.capability_id,
        "schemaRequirements": list(definition.schema_requirements),
        "readDomainId": definition.read_domain_id,
        "writeDomainId": definition.write_domain_id,
        "codecId": definition.codec_id,
        "backfillStateId": definition.backfill_state_id,
        "bridgeConstraintIds": list(definition.bridge_constraint_ids),
        "routineRequirements": list(definition.routine_requirements),
        "privilegeRequirements": list(definition.privilege_requirements),
    }
    return sha256(b"ci-database-capability/v1\0" + canonical_json(projection)).hexdigest()


ADMINISTRATOR_ACTIVITY = CapabilityDefinition(
    capability_id="administrator-activity/v1",
    schema_requirements=(
        "ci_coordinator.activity_events/v1",
        "ci_coordinator.activity_diagnostic_buckets/v1",
    ),
    read_domain_id="bounded-authorized-activity/v1",
    write_domain_id="atomic-session-security-journal/v1",
    codec_id="token-free-activity/v1",
    backfill_state_id="not-applicable/v1",
    bridge_constraint_ids=("ci_coordinator.ck_activity_outcome/v1",),
    routine_requirements=(),
    privilege_requirements=("runtime-activity-insert-select-retention/v1",),
)


DATABASE_COMPATIBILITY_PROTOCOL = CapabilityDefinition(
    capability_id="database-compatibility-protocol/v1",
    schema_requirements=(
        "ci_coordinator.database_compatibility_declarations/v1",
        "ci_coordinator.database_compatibility_capabilities/v1",
    ),
    read_domain_id="database-compatibility-declaration/v1",
    write_domain_id="migration-principal-only/v1",
    codec_id="canonical-json-sha256/v1",
    backfill_state_id="not-applicable/v1",
    bridge_constraint_ids=(),
    routine_requirements=("ci_coordinator.reject_compatibility_mutation/v1",),
    privilege_requirements=("runtime-select-declarations-only/v1",),
)

AUDIT_LEDGER = CapabilityDefinition(
    capability_id="audit-ledger/v1",
    schema_requirements=(
        "ci_coordinator.audit_events/v1",
        "ci_coordinator.audit_ledger_head/v1",
    ),
    read_domain_id="ci-audit-event/v1",
    write_domain_id="ci-audit-event/v1",
    codec_id="ci-audit-event-canonical-json/v1",
    backfill_state_id="complete/v1",
    bridge_constraint_ids=(
        "ci_coordinator.ck_audit_events_payload_canonical_json_byte_limit/v1",
        "ci_coordinator.ck_audit_events_scope_shape/v1",
    ),
    routine_requirements=(),
    privilege_requirements=("runtime-audit-ledger-dml/v1",),
)

CONFIG_EPOCH_LIFECYCLE = CapabilityDefinition(
    capability_id="config-epoch-lifecycle/v1",
    schema_requirements=(
        "ci_coordinator.config_epochs/v1",
        "ci_coordinator.active_config_epochs/v1",
        "ci_coordinator.config_epoch_activations/v1",
    ),
    read_domain_id="config-epoch-draft/v1",
    write_domain_id="config-epoch-lifecycle/v1",
    codec_id="repository-attested-config-activation-audit/v1",
    backfill_state_id="not-applicable/v1",
    bridge_constraint_ids=(),
    routine_requirements=("ci_coordinator.reject_config_epoch_mutation/v1",),
    privilege_requirements=("runtime-config-epoch-lifecycle-dml/v1",),
)

CONFIG_EPOCH_REGISTRATION_OPERATIONS = CapabilityDefinition(
    capability_id="config-epoch-registration-operations/v1",
    schema_requirements=("ci_coordinator.config_epoch_registrations/v1",),
    read_domain_id="config-epoch-registration-record/v1",
    write_domain_id="append-only-config-epoch-registration-pair/v1",
    codec_id="config-epoch-registration-audit/v1",
    backfill_state_id="not-applicable/v1",
    bridge_constraint_ids=(
        "ci_coordinator.ck_config_epoch_registrations_audit_identity/v1",
        "ci_coordinator.ck_config_epoch_registrations_epoch_id_hash/v1",
    ),
    routine_requirements=("ci_coordinator.reject_config_epoch_mutation/v1",),
    privilege_requirements=("runtime-config-epoch-registration-column-dml/v1",),
)

OPERATOR_OVERRIDE_STATE = CapabilityDefinition(
    capability_id="operator-override-state/v1",
    schema_requirements=("ci_coordinator.operator_overrides/v1",),
    read_domain_id="active-operator-override-resolution/v1",
    write_domain_id="append-only-operator-override/v1",
    codec_id="operator-override-canonical-json/v1",
    backfill_state_id="empty-ledger-precondition/v1",
    bridge_constraint_ids=(
        "ci_coordinator.ck_operator_overrides_record_byte_limit/v1",
        "ci_coordinator.ck_operator_overrides_scope_safe/v1",
    ),
    routine_requirements=(),
    privilege_requirements=("runtime-operator-override-column-dml/v1",),
)

PROPOSAL_REVIEW_REGISTRATION = CapabilityDefinition(
    capability_id="proposal-review-registration/v1",
    schema_requirements=(
        "ci_coordinator.repository_attestation_transactions/v1",
        "ci_coordinator.workflow_proposal_reviews/v1",
    ),
    read_domain_id="workflow-proposal-review/v1",
    write_domain_id="one-use-repository-attestation-review-and-activation-guard/v1",
    codec_id="repository-attested-proposal-review-and-activation-authority/v1",
    backfill_state_id="not-applicable/v1",
    bridge_constraint_ids=(
        "ci_coordinator.ck_workflow_proposal_reviews_base_shape/v1",
        "ci_coordinator.ck_workflow_proposal_reviews_diff_evidence/v1",
        "ci_coordinator.ck_workflow_proposal_reviews_attestation_identity/v1",
        "ci_coordinator.ck_workflow_proposal_reviews_attestation_time/v1",
    ),
    routine_requirements=("ci_coordinator.reject_proposal_review_mutation/v1",),
    privilege_requirements=("runtime-repository-attestation-review-dml/v1",),
)

GOVERNANCE_BASELINE_STATE = CapabilityDefinition(
    capability_id="governance-baseline-state/v1",
    schema_requirements=(
        "ci_coordinator.governance_baselines/v1",
        "ci_coordinator.governance_baseline_operations/v1",
    ),
    read_domain_id="governance-baseline-record/v1",
    write_domain_id="append-only-governance-baseline-operation/v1",
    codec_id="governance-state-and-command-canonical-json/v1",
    backfill_state_id="not-applicable/v1",
    bridge_constraint_ids=(
        "ci_coordinator.ck_governance_baselines_chain_shape/v1",
        "ci_coordinator.ck_governance_baselines_state_bytes/v1",
        "ci_coordinator.ck_governance_baselines_time_order/v1",
        "ci_coordinator.ck_governance_baseline_operations_result/v1",
    ),
    routine_requirements=("ci_coordinator.reject_governance_baseline_mutation/v1",),
    privilege_requirements=("runtime-governance-baseline-select-insert/v1",),
)

CONTROL_PLANE_IDENTITY_STATE = CapabilityDefinition(
    capability_id="control-plane-identity-state/v1",
    schema_requirements=(
        "ci_coordinator.control_plane_sessions/v1",
        "ci_coordinator.control_plane_logout_replays/v1",
    ),
    read_domain_id="token-free-control-plane-identity-state/v1",
    write_domain_id="bounded-session-and-atomic-logout-replay/v1",
    codec_id="control-plane-session-role-bitset/v1",
    backfill_state_id="not-applicable/v1",
    bridge_constraint_ids=(
        "ci_coordinator.ck_control_plane_sessions_actor_id/v1",
        "ci_coordinator.ck_control_plane_sessions_roles/v1",
        "ci_coordinator.ck_control_plane_sessions_time_order/v1",
        "ci_coordinator.ck_control_plane_logout_replays_retain_until/v1",
    ),
    routine_requirements=("ci_coordinator.reject_control_plane_session_mutation/v1",),
    privilege_requirements=("runtime-control-plane-identity-bounded-lock-dml/v1",),
)

RUNTIME_INGRESS_ISSUANCE_STATE = CapabilityDefinition(
    capability_id="runtime-ingress-issuance-state/v1",
    schema_requirements=(
        "ci_coordinator.webhook_deliveries/v1",
        "ci_coordinator.issued_plan_envelopes/v1",
        "ci_coordinator.production_admission_authorities/v1",
        "ci_coordinator.production_admission_scope_bindings/v1",
    ),
    read_domain_id="runtime-ingress-issuance-state/v1",
    write_domain_id="runtime-ingress-issuance-state/v1",
    codec_id="runtime-ingress-issuance-canonical-json/v1",
    backfill_state_id="not-applicable/v1",
    bridge_constraint_ids=(
        "ci_coordinator.ck_webhook_deliveries_delivery_id_byte_limit/v1",
        "ci_coordinator.ck_issued_plan_envelopes_envelope_byte_limit/v1",
        "ci_coordinator.ck_issued_plan_envelopes_scope_safe/v1",
        "ci_coordinator.ck_production_admission_authorities_identity/v1",
        "ci_coordinator.ck_production_admission_authorities_envelope_byte_limit/v1",
        "ci_coordinator.ck_production_admission_scope_bindings_scope_safe/v1",
    ),
    routine_requirements=(),
    privilege_requirements=("runtime-ingress-issuance-state-dml/v1",),
)

WEBHOOK_BODY_IDENTITY = CapabilityDefinition(
    capability_id="webhook-body-identity/v1",
    schema_requirements=("ci_coordinator.webhook_deliveries/v1",),
    read_domain_id="authenticated-webhook-body-sha256/v1",
    write_domain_id="unique-authenticated-webhook-body-claim/v1",
    codec_id="lowercase-sha256-hex/v1",
    backfill_state_id="complete/v1",
    bridge_constraint_ids=(
        "ci_coordinator.ck_webhook_deliveries_body_sha256/v1",
        "ci_coordinator.uq_webhook_deliveries_body_sha256/v1",
    ),
    routine_requirements=(),
    privilege_requirements=("runtime-ingress-issuance-state-dml/v1",),
)

SHADOW_RECONCILIATION_STATE = CapabilityDefinition(
    capability_id="runtime-shadow-reconciliation-state/v1",
    schema_requirements=(
        "ci_coordinator.shadow_evidence/v1",
        "ci_coordinator.reconciliation_subjects/v1",
        "ci_coordinator.reconciliation_observations/v1",
        "ci_coordinator.reconciliation_results/v1",
    ),
    read_domain_id="shadow-reconciliation-state/v1",
    write_domain_id="shadow-reconciliation-state/v1",
    codec_id="shadow-reconciliation-state-canonical-json/v1",
    backfill_state_id="not-applicable/v1",
    bridge_constraint_ids=(
        "ci_coordinator.ck_shadow_evidence_record_byte_limit/v1",
        "ci_coordinator.ck_reconciliation_subjects_canonical_byte_limits/v1",
        "ci_coordinator.ck_reconciliation_observations_canonical_byte_limit/v1",
        "ci_coordinator.ck_reconciliation_results_canonical_byte_limit/v1",
        "ci_coordinator.ck_reconciliation_subjects_scope_safe/v1",
    ),
    routine_requirements=(),
    privilege_requirements=("runtime-shadow-reconciliation-state-column-dml/v1",),
)

RUNTIME_INGRESS_ISSUANCE_STATE_V2 = CapabilityDefinition(
    capability_id="runtime-ingress-issuance-state/v2",
    schema_requirements=(
        "ci_coordinator.webhook_deliveries/v1",
        "ci_coordinator.issued_plan_envelopes/v2",
        "ci_coordinator.production_admission_authorities/v1",
        "ci_coordinator.production_admission_scope_bindings/v1",
    ),
    read_domain_id="runtime-ingress-issuance-state/v2",
    write_domain_id="runtime-ingress-issuance-state/v2",
    codec_id="runtime-ingress-issuance-canonical-json/v2",
    backfill_state_id="exact-signed-plan-expiry/v1",
    bridge_constraint_ids=(
        *RUNTIME_INGRESS_ISSUANCE_STATE.bridge_constraint_ids,
        "ci_coordinator.ck_issued_plan_envelopes_exact_expiry/v1",
    ),
    routine_requirements=(),
    privilege_requirements=("runtime-ingress-issuance-state-dml/v2",),
)

SHADOW_RECONCILIATION_STATE_V2 = CapabilityDefinition(
    capability_id="runtime-shadow-reconciliation-state/v2",
    schema_requirements=(
        "ci_coordinator.shadow_evidence/v1",
        "ci_coordinator.reconciliation_subjects/v2",
        "ci_coordinator.reconciliation_observations/v1",
        "ci_coordinator.reconciliation_results/v1",
    ),
    read_domain_id="shadow-reconciliation-state/v2",
    write_domain_id="shadow-reconciliation-state/v2",
    codec_id="shadow-reconciliation-state-canonical-json/v2",
    backfill_state_id="conservative-legacy-execution-origin/v1",
    bridge_constraint_ids=(
        *SHADOW_RECONCILIATION_STATE.bridge_constraint_ids,
        "ci_coordinator.ck_reconciliation_subjects_production_origin/v1",
        "ci_coordinator.fk_reconciliation_subjects_production_generation/v1",
    ),
    routine_requirements=(),
    privilege_requirements=("runtime-shadow-reconciliation-state-column-dml/v2",),
)

PRODUCTION_GENERATION_CUTOVER = CapabilityDefinition(
    capability_id="production-generation-cutover/v1",
    schema_requirements=(
        "ci_coordinator.production_evidence_bundles/v1",
        "ci_coordinator.production_staged_grants/v1",
        "ci_coordinator.production_scope_states/v1",
    ),
    read_domain_id="production-generation-cutover/v1",
    write_domain_id="scope-locked-generation-and-audit/v1",
    codec_id="production-generation-cutover-canonical-json/v1",
    backfill_state_id="inactive-until-explicit-cutover/v1",
    bridge_constraint_ids=(
        "ci_coordinator.ck_production_evidence_bundles_identity/v1",
        "ci_coordinator.ck_production_evidence_bundles_bytes/v1",
        "ci_coordinator.fk_production_staged_grants_scope/v1",
        "ci_coordinator.fk_production_staged_grants_bundle/v1",
        "ci_coordinator.fk_production_scope_states_active/v1",
        "ci_coordinator.fk_production_scope_states_staged/v1",
        "ci_coordinator.fk_production_scope_states_latch/v1",
        "ci_coordinator.ck_production_scope_states_counters/v1",
        "ci_coordinator.ck_production_scope_states_active/v1",
        "ci_coordinator.ck_production_scope_states_latch/v1",
    ),
    routine_requirements=(
        "ci_coordinator.guard_production_evidence_insert/v1",
        "ci_coordinator.guard_production_stage_insert/v1",
        "ci_coordinator.guard_production_state_transition/v1",
    ),
    privilege_requirements=("runtime-production-cutover-column-dml/v1",),
)

CI_ECONOMICS_EVIDENCE = CapabilityDefinition(
    capability_id="ci-economics-evidence/v1",
    schema_requirements=(
        "ci_coordinator.ci_workflow_observations/v1",
        "ci_coordinator.ci_workflow_attempt_collections/v1",
        "ci_coordinator.ci_workflow_attempt_snapshots/v1",
        "ci_coordinator.ci_workflow_attempt_snapshot_jobs/v1",
    ),
    read_domain_id="bounded-ci-economics-evidence/v1",
    write_domain_id="leased-ci-economics-collection-and-retention/v1",
    codec_id="ci-economics-canonical-json/v1",
    backfill_state_id="not-applicable/v1",
    bridge_constraint_ids=(
        "ci_coordinator.ck_ci_workflow_observations_canonical_byte_limit/v1",
        "ci_coordinator.ck_ci_workflow_attempt_collections_subject/v1",
        "ci_coordinator.ck_ci_workflow_attempt_collections_state_shape/v1",
        "ci_coordinator.ck_ci_workflow_attempt_collections_causal_history/v1",
        "ci_coordinator.ck_ci_workflow_attempt_collections_lifetime/v1",
        "ci_coordinator.ck_ci_workflow_attempt_snapshots_identity_safe/v1",
        "ci_coordinator.ck_ci_workflow_attempt_snapshot_jobs_canonical/v1",
    ),
    routine_requirements=("ci_coordinator.guard_ci_economics_mutation/v1",),
    privilege_requirements=("runtime-ci-economics-column-dml/v1",),
)

CI_ECONOMICS_EVIDENCE_V2 = CapabilityDefinition(
    capability_id="ci-economics-evidence/v2",
    schema_requirements=(
        "ci_coordinator.ci_workflow_observations/v1",
        "ci_coordinator.ci_workflow_attempt_collections/v2",
        "ci_coordinator.ci_workflow_attempt_snapshots/v2",
        "ci_coordinator.ci_workflow_attempt_snapshot_jobs/v1",
        "ci_coordinator.ci_job_measurement_reports/v1",
    ),
    read_domain_id="bounded-source-aware-ci-economics-evidence/v2",
    write_domain_id="source-bound-leased-ci-economics-collection-and-retention/v2",
    codec_id="source-aware-ci-economics-canonical-json/v2",
    backfill_state_id="all-existing-economics-sources-linked-to-reconciliation/v2",
    bridge_constraint_ids=(
        *CI_ECONOMICS_EVIDENCE.bridge_constraint_ids,
        "ci_coordinator.ck_ci_workflow_attempt_collections_source/v2",
        "ci_coordinator.fk_ci_workflow_attempt_collections_legacy_subject/v2",
        "ci_coordinator.uq_ci_workflow_attempt_collections_retention/v2",
        "ci_coordinator.ck_ci_workflow_attempt_snapshots_source/v2",
        "ci_coordinator.fk_ci_workflow_attempt_snapshots_collection/v2",
        "ci_coordinator.fk_ci_job_measurement_reports_source/v1",
        "ci_coordinator.ck_ci_job_measurement_reports_payload/v1",
        "ci_coordinator.ck_ci_job_measurement_reports_retention/v1",
    ),
    routine_requirements=CI_ECONOMICS_EVIDENCE.routine_requirements,
    privilege_requirements=("runtime-ci-economics-source-aware-column-dml/v2",),
)

CI_ECONOMICS_EVIDENCE_V3 = CapabilityDefinition(
    capability_id="ci-economics-evidence/v3",
    schema_requirements=(
        *CI_ECONOMICS_EVIDENCE_V2.schema_requirements,
        "ci_coordinator.ci_economics_budget_policies/v1",
        "ci_coordinator.ci_economics_budget_signals/v1",
    ),
    read_domain_id="bounded-source-aware-ci-economics-and-budget-signals/v3",
    write_domain_id="atomic-report-policy-snapshot-and-audited-budget-cas/v3",
    codec_id="source-aware-ci-economics-and-budget-canonical-json/v3",
    backfill_state_id="historical-reports-not-retroactively-evaluated/v3",
    bridge_constraint_ids=(
        *CI_ECONOMICS_EVIDENCE_V2.bridge_constraint_ids,
        "ci_coordinator.uq_ci_job_measurement_reports_budget_identity/v1",
        "ci_coordinator.fk_ci_economics_budget_signals_report/v1",
        "ci_coordinator.fk_ci_economics_budget_signals_policy/v1",
        "ci_coordinator.uq_ci_economics_budget_signals_slot/v1",
        "ci_coordinator.ck_ci_economics_budget_signals_measurement/v1",
        "ci_coordinator.ck_ci_economics_budget_policies_integers/v1",
    ),
    routine_requirements=CI_ECONOMICS_EVIDENCE_V2.routine_requirements,
    privilege_requirements=("runtime-ci-economics-budget-column-dml/v3",),
)

CI_ECONOMICS_EVIDENCE_V4 = replace(
    CI_ECONOMICS_EVIDENCE_V3,
    capability_id="ci-economics-evidence/v4",
    schema_requirements=tuple(
        "ci_coordinator.ci_workflow_attempt_collections/v3"
        if item == "ci_coordinator.ci_workflow_attempt_collections/v2"
        else item
        for item in CI_ECONOMICS_EVIDENCE_V3.schema_requirements
    ),
    bridge_constraint_ids=(
        *(
            item
            for item in CI_ECONOMICS_EVIDENCE_V3.bridge_constraint_ids
            if item != "ci_coordinator.ck_ci_workflow_attempt_collections_state_shape/v1"
        ),
        "ci_coordinator.ck_ci_workflow_attempt_collections_state_shape/v2",
        "ci_coordinator.ck_ci_workflow_attempt_collections_lease/v2",
    ),
    backfill_state_id="existing-collection-states-validated-without-repair/v4",
)

CI_REPOSITORY_OBSERVATION = CapabilityDefinition(
    capability_id="ci-repository-observation/v1",
    schema_requirements=(
        "ci_coordinator.ci_observation_subscriptions/v1",
        "ci_coordinator.ci_observation_scans/v1",
        "ci_coordinator.ci_observation_gaps/v1",
    ),
    read_domain_id="bounded-repository-observation-status-and-gap-evidence/v1",
    write_domain_id="audited-observation-configuration-and-hard-leased-page-cas/v1",
    codec_id="repository-observation-canonical-json/v1",
    backfill_state_id="no-subscriptions-created-by-migration/v1",
    bridge_constraint_ids=(
        "ci_coordinator.fk_ci_observation_scans_subscription/v1",
        "ci_coordinator.fk_ci_observation_gaps_subscription/v1",
        "ci_coordinator.ck_ci_observation_subscriptions_integers/v1",
        "ci_coordinator.ck_ci_observation_scans_integers/v1",
        "ci_coordinator.ck_ci_observation_scans_completed_precision/v1",
    ),
    routine_requirements=(),
    privilege_requirements=("runtime-repository-observation-column-dml/v1",),
)

CI_HISTORY_ARCHIVE = CapabilityDefinition(
    capability_id="ci-actions-history-archive/v1",
    schema_requirements=(
        "ci_coordinator.ci_history_defaults/v1",
        "ci_coordinator.ci_history_datasets/v1",
        "ci_coordinator.ci_history_scans/v1",
        "ci_coordinator.ci_history_attempts/v1",
        "ci_coordinator.ci_history_jobs/v1",
        "ci_coordinator.ci_history_details/v1",
        "ci_coordinator.ci_history_gaps/v1",
        "ci_coordinator.ci_history_rechecks/v1",
        "ci_coordinator.ci_history_delivery_inbox/v1",
    ),
    read_domain_id="bounded-scoped-history-statistics-and-progress/v1",
    write_domain_id="audited-configuration-dual-lane-history-and-detail-expiry/v1",
    codec_id="actions-history-canonical-json-and-normalized-jobs/v1",
    backfill_state_id="default-detail-policy-with-no-history-datasets/v1",
    bridge_constraint_ids=(
        "ci_coordinator.fk_ci_history_scans_dataset/v1",
        "ci_coordinator.fk_ci_history_gaps_dataset/v1",
        "ci_coordinator.fk_ci_history_rechecks_dataset/v1",
        "ci_coordinator.fk_ci_history_attempts_dataset/v1",
        "ci_coordinator.fk_ci_history_jobs_attempt/v1",
        "ci_coordinator.fk_ci_history_details_attempt/v1",
        "ci_coordinator.ck_ci_history_defaults_singleton/v1",
        "ci_coordinator.ck_ci_history_scans_lease/v1",
        "ci_coordinator.ck_ci_history_scans_lane/v1",
        "ci_coordinator.ck_ci_history_rechecks_lease/v1",
        "ci_coordinator.ck_ci_history_rechecks_attempts/v1",
        "ci_coordinator.ck_ci_history_attempts_detail/v1",
        "ci_coordinator.fk_ci_history_delivery_inbox_source/v1",
        "ci_coordinator.ck_ci_history_delivery_inbox_receipt/v1",
    ),
    routine_requirements=(),
    privilege_requirements=("runtime-actions-history-no-permanent-erasure-dml/v1",),
)

ANALYTICS_PURPOSE_SETTINGS = CapabilityDefinition(
    capability_id="ci-analytics-purpose-settings/v1",
    schema_requirements=("ci_coordinator.analytics_purpose_settings/v1",),
    read_domain_id="revision-consistent-scoped-purpose-mapping/v1",
    write_domain_id="generation-fenced-audited-purpose-cas/v1",
    codec_id="canonical-purpose-settings/v1",
    backfill_state_id="missing-until-explicit-configuration/v1",
    bridge_constraint_ids=("ci_coordinator.fk_analytics_purpose_dataset/v1",),
    routine_requirements=(),
    privilege_requirements=("runtime-purpose-select-insert-column-update/v1",),
)

_CAPABILITY_DEFINITIONS = {
    ANALYTICS_PURPOSE_SETTINGS.capability_id: ANALYTICS_PURPOSE_SETTINGS,
    ADMINISTRATOR_ACTIVITY.capability_id: ADMINISTRATOR_ACTIVITY,
    DATABASE_COMPATIBILITY_PROTOCOL.capability_id: DATABASE_COMPATIBILITY_PROTOCOL,
    AUDIT_LEDGER.capability_id: AUDIT_LEDGER,
    CONFIG_EPOCH_LIFECYCLE.capability_id: CONFIG_EPOCH_LIFECYCLE,
    CONFIG_EPOCH_REGISTRATION_OPERATIONS.capability_id: CONFIG_EPOCH_REGISTRATION_OPERATIONS,
    OPERATOR_OVERRIDE_STATE.capability_id: OPERATOR_OVERRIDE_STATE,
    PROPOSAL_REVIEW_REGISTRATION.capability_id: PROPOSAL_REVIEW_REGISTRATION,
    GOVERNANCE_BASELINE_STATE.capability_id: GOVERNANCE_BASELINE_STATE,
    CONTROL_PLANE_IDENTITY_STATE.capability_id: CONTROL_PLANE_IDENTITY_STATE,
    RUNTIME_INGRESS_ISSUANCE_STATE.capability_id: RUNTIME_INGRESS_ISSUANCE_STATE,
    WEBHOOK_BODY_IDENTITY.capability_id: WEBHOOK_BODY_IDENTITY,
    SHADOW_RECONCILIATION_STATE.capability_id: SHADOW_RECONCILIATION_STATE,
    CI_ECONOMICS_EVIDENCE.capability_id: CI_ECONOMICS_EVIDENCE,
    CI_ECONOMICS_EVIDENCE_V2.capability_id: CI_ECONOMICS_EVIDENCE_V2,
    CI_ECONOMICS_EVIDENCE_V3.capability_id: CI_ECONOMICS_EVIDENCE_V3,
    CI_ECONOMICS_EVIDENCE_V4.capability_id: CI_ECONOMICS_EVIDENCE_V4,
    CI_REPOSITORY_OBSERVATION.capability_id: CI_REPOSITORY_OBSERVATION,
    CI_HISTORY_ARCHIVE.capability_id: CI_HISTORY_ARCHIVE,
    RUNTIME_INGRESS_ISSUANCE_STATE_V2.capability_id: RUNTIME_INGRESS_ISSUANCE_STATE_V2,
    SHADOW_RECONCILIATION_STATE_V2.capability_id: SHADOW_RECONCILIATION_STATE_V2,
    PRODUCTION_GENERATION_CUTOVER.capability_id: PRODUCTION_GENERATION_CUTOVER,
}


def audit_ledger_requirements(profile: CompatibilityProfile) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (DATABASE_COMPATIBILITY_PROTOCOL.declaration(), AUDIT_LEDGER.declaration()),
    )


def config_epoch_lifecycle_requirements(
    profile: CompatibilityProfile,
) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            AUDIT_LEDGER.declaration(),
            CONFIG_EPOCH_LIFECYCLE.declaration(),
        ),
    )


def config_epoch_registration_requirements(
    profile: CompatibilityProfile,
) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            AUDIT_LEDGER.declaration(),
            CONFIG_EPOCH_LIFECYCLE.declaration(),
            CONFIG_EPOCH_REGISTRATION_OPERATIONS.declaration(),
        ),
    )


def proposal_review_registration_requirements(
    profile: CompatibilityProfile,
) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            AUDIT_LEDGER.declaration(),
            CONFIG_EPOCH_LIFECYCLE.declaration(),
            PROPOSAL_REVIEW_REGISTRATION.declaration(),
            CONTROL_PLANE_IDENTITY_STATE.declaration(),
        ),
    )


def control_plane_identity_state_requirements(
    profile: CompatibilityProfile,
) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            CONTROL_PLANE_IDENTITY_STATE.declaration(),
        ),
    )


def governance_baseline_state_requirements(
    profile: CompatibilityProfile,
) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            AUDIT_LEDGER.declaration(),
            GOVERNANCE_BASELINE_STATE.declaration(),
        ),
    )


def runtime_ingress_issuance_state_requirements(
    profile: CompatibilityProfile,
) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            AUDIT_LEDGER.declaration(),
            RUNTIME_INGRESS_ISSUANCE_STATE_V2.declaration(),
            WEBHOOK_BODY_IDENTITY.declaration(),
            PRODUCTION_GENERATION_CUTOVER.declaration(),
            SHADOW_RECONCILIATION_STATE_V2.declaration(),
        ),
    )


def shadow_reconciliation_state_requirements(
    profile: CompatibilityProfile,
) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            AUDIT_LEDGER.declaration(),
            SHADOW_RECONCILIATION_STATE_V2.declaration(),
            PRODUCTION_GENERATION_CUTOVER.declaration(),
        ),
    )


def ci_economics_evidence_requirements(
    profile: CompatibilityProfile,
) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            AUDIT_LEDGER.declaration(),
            RUNTIME_INGRESS_ISSUANCE_STATE_V2.declaration(),
            WEBHOOK_BODY_IDENTITY.declaration(),
            SHADOW_RECONCILIATION_STATE_V2.declaration(),
            CI_ECONOMICS_EVIDENCE_V4.declaration(),
        ),
    )


def ci_observation_requirements(
    profile: CompatibilityProfile,
) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (*ci_economics_evidence_requirements(profile), CI_REPOSITORY_OBSERVATION.declaration()),
    )


def ci_history_requirements(profile: CompatibilityProfile) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (*audit_ledger_requirements(profile), CI_HISTORY_ARCHIVE.declaration()),
    )


def webhook_ingestion_requirements(
    profile: CompatibilityProfile,
) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            AUDIT_LEDGER.declaration(),
            RUNTIME_INGRESS_ISSUANCE_STATE_V2.declaration(),
            WEBHOOK_BODY_IDENTITY.declaration(),
            CI_ECONOMICS_EVIDENCE_V4.declaration(),
            CI_HISTORY_ARCHIVE.declaration(),
        ),
    )


def workbench_read_requirements(
    profile: CompatibilityProfile,
) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            AUDIT_LEDGER.declaration(),
            CONFIG_EPOCH_LIFECYCLE.declaration(),
            RUNTIME_INGRESS_ISSUANCE_STATE_V2.declaration(),
            SHADOW_RECONCILIATION_STATE_V2.declaration(),
        ),
    )


def production_cutover_requirements(
    profile: CompatibilityProfile,
) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            AUDIT_LEDGER.declaration(),
            RUNTIME_INGRESS_ISSUANCE_STATE_V2.declaration(),
            WEBHOOK_BODY_IDENTITY.declaration(),
            SHADOW_RECONCILIATION_STATE_V2.declaration(),
            PRODUCTION_GENERATION_CUTOVER.declaration(),
            CONFIG_EPOCH_LIFECYCLE.declaration(),
            OPERATOR_OVERRIDE_STATE.declaration(),
        ),
    )


def definitions_for(
    capabilities: tuple[CapabilityDeclaration, ...],
) -> tuple[CapabilityDefinition, ...]:
    """Resolve only known descriptor-identical capability definitions."""
    definitions: list[CapabilityDefinition] = []
    for capability in capabilities:
        definition = _CAPABILITY_DEFINITIONS.get(capability.capability_id)
        if definition is None or definition.declaration() != capability:
            raise CompatibilityContractError(
                "database_capability_definition_unknown",
                "required capability has no exact local definition",
            )
        definitions.append(definition)
    return tuple(definitions)
