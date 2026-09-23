# ruff: noqa: I001

from __future__ import annotations

from sqlalchemy import Table

from ci_coordinator.persistence import schema
from ci_coordinator.persistence import _schema_audit as audit_schema
from ci_coordinator.persistence import _schema_compatibility as compatibility_schema
from ci_coordinator.persistence import _schema_activity as activity_schema
from ci_coordinator.persistence import _schema_analytics_purpose as analytics_purpose_schema
from ci_coordinator.persistence import _schema_ci_economics as ci_economics_schema
from ci_coordinator.persistence import _schema_ci_economics_budgets as budget_schema
from ci_coordinator.persistence import _schema_ci_observation as observation_schema
from ci_coordinator.persistence import _schema_ci_history_control as history_control_schema
from ci_coordinator.persistence import _schema_ci_history_archive as history_archive_schema
from ci_coordinator.persistence import _schema_ci_history_delivery as history_delivery_schema
from ci_coordinator.persistence import _schema_ci_measurement_reports as measurement_report_schema
from ci_coordinator.persistence import _schema_config_epochs as config_epoch_schema
from ci_coordinator.persistence import _schema_control_plane_identity as identity_schema
from ci_coordinator.persistence import _schema_core as schema_core
from ci_coordinator.persistence import _schema_governance_baselines as governance_baseline_schema
from ci_coordinator.persistence import _schema_proposal_reviews as proposal_review_schema
from ci_coordinator.persistence import _schema_reconciliation as reconciliation_schema
from ci_coordinator.persistence import (
    _schema_repository_attestations as repository_attestation_schema,
)
from ci_coordinator.persistence import _schema_runtime_state as runtime_state_schema
from ci_coordinator.persistence import _schema_shadow as shadow_schema
from ci_coordinator.persistence import operator_override_schema
from ci_coordinator.persistence import _schema_production_cutover as production_cutover_schema

_EXPECTED_SCHEMA_EXPORTS = {
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
    "governance_baselines",
    "governance_baseline_operations",
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
}

_EXPECTED_TABLE_TOPOLOGY: tuple[tuple[str, str, Table], ...] = (
    ("ci_coordinator.audit_events", "audit_events", audit_schema.audit_events),
    ("ci_coordinator.audit_ledger_head", "audit_ledger_head", audit_schema.audit_ledger_head),
    (
        "ci_coordinator.database_compatibility_declarations",
        "compatibility_declarations",
        compatibility_schema.compatibility_declarations,
    ),
    (
        "ci_coordinator.database_compatibility_capabilities",
        "compatibility_capabilities",
        compatibility_schema.compatibility_capabilities,
    ),
    ("ci_coordinator.activity_events", "activity_events", activity_schema.activity_events),
    (
        "ci_coordinator.activity_diagnostic_buckets",
        "activity_diagnostic_buckets",
        activity_schema.activity_diagnostic_buckets,
    ),
    (
        "ci_coordinator.ci_workflow_observations",
        "ci_workflow_observations",
        ci_economics_schema.ci_workflow_observations,
    ),
    (
        "ci_coordinator.ci_workflow_attempt_collections",
        "ci_workflow_attempt_collections",
        ci_economics_schema.ci_workflow_attempt_collections,
    ),
    (
        "ci_coordinator.ci_workflow_attempt_snapshots",
        "ci_workflow_attempt_snapshots",
        ci_economics_schema.ci_workflow_attempt_snapshots,
    ),
    (
        "ci_coordinator.ci_workflow_attempt_snapshot_jobs",
        "ci_workflow_attempt_snapshot_jobs",
        ci_economics_schema.ci_workflow_attempt_snapshot_jobs,
    ),
    ("ci_coordinator.config_epochs", "config_epochs", config_epoch_schema.config_epochs),
    (
        "ci_coordinator.config_epoch_registrations",
        "config_epoch_registrations",
        config_epoch_schema.config_epoch_registrations,
    ),
    (
        "ci_coordinator.active_config_epochs",
        "active_config_epochs",
        config_epoch_schema.active_config_epochs,
    ),
    (
        "ci_coordinator.config_epoch_activations",
        "config_epoch_activations",
        config_epoch_schema.config_epoch_activations,
    ),
    (
        "ci_coordinator.ci_job_measurement_reports",
        "ci_job_measurement_reports",
        measurement_report_schema.ci_job_measurement_reports,
    ),
    (
        "ci_coordinator.ci_economics_budget_policies",
        "ci_economics_budget_policies",
        budget_schema.ci_economics_budget_policies,
    ),
    (
        "ci_coordinator.ci_economics_budget_signals",
        "ci_economics_budget_signals",
        budget_schema.ci_economics_budget_signals,
    ),
    (
        "ci_coordinator.ci_observation_subscriptions",
        "ci_observation_subscriptions",
        observation_schema.ci_observation_subscriptions,
    ),
    (
        "ci_coordinator.ci_observation_scans",
        "ci_observation_scans",
        observation_schema.ci_observation_scans,
    ),
    (
        "ci_coordinator.ci_observation_gaps",
        "ci_observation_gaps",
        observation_schema.ci_observation_gaps,
    ),
    *(
        (f"ci_coordinator.{name}", name, table)
        for name, table in (
            ("ci_history_defaults", history_control_schema.ci_history_defaults),
            ("ci_history_datasets", history_control_schema.ci_history_datasets),
            ("ci_history_scans", history_control_schema.ci_history_scans),
            ("ci_history_gaps", history_control_schema.ci_history_gaps),
            ("ci_history_rechecks", history_control_schema.ci_history_rechecks),
            ("ci_history_attempts", history_archive_schema.ci_history_attempts),
            ("ci_history_jobs", history_archive_schema.ci_history_jobs),
            ("ci_history_details", history_archive_schema.ci_history_details),
            ("ci_history_delivery_inbox", history_delivery_schema.ci_history_delivery_inbox),
        )
    ),
    (
        "ci_coordinator.analytics_purpose_settings",
        "analytics_purpose_settings",
        analytics_purpose_schema.analytics_purpose_settings,
    ),
    (
        "ci_coordinator.workflow_proposal_reviews",
        "workflow_proposal_reviews",
        proposal_review_schema.workflow_proposal_reviews,
    ),
    (
        "ci_coordinator.repository_attestation_transactions",
        "repository_attestation_transactions",
        repository_attestation_schema.repository_attestation_transactions,
    ),
    (
        "ci_coordinator.governance_baselines",
        "governance_baselines",
        governance_baseline_schema.governance_baselines,
    ),
    (
        "ci_coordinator.governance_baseline_operations",
        "governance_baseline_operations",
        governance_baseline_schema.governance_baseline_operations,
    ),
    (
        "ci_coordinator.control_plane_sessions",
        "control_plane_sessions",
        identity_schema.control_plane_sessions,
    ),
    (
        "ci_coordinator.control_plane_logout_replays",
        "control_plane_logout_replays",
        identity_schema.control_plane_logout_replays,
    ),
    (
        "ci_coordinator.webhook_deliveries",
        "webhook_deliveries",
        runtime_state_schema.webhook_deliveries,
    ),
    (
        "ci_coordinator.production_admission_authorities",
        "production_admission_authorities",
        runtime_state_schema.production_admission_authorities,
    ),
    (
        "ci_coordinator.production_admission_scope_bindings",
        "production_admission_scope_bindings",
        runtime_state_schema.production_admission_scope_bindings,
    ),
    (
        "ci_coordinator.issued_plan_envelopes",
        "issued_plan_envelopes",
        runtime_state_schema.issued_plan_envelopes,
    ),
    ("ci_coordinator.shadow_evidence", "shadow_evidence", shadow_schema.shadow_evidence),
    (
        "ci_coordinator.reconciliation_subjects",
        "reconciliation_subjects",
        reconciliation_schema.reconciliation_subjects,
    ),
    (
        "ci_coordinator.reconciliation_observations",
        "reconciliation_observations",
        reconciliation_schema.reconciliation_observations,
    ),
    (
        "ci_coordinator.reconciliation_results",
        "reconciliation_results",
        reconciliation_schema.reconciliation_results,
    ),
    (
        "ci_coordinator.operator_overrides",
        "operator_overrides",
        operator_override_schema.operator_overrides,
    ),
    (
        "ci_coordinator.production_evidence_bundles",
        "production_evidence_bundles",
        production_cutover_schema.production_evidence_bundles,
    ),
    (
        "ci_coordinator.production_staged_grants",
        "production_staged_grants",
        production_cutover_schema.production_staged_grants,
    ),
    (
        "ci_coordinator.production_scope_states",
        "production_scope_states",
        production_cutover_schema.production_scope_states,
    ),
)


def test_schema_facade_preserves_public_surface_and_shared_table_topology() -> None:
    assert set(schema.__all__) == _EXPECTED_SCHEMA_EXPORTS
    assert all(hasattr(schema, name) for name in schema.__all__)
    assert schema.metadata is schema_core.metadata
    assert tuple(schema.metadata.tables) == tuple(
        qualified_name for qualified_name, _, _ in _EXPECTED_TABLE_TOPOLOGY
    )

    for qualified_name, facade_name, private_table in _EXPECTED_TABLE_TOPOLOGY:
        assert schema.metadata.tables[qualified_name] is private_table
        assert getattr(schema, facade_name) is private_table
        assert private_table.metadata is schema.metadata

    assert schema.ci_job_measurement_reports.primary_key.name == "pk_ci_job_measurement_reports"
