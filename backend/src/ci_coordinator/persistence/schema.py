# ruff: noqa: I001

from __future__ import annotations

# SQLAlchemy preserves table insertion order, so family imports are schema-significant.
from ci_coordinator.persistence._schema_core import (
    APPLICATION_SCHEMA as APPLICATION_SCHEMA,
    metadata as metadata,
)
from ci_coordinator.persistence._schema_audit import (
    audit_events as audit_events,
    audit_ledger_head as audit_ledger_head,
)
from ci_coordinator.persistence._schema_compatibility import (
    compatibility_capabilities as compatibility_capabilities,
    compatibility_declarations as compatibility_declarations,
)
from ci_coordinator.persistence._schema_activity import (
    activity_events as activity_events,
    activity_diagnostic_buckets as activity_diagnostic_buckets,
)
from ci_coordinator.persistence._schema_ci_economics import (
    ci_workflow_attempt_collections as ci_workflow_attempt_collections,
    ci_workflow_attempt_snapshot_jobs as ci_workflow_attempt_snapshot_jobs,
    ci_workflow_attempt_snapshots as ci_workflow_attempt_snapshots,
    ci_workflow_observations as ci_workflow_observations,
)
from ci_coordinator.persistence._schema_config_epochs import (
    active_config_epochs as active_config_epochs,
    config_epoch_activations as config_epoch_activations,
    config_epoch_registrations as config_epoch_registrations,
    config_epochs as config_epochs,
)
from ci_coordinator.persistence._schema_ci_measurement_reports import (
    ci_job_measurement_reports as ci_job_measurement_reports,
)
from ci_coordinator.persistence._schema_ci_economics_budgets import (
    ci_economics_budget_policies as ci_economics_budget_policies,
    ci_economics_budget_signals as ci_economics_budget_signals,
)
from ci_coordinator.persistence._schema_ci_observation import (
    ci_observation_subscriptions as ci_observation_subscriptions,
    ci_observation_scans as ci_observation_scans,
    ci_observation_gaps as ci_observation_gaps,
)
from ci_coordinator.persistence._schema_ci_history_control import (
    ci_history_defaults as ci_history_defaults,
    ci_history_datasets as ci_history_datasets,
    ci_history_scans as ci_history_scans,
    ci_history_gaps as ci_history_gaps,
    ci_history_rechecks as ci_history_rechecks,
)
from ci_coordinator.persistence._schema_ci_history_archive import (
    ci_history_attempts as ci_history_attempts,
    ci_history_jobs as ci_history_jobs,
    ci_history_details as ci_history_details,
)
from ci_coordinator.persistence._schema_ci_history_delivery import (
    ci_history_delivery_inbox as ci_history_delivery_inbox,
)
from ci_coordinator.persistence._schema_analytics_purpose import (
    analytics_purpose_settings as analytics_purpose_settings,
)
from ci_coordinator.persistence._schema_proposal_reviews import (
    workflow_proposal_reviews as workflow_proposal_reviews,
)
from ci_coordinator.persistence._schema_repository_attestations import (
    repository_attestation_transactions as repository_attestation_transactions,
)
from ci_coordinator.persistence._schema_governance_baselines import (
    governance_baseline_operations as governance_baseline_operations,
    governance_baselines as governance_baselines,
)
from ci_coordinator.persistence._schema_control_plane_identity import (
    control_plane_logout_replays as control_plane_logout_replays,
    control_plane_sessions as control_plane_sessions,
)
from ci_coordinator.persistence._schema_runtime_state import (
    issued_plan_envelopes as issued_plan_envelopes,
    production_admission_authorities as production_admission_authorities,
    production_admission_scope_bindings as production_admission_scope_bindings,
    webhook_deliveries as webhook_deliveries,
)
from ci_coordinator.persistence._schema_shadow import (
    shadow_evidence as shadow_evidence,
)
from ci_coordinator.persistence._schema_reconciliation import (
    reconciliation_observations as reconciliation_observations,
    reconciliation_results as reconciliation_results,
    reconciliation_subjects as reconciliation_subjects,
)
from ci_coordinator.persistence.operator_override_schema import (
    operator_overrides as operator_overrides,
)
from ci_coordinator.persistence._schema_production_cutover import (
    production_evidence_bundles as production_evidence_bundles,
    production_scope_states as production_scope_states,
    production_staged_grants as production_staged_grants,
)

__all__ = (
    "APPLICATION_SCHEMA",
    "active_config_epochs",
    "activity_diagnostic_buckets",
    "activity_events",
    "analytics_purpose_settings",
    "audit_events",
    "audit_ledger_head",
    "ci_economics_budget_policies",
    "ci_economics_budget_signals",
    "ci_history_attempts",
    "ci_history_datasets",
    "ci_history_defaults",
    "ci_history_delivery_inbox",
    "ci_history_details",
    "ci_history_gaps",
    "ci_history_jobs",
    "ci_history_rechecks",
    "ci_history_scans",
    "ci_job_measurement_reports",
    "ci_observation_gaps",
    "ci_observation_scans",
    "ci_observation_subscriptions",
    "ci_workflow_attempt_collections",
    "ci_workflow_attempt_snapshot_jobs",
    "ci_workflow_attempt_snapshots",
    "ci_workflow_observations",
    "compatibility_capabilities",
    "compatibility_declarations",
    "config_epoch_activations",
    "config_epoch_registrations",
    "config_epochs",
    "control_plane_logout_replays",
    "control_plane_sessions",
    "governance_baseline_operations",
    "governance_baselines",
    "issued_plan_envelopes",
    "metadata",
    "operator_overrides",
    "production_admission_authorities",
    "production_admission_scope_bindings",
    "production_evidence_bundles",
    "production_scope_states",
    "production_staged_grants",
    "reconciliation_observations",
    "reconciliation_results",
    "reconciliation_subjects",
    "repository_attestation_transactions",
    "shadow_evidence",
    "webhook_deliveries",
    "workflow_proposal_reviews",
)
