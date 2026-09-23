"""Public settings projection without secret-bearing process values."""

from __future__ import annotations

from ci_coordinator.runtime_settings.contracts import (
    DisabledRuntimeSettings,
    EnforcingRuntimeSettings,
    RuntimeSettings,
    RuntimeSettingsProjection,
)


def redacted_settings_projection(settings: RuntimeSettings) -> RuntimeSettingsProjection:
    if isinstance(settings, DisabledRuntimeSettings):
        return _disabled_projection(settings)
    enforcing = settings if isinstance(settings, EnforcingRuntimeSettings) else None
    identity = settings.control_plane_identity
    return RuntimeSettingsProjection(
        mode=settings.mode,
        bind_host=settings.bind_host,
        bind_port=settings.bind_port,
        shutdown_timeout_seconds=settings.shutdown_timeout_seconds,
        request_timeout_seconds=settings.request_timeout_seconds,
        maximum_retained_body_bytes=settings.maximum_retained_body_bytes,
        database_pool_size=settings.database_pool_size,
        database_pool_timeout_seconds=settings.database_pool_timeout_seconds,
        plan_ttl_seconds=settings.plan_ttl_seconds,
        reconciliation_interval_seconds=settings.reconciliation_interval_seconds,
        reconciliation_startup_timeout_seconds=(settings.reconciliation_startup_timeout_seconds),
        reconciliation_scan_limit=settings.reconciliation_scan_limit,
        shadow_rollout_profile_id=settings.shadow_rollout_profile_id,
        github_app_id=settings.github_app_id,
        plan_signing_key_id=settings.plan_signing_key_id,
        oidc_allowed_workflow_ref_count=len(settings.oidc_allowed_workflow_refs),
        oidc_allowed_job_workflow_ref_count=len(settings.oidc_allowed_job_workflow_refs),
        oidc_allowed_workflow_path_count=len(settings.oidc_allowed_workflow_paths),
        oidc_allowed_job_workflow_path_count=len(settings.oidc_allowed_job_workflow_paths),
        break_glass_actor_id=settings.break_glass_actor_id,
        control_plane_scope_count=len(settings.control_plane_scope_allowlist),
        control_plane_inventory_mode=settings.control_plane_inventory_mode,
        control_plane_scope_mode=settings.control_plane_scope_mode,
        control_plane_inventory_installation_count=len(
            settings.control_plane_inventory_installation_allowlist
        ),
        control_plane_identity_mode="disabled" if identity is None else "keycloak",
        control_plane_public_origin=None if identity is None else identity.public_origin,
        control_plane_session_maximum_seconds=(
            None if identity is None else identity.maximum_session_seconds
        ),
        control_plane_profile_digest=None if identity is None else identity.profile_digest,
        production_admission_key_id=(
            None if enforcing is None else enforcing.production_admission_key_id
        ),
        deployed_artifact_digest=(
            None if enforcing is None else enforcing.deployed_artifact_digest
        ),
        environment_id=None if enforcing is None else enforcing.environment_id,
        enforcement_scope_count=(
            0 if enforcing is None else len(enforcing.enforcement_scope_allowlist)
        ),
        has_database_dsn=True,
        has_webhook_secret=True,
        has_github_private_key=True,
        has_plan_signing_private_key=True,
        has_break_glass_bearer_token=True,
        has_metrics_bearer_token=True,
        has_control_plane_browser_client_secret=identity is not None,
        has_control_plane_session_key=identity is not None,
        has_github_reviewer_client_secret=identity is not None,
        outbound_proxy_configured=settings.outbound_proxy_url is not None,
        has_production_admission_receipt=enforcing is not None,
        has_production_admission_public_key=enforcing is not None,
    )


def _disabled_projection(settings: DisabledRuntimeSettings) -> RuntimeSettingsProjection:
    return RuntimeSettingsProjection(
        mode=settings.mode,
        bind_host=settings.bind_host,
        bind_port=settings.bind_port,
        shutdown_timeout_seconds=settings.shutdown_timeout_seconds,
        request_timeout_seconds=None,
        maximum_retained_body_bytes=None,
        database_pool_size=None,
        database_pool_timeout_seconds=None,
        plan_ttl_seconds=None,
        reconciliation_interval_seconds=None,
        reconciliation_startup_timeout_seconds=None,
        reconciliation_scan_limit=None,
        shadow_rollout_profile_id=None,
        github_app_id=None,
        plan_signing_key_id=None,
        oidc_allowed_workflow_ref_count=0,
        oidc_allowed_job_workflow_ref_count=0,
        oidc_allowed_workflow_path_count=0,
        oidc_allowed_job_workflow_path_count=0,
        break_glass_actor_id=None,
        control_plane_scope_count=0,
        control_plane_inventory_mode="disabled",
        control_plane_scope_mode="disabled",
        control_plane_inventory_installation_count=0,
        control_plane_identity_mode="disabled",
        control_plane_public_origin=None,
        control_plane_session_maximum_seconds=None,
        control_plane_profile_digest=None,
        production_admission_key_id=None,
        deployed_artifact_digest=None,
        environment_id=None,
        enforcement_scope_count=0,
        has_database_dsn=False,
        has_webhook_secret=False,
        has_github_private_key=False,
        has_plan_signing_private_key=False,
        has_break_glass_bearer_token=False,
        has_metrics_bearer_token=False,
        has_control_plane_browser_client_secret=False,
        has_control_plane_session_key=False,
        has_github_reviewer_client_secret=False,
        outbound_proxy_configured=False,
        has_production_admission_receipt=False,
        has_production_admission_public_key=False,
    )
