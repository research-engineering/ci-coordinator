"""Immutable process-setting contracts."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

type RuntimeMode = Literal["disabled", "enforcing", "non_enforcing"]
MAX_PLAN_TTL_SECONDS = 300
_MAX_SECRET_UTF8_BYTES = 65_536
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_GITHUB_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_GITHUB_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_WORKFLOW_FILE_PATH = re.compile(r"\.github/workflows/[^/\\]+\.ya?ml")
_MIN_AUTHENTICATION_SECRET_UTF8_BYTES = 32
_MAX_BREAK_GLASS_BEARER_TOKEN_UTF8_BYTES = 4_096
_MAX_METRICS_BEARER_TOKEN_UTF8_BYTES = 4_096
_BREAK_GLASS_BEARER_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,4096}")
_METRICS_BEARER_TOKEN = re.compile(r"[A-Za-z0-9._~-]{32,4096}")


@dataclass(frozen=True, slots=True)
class SecretValue:
    """A value whose repr must never reveal its process-supplied text."""

    _value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not is_secret_text(self._value):
            raise ValueError("secret value must be bounded non-empty text")

    def reveal_for_composition(self) -> str:
        return self._value


@dataclass(frozen=True, slots=True)
class ControlPlaneIdentitySettings:
    issuer: str
    browser_client_id: str
    browser_client_secret: SecretValue = field(repr=False)
    api_client_id: str
    public_origin: str
    session_key: SecretValue = field(repr=False)
    maximum_session_seconds: int
    signing_algorithms: tuple[str, ...]
    workload_client_ids: frozenset[str]
    github_reviewer_client_id: str
    github_reviewer_client_secret: SecretValue = field(repr=False)
    profile_digest: str

    def __post_init__(self) -> None:
        if not is_oidc_issuer_url(self.issuer):
            raise ValueError("Keycloak issuer is invalid")
        for value, name in (
            (self.browser_client_id, "browser client id"),
            (self.api_client_id, "API client id"),
            (self.github_reviewer_client_id, "GitHub reviewer client id"),
        ):
            if type(value) is not str or re.fullmatch(r"[A-Za-z0-9._-]{1,255}", value) is None:
                raise ValueError(f"control-plane {name} is invalid")
        _require_secret(self.browser_client_secret, "Keycloak browser client secret")
        _require_secret(self.session_key, "control-plane session key")
        _require_secret(self.github_reviewer_client_secret, "GitHub reviewer client secret")
        browser_secret = self.browser_client_secret.reveal_for_composition()
        reviewer_secret = self.github_reviewer_client_secret.reveal_for_composition()
        if not is_authentication_secret_text(browser_secret, maximum_utf8_bytes=4_096):
            raise ValueError("Keycloak browser client secret is too weak")
        if not is_authentication_secret_text(reviewer_secret, maximum_utf8_bytes=4_096):
            raise ValueError("GitHub reviewer client secret is too weak")
        if not is_browser_session_key(self.session_key.reveal_for_composition()):
            raise ValueError("control-plane session key must be canonical 256-bit base64url")
        if (
            len(
                {
                    browser_secret,
                    self.session_key.reveal_for_composition(),
                    reviewer_secret,
                }
            )
            != 3
        ):
            raise ValueError("control-plane credentials must be pairwise distinct")
        if not is_browser_public_origin(self.public_origin):
            raise ValueError("control-plane public origin is invalid")
        if (
            type(self.maximum_session_seconds) is not int
            or not 60 <= self.maximum_session_seconds <= 900
        ):
            raise ValueError("control-plane session maximum must be in [60, 900]")
        if self.signing_algorithms != ("RS256",):
            raise ValueError("control-plane signing algorithms differ from the admitted profile")
        if (
            type(self.workload_client_ids) is not frozenset
            or len(self.workload_client_ids) > 128
            or any(
                type(value) is not str or re.fullmatch(r"[A-Za-z0-9._-]{1,255}", value) is None
                for value in self.workload_client_ids
            )
        ):
            raise ValueError("workload client allowlist is invalid")
        if (
            type(self.profile_digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.profile_digest) is None
        ):
            raise ValueError("control-plane identity profile digest is invalid")

    @property
    def callback_uri(self) -> str:
        return f"{self.public_origin}/api/v1/auth/keycloak/callback"

    @property
    def post_logout_uri(self) -> str:
        return f"{self.public_origin}/workbench"

    @property
    def back_channel_logout_uri(self) -> str:
        return f"{self.public_origin}/api/v1/auth/keycloak/backchannel-logout"

    @property
    def reviewer_callback_uri(self) -> str:
        return f"{self.public_origin}/api/v1/repository-attestations/github/callback"

    @property
    def secure_cookies(self) -> bool:
        return self.public_origin.startswith("https://")

    @property
    def session_cookie_name(self) -> str:
        return (
            "__Host-ci_coordinator_session" if self.secure_cookies else "ci_coordinator_dev_session"
        )

    @property
    def transaction_cookie_name(self) -> str:
        return (
            "__Secure-ci_coordinator_oidc_transaction"
            if self.secure_cookies
            else "ci_coordinator_dev_oidc_transaction"
        )

    @property
    def reviewer_transaction_cookie_name(self) -> str:
        return (
            "__Secure-ci_coordinator_reviewer_transaction"
            if self.secure_cookies
            else "ci_coordinator_dev_reviewer_transaction"
        )


@dataclass(frozen=True, slots=True)
class DisabledRuntimeSettings:
    bind_host: str
    bind_port: int
    shutdown_timeout_seconds: int
    mode: Literal["disabled"] = field(default="disabled", init=False)

    def __post_init__(self) -> None:
        _require_common_runtime_settings(
            self.bind_host,
            self.bind_port,
            self.shutdown_timeout_seconds,
        )


@dataclass(frozen=True, slots=True)
class ConnectedRuntimeSettings:
    bind_host: str
    bind_port: int
    shutdown_timeout_seconds: int
    request_timeout_seconds: int
    maximum_retained_body_bytes: int
    database_pool_size: int
    database_pool_timeout_seconds: int
    plan_ttl_seconds: int
    reconciliation_interval_seconds: int
    reconciliation_startup_timeout_seconds: int
    reconciliation_scan_limit: int
    shadow_rollout_profile_id: str
    database_dsn: SecretValue = field(repr=False)
    webhook_secret: SecretValue = field(repr=False)
    github_app_id: str
    github_private_key: SecretValue = field(repr=False)
    plan_signing_key_id: str
    plan_signing_private_key: SecretValue = field(repr=False)
    oidc_audience: str
    oidc_allowed_workflow_refs: frozenset[str]
    oidc_allowed_job_workflow_refs: frozenset[str]
    oidc_allowed_workflow_paths: frozenset[str]
    oidc_allowed_job_workflow_paths: frozenset[str]
    break_glass_actor_id: str
    break_glass_bearer_token: SecretValue = field(repr=False)
    metrics_bearer_token: SecretValue = field(repr=False)
    control_plane_scope_allowlist: frozenset[str]
    control_plane_inventory_installation_allowlist: frozenset[int]
    control_plane_inventory_mode: Literal["restricted", "app"] = field(
        default="restricted", kw_only=True
    )
    control_plane_scope_mode: Literal["restricted", "app"] = field(
        default="restricted", kw_only=True
    )
    control_plane_identity: ControlPlaneIdentitySettings | None
    outbound_proxy_url: str | None

    def __post_init__(self) -> None:
        _require_common_runtime_settings(
            self.bind_host,
            self.bind_port,
            self.shutdown_timeout_seconds,
        )
        _require_connected_bounds(self)
        _require_secret(self.database_dsn, "database DSN")
        _require_secret(self.webhook_secret, "webhook secret")
        if not is_authentication_secret_text(self.webhook_secret.reveal_for_composition()):
            raise ValueError("webhook secret must satisfy the authentication byte bounds")
        _require_secret(self.github_private_key, "GitHub private key")
        _require_secret(self.plan_signing_private_key, "plan signing private key")
        _require_secret(self.break_glass_bearer_token, "break-glass bearer token")
        _require_secret(self.metrics_bearer_token, "metrics bearer token")
        _require_github_identity(self)
        _require_plan_signing_identity(self)
        _require_oidc_identity(self)
        _require_control_plane_authority(self)
        if not is_metrics_bearer_token(self.metrics_bearer_token.reveal_for_composition()):
            raise ValueError("metrics bearer token must satisfy the authentication byte bounds")
        if (
            self.control_plane_identity is not None
            and type(self.control_plane_identity) is not ControlPlaneIdentitySettings
        ):
            raise TypeError("control-plane identity settings must be exact or absent")
        _require_distinct_metrics_bearer(self)
        if self.outbound_proxy_url is not None and not is_outbound_proxy_url(
            self.outbound_proxy_url
        ):
            raise ValueError("outbound proxy URL is invalid")


@dataclass(frozen=True, slots=True)
class NonEnforcingRuntimeSettings(ConnectedRuntimeSettings):
    mode: Literal["non_enforcing"] = field(default="non_enforcing", init=False)


@dataclass(frozen=True, slots=True)
class EnforcingRuntimeSettings(ConnectedRuntimeSettings):
    production_admission_receipt_path: Path
    production_admission_key_id: str
    production_admission_public_key_pem: str = field(repr=False)
    deployed_artifact_digest: str
    environment_id: str
    enforcement_scope_allowlist: frozenset[str]
    mode: Literal["enforcing"] = field(default="enforcing", init=False)

    def __post_init__(self) -> None:
        super(EnforcingRuntimeSettings, self).__post_init__()
        _require_production_admission_settings(self)


type RuntimeSettings = (
    DisabledRuntimeSettings | EnforcingRuntimeSettings | NonEnforcingRuntimeSettings
)


@dataclass(frozen=True, slots=True)
class RuntimeSettingsRejection:
    code: Literal[
        "missing_required_setting",
        "invalid_setting_value",
        "incomplete_signing_configuration",
    ]
    field_name: str


@dataclass(frozen=True, slots=True)
class RuntimeSettingsProjection:
    mode: RuntimeMode
    bind_host: str
    bind_port: int
    shutdown_timeout_seconds: int
    request_timeout_seconds: int | None
    maximum_retained_body_bytes: int | None
    database_pool_size: int | None
    database_pool_timeout_seconds: int | None
    plan_ttl_seconds: int | None
    reconciliation_interval_seconds: int | None
    reconciliation_startup_timeout_seconds: int | None
    reconciliation_scan_limit: int | None
    shadow_rollout_profile_id: str | None
    github_app_id: str | None
    plan_signing_key_id: str | None
    oidc_allowed_workflow_ref_count: int
    oidc_allowed_job_workflow_ref_count: int
    oidc_allowed_workflow_path_count: int
    oidc_allowed_job_workflow_path_count: int
    break_glass_actor_id: str | None
    control_plane_scope_count: int
    control_plane_inventory_installation_count: int
    control_plane_inventory_mode: Literal["restricted", "app", "disabled"]
    control_plane_scope_mode: Literal["restricted", "app", "disabled"]
    control_plane_identity_mode: Literal["disabled", "keycloak"]
    control_plane_public_origin: str | None
    control_plane_session_maximum_seconds: int | None
    control_plane_profile_digest: str | None
    production_admission_key_id: str | None
    deployed_artifact_digest: str | None
    environment_id: str | None
    enforcement_scope_count: int
    has_database_dsn: bool
    has_webhook_secret: bool
    has_github_private_key: bool
    has_plan_signing_private_key: bool
    has_break_glass_bearer_token: bool
    has_metrics_bearer_token: bool
    has_control_plane_browser_client_secret: bool
    has_control_plane_session_key: bool
    has_github_reviewer_client_secret: bool
    outbound_proxy_configured: bool
    has_production_admission_receipt: bool
    has_production_admission_public_key: bool


def _require_common_runtime_settings(
    bind_host: object,
    bind_port: object,
    shutdown_timeout_seconds: object,
) -> None:
    normalized_host = normalize_bind_host(bind_host)
    if normalized_host is None or normalized_host != bind_host:
        raise ValueError("bind host must be a canonical IP address or DNS name")
    if type(bind_port) is not int or not 1 <= bind_port <= 65535:
        raise ValueError("bind port must be in range")
    _require_timeout(shutdown_timeout_seconds, "shutdown timeout")


def _require_connected_bounds(settings: ConnectedRuntimeSettings) -> None:
    _require_timeout(settings.request_timeout_seconds, "request timeout")
    if (
        type(settings.maximum_retained_body_bytes) is not int
        or not 1 <= settings.maximum_retained_body_bytes <= 1_073_741_824
    ):
        raise ValueError("retained-body budget must be an integer in [1, 1073741824]")
    if type(settings.database_pool_size) is not int or not 1 <= settings.database_pool_size <= 128:
        raise ValueError("database pool size must be an integer in [1, 128]")
    _require_timeout(settings.database_pool_timeout_seconds, "database pool timeout")
    if (
        type(settings.plan_ttl_seconds) is not int
        or not 1 <= settings.plan_ttl_seconds <= MAX_PLAN_TTL_SECONDS
    ):
        raise ValueError("plan TTL must match the target lifetime bound")
    _require_timeout(settings.reconciliation_interval_seconds, "reconciliation interval")
    _require_timeout(
        settings.reconciliation_startup_timeout_seconds,
        "reconciliation startup timeout",
    )
    if (
        type(settings.reconciliation_scan_limit) is not int
        or not 1 <= settings.reconciliation_scan_limit <= 1_000
    ):
        raise ValueError("reconciliation scan limit must be an integer in [1, 1000]")
    if (
        type(settings.shadow_rollout_profile_id) is not str
        or len(settings.shadow_rollout_profile_id) != 64
        or any(
            character not in "0123456789abcdef" for character in settings.shadow_rollout_profile_id
        )
    ):
        raise ValueError("shadow rollout profile id must be a lowercase SHA-256 digest")


def _require_github_identity(settings: ConnectedRuntimeSettings) -> None:
    if (
        type(settings.github_app_id) is not str
        or not settings.github_app_id.strip()
        or settings.github_app_id != settings.github_app_id.strip()
        or len(settings.github_app_id) > 255
        or _utf8_length(settings.github_app_id) is None
    ):
        raise ValueError("GitHub app id must be bounded non-empty text")


def _require_plan_signing_identity(settings: ConnectedRuntimeSettings) -> None:
    if (
        type(settings.plan_signing_key_id) is not str
        or not settings.plan_signing_key_id
        or len(settings.plan_signing_key_id) > 255
        or _utf8_length(settings.plan_signing_key_id) is None
    ):
        raise ValueError("plan signing key id must be bounded non-empty text")


def _require_oidc_identity(settings: ConnectedRuntimeSettings) -> None:
    if (
        type(settings.oidc_audience) is not str
        or not settings.oidc_audience
        or settings.oidc_audience != settings.oidc_audience.strip()
        or len(settings.oidc_audience) > 512
        or _utf8_length(settings.oidc_audience) is None
    ):
        raise ValueError("OIDC audience must be bounded non-empty text")
    for references in (
        settings.oidc_allowed_workflow_refs,
        settings.oidc_allowed_job_workflow_refs,
        settings.oidc_allowed_workflow_paths,
        settings.oidc_allowed_job_workflow_paths,
    ):
        if type(references) is not frozenset or any(
            type(value) is not str
            or not value
            or value != value.strip()
            or len(value) > 512
            or _utf8_length(value) is None
            for value in references
        ):
            raise ValueError("OIDC workflow references must be bounded non-empty text")
        if len(references) > 64:
            raise ValueError("OIDC workflow reference count exceeds the admitted bound")
    if any(
        not is_workflow_path_identity(value)
        for values in (
            settings.oidc_allowed_workflow_paths,
            settings.oidc_allowed_job_workflow_paths,
        )
        for value in values
    ):
        raise ValueError("OIDC workflow paths must identify exact repository workflow files")
    if not any(
        (
            settings.oidc_allowed_workflow_refs,
            settings.oidc_allowed_job_workflow_refs,
            settings.oidc_allowed_workflow_paths,
            settings.oidc_allowed_job_workflow_paths,
        )
    ):
        raise ValueError("at least one OIDC workflow reference is required")


def is_workflow_path_identity(value: str) -> bool:
    parts = value.split("/")
    return (
        len(value.encode("utf-8")) <= 512
        and len(parts) == 5
        and _GITHUB_OWNER.fullmatch(parts[0]) is not None
        and _GITHUB_REPOSITORY.fullmatch(parts[1]) is not None
        and _WORKFLOW_FILE_PATH.fullmatch("/".join(parts[2:])) is not None
    )


def _require_control_plane_authority(settings: ConnectedRuntimeSettings) -> None:
    if (
        type(settings.break_glass_actor_id) is not str
        or re.fullmatch(
            r"break-glass:v1:[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?",
            settings.break_glass_actor_id,
        )
        is None
    ):
        raise ValueError("break-glass actor id must be canonical")
    if (
        type(settings.control_plane_scope_allowlist) is not frozenset
        or (
            not settings.control_plane_scope_allowlist
            and (
                settings.control_plane_inventory_mode != "app"
                or isinstance(settings, EnforcingRuntimeSettings)
            )
        )
        or any(not is_repository_scope(value) for value in settings.control_plane_scope_allowlist)
    ):
        raise ValueError("control-plane scopes must use installation:repository identities")
    if len(settings.control_plane_scope_allowlist) > 1024:
        raise ValueError("control-plane scope count exceeds the admitted bound")
    installation_ids = settings.control_plane_inventory_installation_allowlist
    if settings.control_plane_inventory_mode not in {"restricted", "app"}:
        raise ValueError("control-plane inventory mode is invalid")
    if settings.control_plane_scope_mode not in {"restricted", "app"} or (
        settings.control_plane_scope_mode == "app"
        and settings.control_plane_inventory_mode != "app"
    ):
        raise ValueError("App scope mode requires App inventory mode")
    if settings.control_plane_inventory_mode == "app" and installation_ids:
        raise ValueError("App inventory cannot override an installation restriction")
    if (
        type(installation_ids) is not frozenset
        or len(installation_ids) > 64
        or any(
            type(value) is not int or not 1 <= value <= 9_007_199_254_740_991
            for value in installation_ids
        )
    ):
        raise ValueError("control-plane inventory installation grant is invalid")
    if not is_break_glass_bearer_token(settings.break_glass_bearer_token.reveal_for_composition()):
        raise ValueError("break-glass bearer token must satisfy the authentication byte bounds")


def _require_production_admission_settings(settings: EnforcingRuntimeSettings) -> None:
    path = settings.production_admission_receipt_path
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or not 1 <= len(str(path).encode("utf-8")) <= 4_096
    ):
        raise ValueError("production admission receipt path must be bounded and absolute")
    if (
        type(settings.production_admission_key_id) is not str
        or not settings.production_admission_key_id
        or len(settings.production_admission_key_id.encode("utf-8")) > 128
    ):
        raise ValueError("production admission key id must be bounded non-empty text")
    if not is_secret_text(
        settings.production_admission_public_key_pem,
        maximum_utf8_bytes=16_384,
    ):
        raise ValueError("production admission public key must be bounded PEM text")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", settings.deployed_artifact_digest) is None:
        raise ValueError("deployed artifact digest must be canonical SHA-256")
    if re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", settings.environment_id) is None:
        raise ValueError("environment id must be canonical bounded text")
    scopes = settings.enforcement_scope_allowlist
    if (
        type(scopes) is not frozenset
        or not scopes
        or len(scopes) > 1_024
        or any(not is_repository_scope(value) for value in scopes)
        or not scopes.issubset(settings.control_plane_scope_allowlist)
    ):
        raise ValueError("enforcement scopes must be a control-plane-authorized subset")


def _require_timeout(value: object, name: str) -> None:
    if type(value) is not int or not 1 <= value <= 3600:
        raise ValueError(f"{name} must be an integer in range")


def _require_secret(value: object, name: str) -> None:
    if type(value) is not SecretValue:
        raise ValueError(f"{name} must be an admitted secret value")


def is_repository_scope(value: object) -> bool:
    if type(value) is not str or len(value) > 64:
        return False
    installation_id, separator, repository_id = value.partition(":")
    return (
        separator == ":"
        and installation_id.isascii()
        and installation_id.isdecimal()
        and repository_id.isascii()
        and repository_id.isdecimal()
        and 0 < int(installation_id) <= 9_007_199_254_740_991
        and 0 < int(repository_id) <= 9_007_199_254_740_991
    )


def is_secret_text(
    value: object,
    *,
    maximum_utf8_bytes: int = _MAX_SECRET_UTF8_BYTES,
) -> bool:
    if type(maximum_utf8_bytes) is not int or maximum_utf8_bytes < 1:
        raise ValueError("secret byte bound must be a positive integer")
    return (
        type(value) is str
        and bool(value)
        and "\x00" not in value
        and (size := _utf8_length(value)) is not None
        and size <= maximum_utf8_bytes
    )


def is_authentication_secret_text(
    value: object,
    *,
    maximum_utf8_bytes: int = _MAX_SECRET_UTF8_BYTES,
) -> bool:
    return (
        type(value) is str
        and is_secret_text(value, maximum_utf8_bytes=maximum_utf8_bytes)
        and len(value.encode("utf-8")) >= _MIN_AUTHENTICATION_SECRET_UTF8_BYTES
    )


def is_break_glass_bearer_token(value: object) -> bool:
    return (
        type(value) is str
        and _BREAK_GLASS_BEARER_TOKEN.fullmatch(value) is not None
        and is_authentication_secret_text(
            value,
            maximum_utf8_bytes=_MAX_BREAK_GLASS_BEARER_TOKEN_UTF8_BYTES,
        )
    )


def is_metrics_bearer_token(value: object) -> bool:
    return (
        type(value) is str
        and _METRICS_BEARER_TOKEN.fullmatch(value) is not None
        and is_authentication_secret_text(
            value,
            maximum_utf8_bytes=_MAX_METRICS_BEARER_TOKEN_UTF8_BYTES,
        )
    )


def _require_distinct_metrics_bearer(settings: ConnectedRuntimeSettings) -> None:
    metrics_bearer = settings.metrics_bearer_token.reveal_for_composition()
    forbidden_reuse = {
        settings.database_dsn.reveal_for_composition(),
        settings.github_private_key.reveal_for_composition(),
        settings.break_glass_bearer_token.reveal_for_composition(),
        settings.plan_signing_private_key.reveal_for_composition(),
        settings.webhook_secret.reveal_for_composition(),
    }
    if settings.control_plane_identity is not None:
        forbidden_reuse.update(
            {
                settings.control_plane_identity.browser_client_secret.reveal_for_composition(),
                settings.control_plane_identity.github_reviewer_client_secret.reveal_for_composition(),
                settings.control_plane_identity.session_key.reveal_for_composition(),
            }
        )
    if metrics_bearer in forbidden_reuse:
        raise ValueError("metrics bearer token must be distinct from every credential secret")


def _utf8_length(value: str) -> int | None:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def decode_control_plane_session_key(settings: ControlPlaneIdentitySettings) -> bytes:
    if type(settings) is not ControlPlaneIdentitySettings:
        raise TypeError("control-plane session key requires exact settings")
    decoded = _decode_256_bit_base64url(settings.session_key.reveal_for_composition())
    if decoded is None:
        raise ValueError("browser session key is invalid")
    return decoded


def is_browser_session_key(value: object) -> bool:
    return _decode_256_bit_base64url(value) is not None


def _decode_256_bit_base64url(value: object) -> bytes | None:
    if type(value) is not str or len(value) != 43:
        return None
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        return None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    return decoded if len(decoded) == 32 and canonical == value else None


def is_browser_public_origin(value: object) -> bool:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 512
        or not value.isascii()
        or value != value.lower()
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        return False
    canonical_host = _canonical_origin_host(hostname)
    if canonical_host is None:
        return False
    loopback_http = parsed.scheme == "http" and canonical_host in {
        "127.0.0.1",
        "::1",
        "localhost",
    }
    authority = f"[{canonical_host}]" if ":" in canonical_host else canonical_host
    if port is not None:
        authority = f"{authority}:{port}"
    return not (
        (parsed.scheme != "https" and not loopback_http)
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port == 0
        or (parsed.scheme == "https" and port == 443)
        or (parsed.scheme == "http" and port == 80)
        or value != f"{parsed.scheme}://{authority}"
    )


def is_oidc_issuer_url(value: object) -> bool:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 512
        or not value.isascii()
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        return False
    canonical_host = _canonical_origin_host(hostname)
    if canonical_host is None or hostname != canonical_host:
        return False
    authority = f"[{canonical_host}]" if ":" in canonical_host else canonical_host
    if port is not None:
        authority = f"{authority}:{port}"
    return not (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port in {0, 443}
        or value.endswith("/")
        or value != f"https://{authority}{parsed.path}"
    )


def is_outbound_proxy_url(value: object) -> bool:
    return type(value) is str and normalize_outbound_proxy_url(value) == value


def normalize_bind_host(value: object) -> str | None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 253
        or not value.isascii()
        or value != value.lower()
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
        or "%" in value
    ):
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if all(character in "0123456789." for character in value) or any(
            _DNS_LABEL.fullmatch(label) is None for label in value.split(".")
        ):
            return None
        return value
    canonical = address.compressed
    return canonical if value == canonical else None


def normalize_outbound_proxy_url(value: object) -> str | None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 512
        or not value.isascii()
        or value != value.lower()
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        return None
    canonical_host = _canonical_origin_host(hostname)
    if canonical_host is None:
        return None
    authority = f"[{canonical_host}]" if ":" in canonical_host else canonical_host
    if port is not None:
        authority = f"{authority}:{port}"
    if (
        parsed.scheme != "http"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port == 0
        or port == 80
        or value != f"http://{authority}{parsed.path}"
    ):
        return None
    return f"http://{authority}"


def _canonical_origin_host(value: str | None) -> str | None:
    if value is None or not value or "%" in value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if (
            len(value) > 253
            or all(character in "0123456789." for character in value)
            or any(_DNS_LABEL.fullmatch(label) is None for label in value.split("."))
        ):
            return None
        return value
    if address.is_unspecified:
        return None
    canonical = address.compressed
    return canonical if value == canonical else None
