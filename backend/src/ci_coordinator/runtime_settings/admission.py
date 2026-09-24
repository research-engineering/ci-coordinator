"""Pure admission of caller-supplied process setting mappings."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ci_coordinator.runtime_settings._rejection import SettingsRejected
from ci_coordinator.runtime_settings.contracts import (
    DisabledRuntimeSettings,
    EnforcingRuntimeSettings,
    NonEnforcingRuntimeSettings,
    RuntimeSettings,
    RuntimeSettingsRejection,
    SecretValue,
    is_authentication_secret_text,
    is_break_glass_bearer_token,
    is_metrics_bearer_token,
    is_repository_scope,
    is_secret_text,
    is_workflow_path_identity,
    normalize_bind_host,
    normalize_outbound_proxy_url,
)
from ci_coordinator.runtime_settings.control_plane_identity import (
    CONTROL_PLANE_IDENTITY_FIELDS,
    _admit_control_plane_identity_settings,
)

_PREFIX = "CI_COORDINATOR_"
_MODE = f"{_PREFIX}RUNTIME_MODE"
_HOST = f"{_PREFIX}BIND_HOST"
_PORT = f"{_PREFIX}BIND_PORT"
_SHUTDOWN_TIMEOUT = f"{_PREFIX}SHUTDOWN_TIMEOUT_SECONDS"
_REQUEST_TIMEOUT = f"{_PREFIX}REQUEST_TIMEOUT_SECONDS"
_MAXIMUM_RETAINED_BODY_BYTES = f"{_PREFIX}MAXIMUM_RETAINED_BODY_BYTES"
_DATABASE_POOL_SIZE = f"{_PREFIX}DATABASE_POOL_SIZE"
_DATABASE_POOL_TIMEOUT = f"{_PREFIX}DATABASE_POOL_TIMEOUT_SECONDS"
_PLAN_TTL = f"{_PREFIX}PLAN_TTL_SECONDS"
_MAX_PLAN_TTL_SECONDS = 300
_RECONCILIATION_INTERVAL = f"{_PREFIX}RECONCILIATION_INTERVAL_SECONDS"
_RECONCILIATION_STARTUP_TIMEOUT = f"{_PREFIX}RECONCILIATION_STARTUP_TIMEOUT_SECONDS"
_RECONCILIATION_SCAN_LIMIT = f"{_PREFIX}RECONCILIATION_SCAN_LIMIT"
_SHADOW_ROLLOUT_PROFILE_ID = f"{_PREFIX}SHADOW_ROLLOUT_PROFILE_ID"
_DATABASE_DSN = f"{_PREFIX}DATABASE_DSN"
_WEBHOOK_SECRET = f"{_PREFIX}WEBHOOK_SECRET"
_GITHUB_APP_ID = f"{_PREFIX}GITHUB_APP_ID"
_GITHUB_PRIVATE_KEY = f"{_PREFIX}GITHUB_PRIVATE_KEY"
_PLAN_SIGNING_KEY_ID = f"{_PREFIX}PLAN_SIGNING_KEY_ID"
_PLAN_SIGNING_PRIVATE_KEY = f"{_PREFIX}PLAN_SIGNING_PRIVATE_KEY"
_OIDC_AUDIENCE = f"{_PREFIX}OIDC_AUDIENCE"
_OIDC_WORKFLOW_REFS = f"{_PREFIX}OIDC_ALLOWED_WORKFLOW_REFS"
_OIDC_JOB_WORKFLOW_REFS = f"{_PREFIX}OIDC_ALLOWED_JOB_WORKFLOW_REFS"
_OIDC_WORKFLOW_PATHS = f"{_PREFIX}OIDC_ALLOWED_WORKFLOW_PATHS"
_OIDC_JOB_WORKFLOW_PATHS = f"{_PREFIX}OIDC_ALLOWED_JOB_WORKFLOW_PATHS"
_BREAK_GLASS_ACTOR_ID = f"{_PREFIX}BREAK_GLASS_ACTOR_ID"
_BREAK_GLASS_BEARER_TOKEN = f"{_PREFIX}BREAK_GLASS_BEARER_TOKEN"
_METRICS_BEARER_TOKEN = f"{_PREFIX}METRICS_BEARER_TOKEN"
_CONTROL_PLANE_SCOPES = f"{_PREFIX}CONTROL_PLANE_SCOPE_ALLOWLIST"
_CONTROL_PLANE_INVENTORY_INSTALLATIONS = f"{_PREFIX}CONTROL_PLANE_INVENTORY_INSTALLATION_ALLOWLIST"
_CONTROL_PLANE_INVENTORY_MODE = f"{_PREFIX}CONTROL_PLANE_INVENTORY_MODE"
_CONTROL_PLANE_SCOPE_MODE = f"{_PREFIX}CONTROL_PLANE_SCOPE_MODE"
_OUTBOUND_PROXY_URL = f"{_PREFIX}OUTBOUND_PROXY_URL"
_PRODUCTION_ADMISSION_RECEIPT_PATH = f"{_PREFIX}PRODUCTION_ADMISSION_RECEIPT_PATH"
_PRODUCTION_ADMISSION_KEY_ID = f"{_PREFIX}PRODUCTION_ADMISSION_KEY_ID"
_PRODUCTION_ADMISSION_PUBLIC_KEY = f"{_PREFIX}PRODUCTION_ADMISSION_PUBLIC_KEY_PEM"
_DEPLOYED_ARTIFACT_DIGEST = f"{_PREFIX}DEPLOYED_ARTIFACT_DIGEST"
_ENVIRONMENT_ID = f"{_PREFIX}ENVIRONMENT_ID"
_ENFORCEMENT_SCOPES = f"{_PREFIX}ENFORCEMENT_SCOPE_ALLOWLIST"
_MAX_BREAK_GLASS_BEARER_TOKEN_UTF8_BYTES = 4096
_MAX_WORKFLOW_REF_COUNT = 64
_MAX_WORKFLOW_REF_UTF8_BYTES = 512 * 4
_MAX_CONTROL_PLANE_SCOPE_COUNT = 1024
_MAX_CONTROL_PLANE_SCOPE_UTF8_BYTES = 64
_MAX_CONTROL_PLANE_INVENTORY_INSTALLATION_COUNT = 64
_COMMON_FIELDS = frozenset({_MODE, _HOST, _PORT, _SHUTDOWN_TIMEOUT})
_CONNECTED_FIELDS = _COMMON_FIELDS | frozenset(
    {
        _REQUEST_TIMEOUT,
        _MAXIMUM_RETAINED_BODY_BYTES,
        _DATABASE_POOL_SIZE,
        _DATABASE_POOL_TIMEOUT,
        _PLAN_TTL,
        _RECONCILIATION_INTERVAL,
        _RECONCILIATION_STARTUP_TIMEOUT,
        _RECONCILIATION_SCAN_LIMIT,
        _SHADOW_ROLLOUT_PROFILE_ID,
        _DATABASE_DSN,
        _WEBHOOK_SECRET,
        _GITHUB_APP_ID,
        _GITHUB_PRIVATE_KEY,
        _PLAN_SIGNING_KEY_ID,
        _PLAN_SIGNING_PRIVATE_KEY,
        _OIDC_AUDIENCE,
        _OIDC_WORKFLOW_REFS,
        _OIDC_JOB_WORKFLOW_REFS,
        _OIDC_WORKFLOW_PATHS,
        _OIDC_JOB_WORKFLOW_PATHS,
        _BREAK_GLASS_ACTOR_ID,
        _BREAK_GLASS_BEARER_TOKEN,
        _METRICS_BEARER_TOKEN,
        _CONTROL_PLANE_SCOPES,
        _CONTROL_PLANE_INVENTORY_INSTALLATIONS,
        _CONTROL_PLANE_INVENTORY_MODE,
        _CONTROL_PLANE_SCOPE_MODE,
        _OUTBOUND_PROXY_URL,
        *CONTROL_PLANE_IDENTITY_FIELDS,
    }
)
_ENFORCING_FIELDS = _CONNECTED_FIELDS | frozenset(
    {
        _PRODUCTION_ADMISSION_RECEIPT_PATH,
        _PRODUCTION_ADMISSION_KEY_ID,
        _PRODUCTION_ADMISSION_PUBLIC_KEY,
        _DEPLOYED_ARTIFACT_DIGEST,
        _ENVIRONMENT_ID,
        _ENFORCEMENT_SCOPES,
    }
)


@dataclass(frozen=True, slots=True)
class _CommonInputs:
    mode: str
    host: str
    port: int
    shutdown_timeout: int


@dataclass(frozen=True, slots=True)
class _RuntimeBounds:
    request_timeout: int
    plan_ttl: int
    reconciliation_interval: int
    reconciliation_startup_timeout: int
    reconciliation_scan_limit: int
    shadow_rollout_profile_id: str


@dataclass(frozen=True, slots=True)
class _CapacityInputs:
    maximum_retained_body_bytes: int
    database_pool_size: int
    database_pool_timeout_seconds: int


@dataclass(frozen=True, slots=True)
class _ProviderInputs:
    outbound_proxy_url: str | None
    database_dsn: str
    webhook_secret: str
    github_app_id: str
    github_private_key: str
    oidc_audience: str
    workflow_refs: frozenset[str]
    job_workflow_refs: frozenset[str]
    workflow_paths: frozenset[str]
    job_workflow_paths: frozenset[str]


@dataclass(frozen=True, slots=True)
class _ControlPlaneAuthorityInputs:
    break_glass_actor_id: str
    bearer_token: str
    scopes: frozenset[str]
    inventory_installation_ids: frozenset[int]
    inventory_mode: Literal["restricted", "app"]
    scope_mode: Literal["restricted", "app"]


@dataclass(frozen=True, slots=True)
class _ProductionInputs:
    receipt_path: Path
    key_id: str
    public_key_pem: str
    artifact_digest: str
    environment_id: str
    scopes: frozenset[str]


def admit_runtime_settings(
    mapping: Mapping[str, str],
) -> RuntimeSettings | RuntimeSettingsRejection:
    source = dict(mapping)
    try:
        return _admit_runtime_settings(source)
    except SettingsRejected as failure:
        return failure.rejection


def _admit_runtime_settings(source: dict[str, str]) -> RuntimeSettings:
    common = _admit_common_inputs(source)
    allowed_fields = {
        "disabled": _COMMON_FIELDS,
        "non_enforcing": _CONNECTED_FIELDS,
        "enforcing": _ENFORCING_FIELDS,
    }[common.mode]
    unexpected_field = next(
        (
            name
            for name in sorted((name for name in source if type(name) is str))
            if name.startswith(_PREFIX) and name not in allowed_fields
        ),
        None,
    )
    if unexpected_field is not None:
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", unexpected_field)
        ) from None
    signing = _admit_signing_pair(source)
    if common.mode == "disabled":
        return DisabledRuntimeSettings(
            bind_host=common.host,
            bind_port=common.port,
            shutdown_timeout_seconds=common.shutdown_timeout,
        )
    if signing is None:
        raise SettingsRejected(
            RuntimeSettingsRejection("missing_required_setting", _PLAN_SIGNING_KEY_ID)
        ) from None
    signing_key_id, signing_private_key = signing
    bounds = _admit_runtime_bounds(source)
    capacity = _admit_capacity_inputs(source)
    provider = _admit_provider_inputs(source)
    authority = _admit_control_plane_authority_inputs(source)
    metrics_bearer_token = _required_secret(
        source, _METRICS_BEARER_TOKEN, maximum_utf8_bytes=_MAX_BREAK_GLASS_BEARER_TOKEN_UTF8_BYTES
    )
    if not is_metrics_bearer_token(metrics_bearer_token):
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _METRICS_BEARER_TOKEN)
        ) from None
    control_plane_identity = _admit_control_plane_identity_settings(source)
    credential_values = {
        authority.bearer_token,
        provider.database_dsn,
        provider.github_private_key,
        provider.webhook_secret,
        signing_private_key,
    }
    if control_plane_identity is not None:
        credential_values.update(
            {
                control_plane_identity.browser_client_secret.reveal_for_composition(),
                control_plane_identity.github_reviewer_client_secret.reveal_for_composition(),
                control_plane_identity.session_key.reveal_for_composition(),
            }
        )
    if metrics_bearer_token in credential_values:
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _METRICS_BEARER_TOKEN)
        ) from None
    connected = NonEnforcingRuntimeSettings(
        bind_host=common.host,
        bind_port=common.port,
        shutdown_timeout_seconds=common.shutdown_timeout,
        request_timeout_seconds=bounds.request_timeout,
        maximum_retained_body_bytes=capacity.maximum_retained_body_bytes,
        database_pool_size=capacity.database_pool_size,
        database_pool_timeout_seconds=capacity.database_pool_timeout_seconds,
        plan_ttl_seconds=bounds.plan_ttl,
        reconciliation_interval_seconds=bounds.reconciliation_interval,
        reconciliation_startup_timeout_seconds=bounds.reconciliation_startup_timeout,
        reconciliation_scan_limit=bounds.reconciliation_scan_limit,
        shadow_rollout_profile_id=bounds.shadow_rollout_profile_id,
        database_dsn=SecretValue(provider.database_dsn),
        webhook_secret=SecretValue(provider.webhook_secret),
        github_app_id=provider.github_app_id,
        github_private_key=SecretValue(provider.github_private_key),
        plan_signing_key_id=signing_key_id,
        plan_signing_private_key=SecretValue(signing_private_key),
        oidc_audience=provider.oidc_audience,
        oidc_allowed_workflow_refs=provider.workflow_refs,
        oidc_allowed_job_workflow_refs=provider.job_workflow_refs,
        oidc_allowed_workflow_paths=provider.workflow_paths,
        oidc_allowed_job_workflow_paths=provider.job_workflow_paths,
        break_glass_actor_id=authority.break_glass_actor_id,
        break_glass_bearer_token=SecretValue(authority.bearer_token),
        metrics_bearer_token=SecretValue(metrics_bearer_token),
        control_plane_scope_allowlist=authority.scopes,
        control_plane_inventory_installation_allowlist=authority.inventory_installation_ids,
        control_plane_inventory_mode=authority.inventory_mode,
        control_plane_scope_mode=authority.scope_mode,
        control_plane_identity=control_plane_identity,
        outbound_proxy_url=provider.outbound_proxy_url,
    )
    if common.mode == "non_enforcing":
        return connected
    production = _admit_production_inputs(source, authority)
    return EnforcingRuntimeSettings(
        bind_host=connected.bind_host,
        bind_port=connected.bind_port,
        shutdown_timeout_seconds=connected.shutdown_timeout_seconds,
        request_timeout_seconds=connected.request_timeout_seconds,
        maximum_retained_body_bytes=connected.maximum_retained_body_bytes,
        database_pool_size=connected.database_pool_size,
        database_pool_timeout_seconds=connected.database_pool_timeout_seconds,
        plan_ttl_seconds=connected.plan_ttl_seconds,
        reconciliation_interval_seconds=connected.reconciliation_interval_seconds,
        reconciliation_startup_timeout_seconds=connected.reconciliation_startup_timeout_seconds,
        reconciliation_scan_limit=connected.reconciliation_scan_limit,
        shadow_rollout_profile_id=connected.shadow_rollout_profile_id,
        database_dsn=connected.database_dsn,
        webhook_secret=connected.webhook_secret,
        github_app_id=connected.github_app_id,
        github_private_key=connected.github_private_key,
        plan_signing_key_id=connected.plan_signing_key_id,
        plan_signing_private_key=connected.plan_signing_private_key,
        oidc_audience=connected.oidc_audience,
        oidc_allowed_workflow_refs=connected.oidc_allowed_workflow_refs,
        oidc_allowed_job_workflow_refs=connected.oidc_allowed_job_workflow_refs,
        oidc_allowed_workflow_paths=connected.oidc_allowed_workflow_paths,
        oidc_allowed_job_workflow_paths=connected.oidc_allowed_job_workflow_paths,
        break_glass_actor_id=connected.break_glass_actor_id,
        break_glass_bearer_token=connected.break_glass_bearer_token,
        metrics_bearer_token=connected.metrics_bearer_token,
        control_plane_scope_allowlist=connected.control_plane_scope_allowlist,
        control_plane_inventory_mode=connected.control_plane_inventory_mode,
        control_plane_scope_mode=connected.control_plane_scope_mode,
        control_plane_inventory_installation_allowlist=connected.control_plane_inventory_installation_allowlist,
        control_plane_identity=connected.control_plane_identity,
        outbound_proxy_url=connected.outbound_proxy_url,
        production_admission_receipt_path=production.receipt_path,
        production_admission_key_id=production.key_id,
        production_admission_public_key_pem=production.public_key_pem,
        deployed_artifact_digest=production.artifact_digest,
        environment_id=production.environment_id,
        enforcement_scope_allowlist=production.scopes,
    )


def _admit_common_inputs(mapping: Mapping[str, str]) -> _CommonInputs:
    mode = _required(mapping, _MODE)
    if mode not in {"disabled", "enforcing", "non_enforcing"}:
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", _MODE)) from None
    host = _required(mapping, _HOST)
    canonical_host = normalize_bind_host(host)
    if canonical_host is None:
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", _HOST)) from None
    port = _positive_int(mapping, _PORT, maximum=65535)
    shutdown_timeout = _positive_int(mapping, _SHUTDOWN_TIMEOUT, maximum=3600)
    return _CommonInputs(mode, canonical_host, port, shutdown_timeout)


def _admit_signing_pair(mapping: Mapping[str, str]) -> tuple[str, str] | None:
    key_id = _optional(mapping, _PLAN_SIGNING_KEY_ID)
    private_key = _optional_secret(mapping, _PLAN_SIGNING_PRIVATE_KEY)
    if (key_id is None) != (private_key is None):
        raise SettingsRejected(
            RuntimeSettingsRejection("incomplete_signing_configuration", _PLAN_SIGNING_KEY_ID)
        ) from None
    if key_id is None or private_key is None:
        return None
    if len(key_id) > 255:
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _PLAN_SIGNING_KEY_ID)
        ) from None
    return (key_id, private_key)


def _admit_runtime_bounds(mapping: Mapping[str, str]) -> _RuntimeBounds:
    request_timeout = _positive_int(mapping, _REQUEST_TIMEOUT, maximum=3600)
    plan_ttl = _positive_int(mapping, _PLAN_TTL, maximum=_MAX_PLAN_TTL_SECONDS)
    interval = _positive_int(mapping, _RECONCILIATION_INTERVAL, maximum=3600)
    startup_timeout = _positive_int(mapping, _RECONCILIATION_STARTUP_TIMEOUT, maximum=3600)
    scan_limit = _positive_int(mapping, _RECONCILIATION_SCAN_LIMIT, maximum=1000)
    profile_id = _required(mapping, _SHADOW_ROLLOUT_PROFILE_ID)
    if not _is_sha256_hex(profile_id):
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _SHADOW_ROLLOUT_PROFILE_ID)
        ) from None
    return _RuntimeBounds(
        request_timeout, plan_ttl, interval, startup_timeout, scan_limit, profile_id
    )


def _admit_capacity_inputs(mapping: Mapping[str, str]) -> _CapacityInputs:
    retained_body_bytes = _positive_int(mapping, _MAXIMUM_RETAINED_BODY_BYTES, maximum=1073741824)
    pool_size = _positive_int(mapping, _DATABASE_POOL_SIZE, maximum=128)
    pool_timeout = _positive_int(mapping, _DATABASE_POOL_TIMEOUT, maximum=3600)
    return _CapacityInputs(retained_body_bytes, pool_size, pool_timeout)


def _admit_provider_inputs(mapping: Mapping[str, str]) -> _ProviderInputs:
    outbound_proxy_url = _optional(mapping, _OUTBOUND_PROXY_URL)
    if outbound_proxy_url is not None:
        normalized_proxy_url = normalize_outbound_proxy_url(outbound_proxy_url)
        if normalized_proxy_url is None:
            raise SettingsRejected(
                RuntimeSettingsRejection("invalid_setting_value", _OUTBOUND_PROXY_URL)
            ) from None
        outbound_proxy_url = normalized_proxy_url
    database_dsn = _required_secret(mapping, _DATABASE_DSN)
    webhook_secret = _required_secret(mapping, _WEBHOOK_SECRET)
    if not is_authentication_secret_text(webhook_secret):
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _WEBHOOK_SECRET)
        ) from None
    github_app_id = _canonical_required(mapping, _GITHUB_APP_ID, maximum=255)
    github_private_key = _required_secret(mapping, _GITHUB_PRIVATE_KEY)
    oidc_audience = _normalized_required(mapping, _OIDC_AUDIENCE, maximum=512)
    workflow_refs = _optional_workflow_refs(mapping, _OIDC_WORKFLOW_REFS)
    job_workflow_refs = _optional_workflow_refs(mapping, _OIDC_JOB_WORKFLOW_REFS)
    workflow_paths = _optional_workflow_paths(mapping, _OIDC_WORKFLOW_PATHS)
    job_workflow_paths = _optional_workflow_paths(mapping, _OIDC_JOB_WORKFLOW_PATHS)
    if not any((workflow_refs, job_workflow_refs, workflow_paths, job_workflow_paths)):
        raise SettingsRejected(
            RuntimeSettingsRejection("missing_required_setting", _OIDC_WORKFLOW_REFS)
        ) from None
    return _ProviderInputs(
        outbound_proxy_url,
        database_dsn,
        webhook_secret,
        github_app_id,
        github_private_key,
        oidc_audience,
        workflow_refs,
        job_workflow_refs,
        workflow_paths,
        job_workflow_paths,
    )


def _admit_control_plane_authority_inputs(
    mapping: Mapping[str, str],
) -> _ControlPlaneAuthorityInputs:
    actor_id = _normalized_required(mapping, _BREAK_GLASS_ACTOR_ID, maximum_utf8_bytes=80)
    if re.fullmatch("break-glass:v1:[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?", actor_id) is None:
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _BREAK_GLASS_ACTOR_ID)
        ) from None
    bearer_token = _required_secret(
        mapping,
        _BREAK_GLASS_BEARER_TOKEN,
        maximum_utf8_bytes=_MAX_BREAK_GLASS_BEARER_TOKEN_UTF8_BYTES,
    )
    if not is_break_glass_bearer_token(bearer_token):
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _BREAK_GLASS_BEARER_TOKEN)
        ) from None
    inventory_mode = mapping.get(_CONTROL_PLANE_INVENTORY_MODE, "restricted")
    if inventory_mode not in {"restricted", "app"}:
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _CONTROL_PLANE_INVENTORY_MODE)
        ) from None
    scope_mode = mapping.get(_CONTROL_PLANE_SCOPE_MODE, "restricted")
    if scope_mode not in {"restricted", "app"} or (scope_mode == "app" and inventory_mode != "app"):
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _CONTROL_PLANE_SCOPE_MODE)
        ) from None
    scopes_text = mapping.get(_CONTROL_PLANE_SCOPES, "")
    scopes: frozenset[str] | None
    if scopes_text == "" and inventory_mode == "app" and (mapping.get(_MODE) == "non_enforcing"):
        scopes = frozenset[str]()
    else:
        required_scopes = _required(mapping, _CONTROL_PLANE_SCOPES)
        scopes = _control_plane_scopes(required_scopes)
    if scopes is None:
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _CONTROL_PLANE_SCOPES)
        ) from None
    installation_ids = _inventory_installation_ids(mapping)
    if installation_ids is None or (inventory_mode == "app" and installation_ids):
        raise SettingsRejected(
            RuntimeSettingsRejection(
                "invalid_setting_value", _CONTROL_PLANE_INVENTORY_INSTALLATIONS
            )
        ) from None
    return _ControlPlaneAuthorityInputs(
        actor_id,
        bearer_token,
        scopes,
        installation_ids,
        "app" if inventory_mode == "app" else "restricted",
        "app" if scope_mode == "app" else "restricted",
    )


def _admit_production_inputs(
    mapping: Mapping[str, str], authority: _ControlPlaneAuthorityInputs
) -> _ProductionInputs:
    path_text = _canonical_bounded_text(mapping, _PRODUCTION_ADMISSION_RECEIPT_PATH, 4096)
    receipt_path = Path(path_text)
    if not receipt_path.is_absolute():
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _PRODUCTION_ADMISSION_RECEIPT_PATH)
        ) from None
    key_id = _canonical_bounded_text(mapping, _PRODUCTION_ADMISSION_KEY_ID, 128)
    public_key = _required_secret(
        mapping, _PRODUCTION_ADMISSION_PUBLIC_KEY, maximum_utf8_bytes=16384
    )
    artifact = _required(mapping, _DEPLOYED_ARTIFACT_DIGEST)
    if re.fullmatch("sha256:[0-9a-f]{64}", artifact) is None:
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _DEPLOYED_ARTIFACT_DIGEST)
        ) from None
    environment_id = _required(mapping, _ENVIRONMENT_ID)
    if re.fullmatch("[a-z][a-z0-9._-]{0,63}", environment_id) is None:
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _ENVIRONMENT_ID)
        ) from None
    scopes_text = _required(mapping, _ENFORCEMENT_SCOPES)
    scopes = _control_plane_scopes(scopes_text)
    if scopes is None or not scopes or (not scopes.issubset(authority.scopes)):
        raise SettingsRejected(
            RuntimeSettingsRejection("invalid_setting_value", _ENFORCEMENT_SCOPES)
        ) from None
    return _ProductionInputs(receipt_path, key_id, public_key, artifact, environment_id, scopes)


def _required(mapping: Mapping[str, str], name: str) -> str:
    value = mapping.get(name)
    if type(value) is not str or not value or value.isspace():
        raise SettingsRejected(RuntimeSettingsRejection("missing_required_setting", name)) from None
    if _utf8_length(value) is None:
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    return value


def _optional(mapping: Mapping[str, str], name: str) -> str | None:
    value = mapping.get(name)
    if value is None:
        return None
    if type(value) is not str or not value or value.isspace():
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    if _utf8_length(value) is None:
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    return value


def _required_secret(
    mapping: Mapping[str, str], name: str, *, maximum_utf8_bytes: int = 65536
) -> str:
    value = _required(mapping, name)
    if not is_secret_text(value, maximum_utf8_bytes=maximum_utf8_bytes):
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    return value


def _optional_secret(mapping: Mapping[str, str], name: str) -> str | None:
    value = _optional(mapping, name)
    if value is None:
        return value
    if not is_secret_text(value):
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    return value


def _positive_int(mapping: Mapping[str, str], name: str, *, maximum: int) -> int:
    value = _required(mapping, name)
    if not value.isascii() or not value.isdecimal():
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    significant = value.lstrip("0")
    maximum_text = str(maximum)
    if (
        not significant
        or len(significant) > len(maximum_text)
        or (len(significant) == len(maximum_text) and significant > maximum_text)
    ):
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    return int(significant)


def _canonical_required(mapping: Mapping[str, str], name: str, *, maximum: int) -> str:
    value = _required(mapping, name)
    if value != value.strip() or len(value) > maximum:
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    return value


def _canonical_bounded_text(mapping: Mapping[str, str], name: str, maximum_utf8_bytes: int) -> str:
    value = _required(mapping, name)
    size = _utf8_length(value)
    if value != value.strip() or size is None or size > maximum_utf8_bytes:
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    return value


def _normalized_required(
    mapping: Mapping[str, str],
    name: str,
    *,
    maximum: int | None = None,
    maximum_utf8_bytes: int | None = None,
) -> str:
    value = _required(mapping, name)
    normalized = value.strip()
    if maximum is not None and len(normalized) > maximum:
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    if maximum_utf8_bytes is not None:
        size = _utf8_length(normalized)
        if size is None or size > maximum_utf8_bytes:
            raise SettingsRejected(
                RuntimeSettingsRejection("invalid_setting_value", name)
            ) from None
    return normalized


def _workflow_refs(value: str) -> frozenset[str] | None:
    references = _bounded_csv_members(
        value,
        maximum_count=_MAX_WORKFLOW_REF_COUNT,
        maximum_member_utf8_bytes=_MAX_WORKFLOW_REF_UTF8_BYTES,
    )
    if references is None or any(not item or len(item) > 512 for item in references):
        return None
    return frozenset(references)


def _optional_workflow_refs(mapping: Mapping[str, str], name: str) -> frozenset[str]:
    if mapping.get(name) == "":
        return frozenset()
    value = _optional(mapping, name)
    if value is None:
        return frozenset()
    references = _workflow_refs(value)
    if references is None:
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    return references


def _optional_workflow_paths(mapping: Mapping[str, str], name: str) -> frozenset[str]:
    result = _optional_workflow_refs(mapping, name)
    if any(not is_workflow_path_identity(value) for value in result):
        raise SettingsRejected(RuntimeSettingsRejection("invalid_setting_value", name)) from None
    return result


def _control_plane_scopes(value: str) -> frozenset[str] | None:
    scopes = _bounded_csv_members(
        value,
        maximum_count=_MAX_CONTROL_PLANE_SCOPE_COUNT,
        maximum_member_utf8_bytes=_MAX_CONTROL_PLANE_SCOPE_UTF8_BYTES,
    )
    if scopes is None or any(not is_repository_scope(item) for item in scopes):
        return None
    return frozenset(scopes)


def _inventory_installation_ids(mapping: Mapping[str, str]) -> frozenset[int] | None:
    value = mapping.get(_CONTROL_PLANE_INVENTORY_INSTALLATIONS)
    if value is None or value == "":
        return frozenset()
    if type(value) is not str or value.isspace():
        return None
    members = _bounded_csv_members(
        value,
        maximum_count=_MAX_CONTROL_PLANE_INVENTORY_INSTALLATION_COUNT,
        maximum_member_utf8_bytes=16,
    )
    if members is None or any(
        not member.isascii()
        or not member.isdecimal()
        or member.startswith("0")
        or (not 1 <= int(member) <= 9007199254740991)
        for member in members
    ):
        return None
    installation_ids = tuple(int(member) for member in members)
    return (
        frozenset(installation_ids) if len(installation_ids) == len(set(installation_ids)) else None
    )


def _bounded_csv_members(
    value: str, *, maximum_count: int, maximum_member_utf8_bytes: int
) -> tuple[str, ...] | None:
    maximum_total_bytes = maximum_count * maximum_member_utf8_bytes + maximum_count - 1
    if len(value) > maximum_total_bytes:
        return None
    size = _utf8_length(value)
    if size is None or size > maximum_total_bytes or value.count(",") >= maximum_count:
        return None
    return tuple(item.strip() for item in value.split(","))


def _utf8_length(value: str) -> int | None:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
