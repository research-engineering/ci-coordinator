"""Immutable authority values for the organization control plane."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, Literal
from urllib.parse import urlsplit

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type ControlPlaneRole = Literal["activate", "audit", "configure", "override", "read"]
type CredentialPlane = Literal[
    "emergency-safety",
    "human-administration",
    "machine-administration",
    "repository-attestation",
]
type IdentityRejectionCode = Literal[
    "authority_profile_changed",
    "forbidden",
    "invalid_login",
    "invalid_logout",
    "invalid_machine_token",
    "logout_replayed",
    "overloaded",
    "unauthenticated",
]
type IdentityUnavailableCode = Literal[
    "identity_provider_unavailable",
    "logout_store_unavailable",
    "machine_verifier_unavailable",
    "session_store_unavailable",
]
type ReviewerPermission = Literal["admin", "maintain"]

CONTROL_PLANE_ROLES: Final[frozenset[ControlPlaneRole]] = frozenset(
    {"activate", "audit", "configure", "override", "read"}
)
_ACTOR_ID = re.compile(
    r"keycloak-(?:human|workload):v1:[0-9a-f]{64}"
    r"|github-reviewer:v1:[1-9][0-9]{0,15}"
    r"|break-glass:v1:[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?"
)
_GIT_REVISION = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_GITHUB_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_PROPOSAL_MANIFEST_ID = re.compile(r"proposal:[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_SESSION_SECONDS: Final = 900
_MAX_OPERATION_ID_BYTES: Final = 256
_MACHINE_CREDENTIAL_KIND: Final = "access"


@dataclass(frozen=True, slots=True)
class DisplayMetadata:
    preferred_username: str | None = None
    display_name: str | None = None

    def __post_init__(self) -> None:
        _optional_text(self.preferred_username, "preferred username", maximum_bytes=256)
        _optional_text(self.display_name, "display name", maximum_bytes=512)


@dataclass(frozen=True, slots=True)
class KeycloakBrowserEvidence:
    issuer: str
    subject: str
    keycloak_session_id: str
    roles: frozenset[ControlPlaneRole]
    issued_at: datetime
    expires_at: datetime
    display: DisplayMetadata = field(default_factory=DisplayMetadata)

    def __post_init__(self) -> None:
        _issuer(self.issuer)
        _text(self.subject, "Keycloak subject", maximum_bytes=512)
        _text(self.keycloak_session_id, "Keycloak session id", maximum_bytes=512)
        _roles(self.roles)
        issued_at, expires_at = _time_interval(
            self.issued_at,
            self.expires_at,
            "Keycloak browser evidence",
        )
        if type(self.display) is not DisplayMetadata:
            raise TypeError("Keycloak display metadata must be exact")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class KeycloakHumanPrincipal:
    issuer: str
    subject: str
    keycloak_session_id: str
    roles: frozenset[ControlPlaneRole]
    session_handle: str = field(repr=False)
    expires_at: datetime
    authority_profile_digest: str
    display: DisplayMetadata = field(default_factory=DisplayMetadata)

    def __post_init__(self) -> None:
        _issuer(self.issuer)
        _text(self.subject, "Keycloak subject", maximum_bytes=512)
        _text(self.keycloak_session_id, "Keycloak session id", maximum_bytes=512)
        _roles(self.roles)
        _opaque_handle(self.session_handle, "browser session handle")
        object.__setattr__(self, "expires_at", _aware_utc(self.expires_at, "session expiry"))
        _digest(self.authority_profile_digest, "authority profile digest")
        if type(self.display) is not DisplayMetadata:
            raise TypeError("Keycloak display metadata must be exact")

    @property
    def actor_id(self) -> str:
        return derive_human_actor_id(self.issuer, self.subject)

    @property
    def credential_plane(self) -> Literal["human-administration"]:
        return "human-administration"


@dataclass(frozen=True, slots=True)
class KeycloakWorkloadPrincipal:
    issuer: str
    authorized_party: str
    subject: str
    roles: frozenset[ControlPlaneRole]
    issued_at: datetime
    expires_at: datetime
    authority_profile_digest: str

    def __post_init__(self) -> None:
        _issuer(self.issuer)
        _text(self.authorized_party, "Keycloak authorized party", maximum_bytes=256)
        _text(self.subject, "Keycloak subject", maximum_bytes=512)
        _roles(self.roles)
        issued_at, expires_at = _time_interval(
            self.issued_at,
            self.expires_at,
            "Keycloak workload principal",
        )
        _digest(self.authority_profile_digest, "authority profile digest")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)

    @property
    def actor_id(self) -> str:
        return derive_workload_actor_id(self.issuer, self.authorized_party, self.subject)

    @property
    def credential_plane(self) -> Literal["machine-administration"]:
        return "machine-administration"


@dataclass(frozen=True, slots=True)
class GitHubReviewerEvidence:
    user_id: int
    login: str
    permission: ReviewerPermission

    def __post_init__(self) -> None:
        _positive_safe_integer(self.user_id, "GitHub reviewer id")
        if type(self.login) is not str or _GITHUB_LOGIN.fullmatch(self.login) is None:
            raise ValueError("GitHub reviewer login is not canonical")
        if self.permission not in {"admin", "maintain"}:
            raise ValueError("GitHub reviewer permission is not admitted")


@dataclass(frozen=True, slots=True)
class GitHubReviewerPrincipal:
    evidence: GitHubReviewerEvidence
    observed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if type(self.evidence) is not GitHubReviewerEvidence:
            raise TypeError("GitHub reviewer principal requires exact identity evidence")
        observed_at, expires_at = _time_interval(
            self.observed_at,
            self.expires_at,
            "GitHub reviewer observation",
        )
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "expires_at", expires_at)

    @property
    def actor_id(self) -> str:
        return f"github-reviewer:v1:{self.evidence.user_id}"

    @property
    def user_id(self) -> int:
        return self.evidence.user_id

    @property
    def login(self) -> str:
        return self.evidence.login

    @property
    def permission(self) -> ReviewerPermission:
        return self.evidence.permission

    @property
    def credential_plane(self) -> Literal["repository-attestation"]:
        return "repository-attestation"


@dataclass(frozen=True, slots=True)
class BreakGlassPrincipal:
    deployment_identity: str

    def __post_init__(self) -> None:
        _text(self.deployment_identity, "break-glass deployment identity", maximum_bytes=64)
        if _ACTOR_ID.fullmatch(self.actor_id) is None:
            raise ValueError("break-glass deployment identity is not canonical")

    @property
    def actor_id(self) -> str:
        return f"break-glass:v1:{self.deployment_identity}"

    @property
    def credential_plane(self) -> Literal["emergency-safety"]:
        return "emergency-safety"


type ControlPlanePrincipal = (
    KeycloakHumanPrincipal
    | KeycloakWorkloadPrincipal
    | GitHubReviewerPrincipal
    | BreakGlassPrincipal
)


@dataclass(frozen=True, slots=True)
class ControlPlaneSessionRecord:
    handle_digest: bytes = field(repr=False)
    issuer: str
    subject: str
    keycloak_session_id: str
    actor_id: str
    roles: frozenset[ControlPlaneRole]
    authority_profile_digest: str
    issued_at: datetime
    expires_at: datetime
    display: DisplayMetadata = field(default_factory=DisplayMetadata)

    def __post_init__(self) -> None:
        _bytes(self.handle_digest, "session handle digest", exact=32)
        _issuer(self.issuer)
        _text(self.subject, "Keycloak subject", maximum_bytes=512)
        _text(self.keycloak_session_id, "Keycloak session id", maximum_bytes=512)
        if self.actor_id != derive_human_actor_id(self.issuer, self.subject):
            raise ValueError("session actor does not match its immutable identity")
        _roles(self.roles)
        _digest(self.authority_profile_digest, "authority profile digest")
        issued_at, expires_at = _time_interval(
            self.issued_at,
            self.expires_at,
            "control-plane session",
        )
        if expires_at - issued_at > timedelta(seconds=_MAX_SESSION_SECONDS):
            raise ValueError("control-plane session exceeds the 15 minute maximum")
        if type(self.display) is not DisplayMetadata:
            raise TypeError("session display metadata must be exact")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class BrowserOAuthTransaction:
    state_digest: bytes = field(repr=False)
    nonce: str = field(repr=False)
    code_verifier: str = field(repr=False)
    authority_profile_digest: str
    issued_at: datetime

    def __post_init__(self) -> None:
        _bytes(self.state_digest, "OAuth state digest", exact=32)
        _opaque_handle(self.nonce, "OIDC nonce")
        _opaque_handle(self.code_verifier, "PKCE verifier")
        _digest(self.authority_profile_digest, "authority profile digest")
        object.__setattr__(self, "issued_at", _aware_utc(self.issued_at, "OAuth issue time"))


@dataclass(frozen=True, slots=True)
class ReviewerStepUpBinding:
    session_handle_digest: bytes = field(repr=False)
    initiating_actor: str
    scope: RepositoryScope
    operation_id: str
    proposal_manifest_id: str
    revision: str
    proposal_digest: str
    expected_active_epoch_id: str | None
    expected_active_revision: int | None
    authority_profile_digest: str
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        _bytes(self.session_handle_digest, "reviewer session digest", exact=32)
        if (
            type(self.initiating_actor) is not str
            or _ACTOR_ID.fullmatch(self.initiating_actor) is None
        ):
            raise ValueError("reviewer initiating actor is not canonical")
        if not self.initiating_actor.startswith("keycloak-human:v1:"):
            raise ValueError("reviewer step-up requires a human Keycloak actor")
        if type(self.scope) is not RepositoryScope:
            raise TypeError("reviewer step-up scope must be exact")
        _text(self.operation_id, "reviewer operation id", maximum_bytes=_MAX_OPERATION_ID_BYTES)
        if "\0" in self.operation_id:
            raise ValueError("reviewer operation id cannot contain NUL")
        if (
            type(self.proposal_manifest_id) is not str
            or _PROPOSAL_MANIFEST_ID.fullmatch(self.proposal_manifest_id) is None
        ):
            raise ValueError("reviewer proposal manifest id is invalid")
        if type(self.revision) is not str or _GIT_REVISION.fullmatch(self.revision) is None:
            raise ValueError("reviewer revision must be an exact Git object id")
        _digest(self.proposal_digest, "proposal digest")
        if (self.expected_active_epoch_id is None) != (self.expected_active_revision is None):
            raise ValueError("expected active identity must be wholly present or absent")
        if self.expected_active_epoch_id is not None:
            _digest(self.expected_active_epoch_id, "expected active epoch")
            _positive_safe_integer(self.expected_active_revision, "expected active revision")
        _digest(self.authority_profile_digest, "authority profile digest")
        issued_at, expires_at = _time_interval(
            self.issued_at,
            self.expires_at,
            "reviewer step-up binding",
        )
        if expires_at - issued_at > timedelta(seconds=300):
            raise ValueError("reviewer step-up exceeds the five minute maximum")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class ReviewerOAuthTransaction:
    state_digest: bytes = field(repr=False)
    code_verifier: str = field(repr=False)
    binding: ReviewerStepUpBinding

    def __post_init__(self) -> None:
        _bytes(self.state_digest, "reviewer OAuth state digest", exact=32)
        _opaque_handle(self.code_verifier, "reviewer PKCE verifier")
        if type(self.binding) is not ReviewerStepUpBinding:
            raise TypeError("reviewer OAuth binding must be exact")


@dataclass(frozen=True, slots=True)
class KeycloakMachineTokenEvidence:
    token_kind: Literal["access"]
    issuer: str
    audience: frozenset[str]
    authorized_party: str
    subject: str
    roles: frozenset[ControlPlaneRole]
    issued_at: datetime
    not_before: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.token_kind != _MACHINE_CREDENTIAL_KIND:
            raise ValueError("machine credential must be an access token")
        _issuer(self.issuer)
        if type(self.audience) is not frozenset or not 1 <= len(self.audience) <= 16:
            raise ValueError("machine token audience must be a bounded exact set")
        for audience in self.audience:
            _text(audience, "machine token audience", maximum_bytes=512)
        _text(self.authorized_party, "machine authorized party", maximum_bytes=256)
        _text(self.subject, "machine subject", maximum_bytes=512)
        _roles(self.roles)
        issued_at, expires_at = _time_interval(
            self.issued_at,
            self.expires_at,
            "machine token evidence",
        )
        not_before = _aware_utc(self.not_before, "machine token not-before")
        if not_before > expires_at:
            raise ValueError("machine token not-before follows its expiry")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "not_before", not_before)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class BackChannelLogoutTarget:
    keycloak_session_id: str | None = None
    subject: str | None = None

    def __post_init__(self) -> None:
        if self.keycloak_session_id is None and self.subject is None:
            raise ValueError("back-channel logout requires sid, subject, or both")
        _optional_text(self.keycloak_session_id, "logout session id", maximum_bytes=512)
        _optional_text(self.subject, "logout subject", maximum_bytes=512)


@dataclass(frozen=True, slots=True)
class BackChannelLogoutEvidence:
    issuer: str
    token_id: str
    issued_at: datetime
    expires_at: datetime
    target: BackChannelLogoutTarget

    def __post_init__(self) -> None:
        _issuer(self.issuer)
        _text(self.token_id, "logout token id", maximum_bytes=512)
        issued_at, expires_at = _time_interval(
            self.issued_at,
            self.expires_at,
            "back-channel logout token",
        )
        if expires_at - issued_at > timedelta(seconds=120):
            raise ValueError("back-channel logout token exceeds its lifetime maximum")
        if type(self.target) is not BackChannelLogoutTarget:
            raise TypeError("back-channel logout target must be exact")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class IdentityRejected:
    code: IdentityRejectionCode
    missing_roles: tuple[ControlPlaneRole, ...] = ()

    def __post_init__(self) -> None:
        if self.code not in {
            "authority_profile_changed",
            "forbidden",
            "invalid_login",
            "invalid_logout",
            "invalid_machine_token",
            "logout_replayed",
            "overloaded",
            "unauthenticated",
        }:
            raise ValueError("identity rejection code is not admitted")
        if type(self.missing_roles) is not tuple or any(
            role not in CONTROL_PLANE_ROLES for role in self.missing_roles
        ):
            raise ValueError("identity rejection roles are not admitted")
        if self.missing_roles != tuple(sorted(set(self.missing_roles))):
            raise ValueError("identity rejection roles must be canonical")
        if self.code != "forbidden" and self.missing_roles:
            raise ValueError("only forbidden identity outcomes carry missing roles")


@dataclass(frozen=True, slots=True)
class IdentityUnavailable:
    code: IdentityUnavailableCode

    def __post_init__(self) -> None:
        if self.code not in {
            "identity_provider_unavailable",
            "logout_store_unavailable",
            "machine_verifier_unavailable",
            "session_store_unavailable",
        }:
            raise ValueError("identity unavailable code is not admitted")


@dataclass(frozen=True, slots=True)
class RoleAdmissionGranted:
    principal: KeycloakHumanPrincipal | KeycloakWorkloadPrincipal
    effective_roles: frozenset[ControlPlaneRole]

    def __post_init__(self) -> None:
        if type(self.principal) not in {KeycloakHumanPrincipal, KeycloakWorkloadPrincipal}:
            raise TypeError("role admission principal must be an exact Keycloak principal")
        _roles(self.effective_roles)
        if not self.effective_roles <= self.principal.roles:
            raise ValueError("effective roles exceed principal evidence")


type RoleAdmission = RoleAdmissionGranted | IdentityRejected


def admit_control_plane_roles(
    principal: ControlPlanePrincipal,
    required_roles: frozenset[ControlPlaneRole],
    *,
    at: datetime,
) -> RoleAdmission:
    _roles(required_roles)
    instant = _aware_utc(at, "role admission time")
    if type(principal) is KeycloakHumanPrincipal:
        admitted_principal: KeycloakHumanPrincipal | KeycloakWorkloadPrincipal = principal
    elif type(principal) is KeycloakWorkloadPrincipal:
        admitted_principal = principal
    else:
        return IdentityRejected("forbidden")
    if instant >= admitted_principal.expires_at:
        return IdentityRejected("unauthenticated")
    missing = tuple(sorted(required_roles - admitted_principal.roles))
    if missing:
        return IdentityRejected("forbidden", missing)
    return RoleAdmissionGranted(admitted_principal, required_roles)


def derive_human_actor_id(issuer: str, subject: str) -> str:
    _issuer(issuer)
    _text(subject, "Keycloak subject", maximum_bytes=512)
    return "keycloak-human:v1:" + _coordinate_digest(issuer, subject)


def derive_workload_actor_id(issuer: str, authorized_party: str, subject: str) -> str:
    _issuer(issuer)
    _text(authorized_party, "Keycloak authorized party", maximum_bytes=256)
    _text(subject, "Keycloak subject", maximum_bytes=512)
    return "keycloak-workload:v1:" + _coordinate_digest(issuer, authorized_party, subject)


def is_administrator_actor_id(value: object) -> bool:
    return (
        type(value) is str
        and _ACTOR_ID.fullmatch(value) is not None
        and value.startswith(("keycloak-human:v1:", "keycloak-workload:v1:"))
    )


def is_break_glass_actor_id(value: object) -> bool:
    return (
        type(value) is str
        and _ACTOR_ID.fullmatch(value) is not None
        and value.startswith("break-glass:v1:")
    )


class ControlPlaneActorAuthority:
    def is_administrator(self, actor: object) -> bool:
        return is_administrator_actor_id(actor)

    def is_break_glass(self, actor: object) -> bool:
        return is_break_glass_actor_id(actor)


def is_canonical_oidc_issuer(value: object) -> bool:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 512
        or not value.isascii()
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if hostname is None or hostname != hostname.lower():
        return False
    authority = f"[{hostname}]" if ":" in hostname else hostname
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


def _coordinate_digest(*coordinates: str) -> str:
    return hashlib.sha256(b"\0".join(value.encode("utf-8") for value in coordinates)).hexdigest()


def _aware_utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _time_interval(start: object, end: object, name: str) -> tuple[datetime, datetime]:
    admitted_start = _aware_utc(start, f"{name} issue time")
    admitted_end = _aware_utc(end, f"{name} expiry")
    if admitted_end <= admitted_start:
        raise ValueError(f"{name} expiry must follow issue time")
    return admitted_start, admitted_end


def _issuer(value: object) -> None:
    if not is_canonical_oidc_issuer(value):
        raise ValueError("Keycloak issuer must be a canonical HTTPS URL")


def _roles(value: object) -> None:
    if type(value) is not frozenset or not value <= CONTROL_PLANE_ROLES:
        raise ValueError("control-plane roles must be an exact admitted set")


def _text(value: object, name: str, *, maximum_bytes: int) -> None:
    if (
        type(value) is not str
        or not value
        or "\0" in value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"{name} must be bounded non-empty text")


def _optional_text(value: object, name: str, *, maximum_bytes: int) -> None:
    if value is not None:
        _text(value, name, maximum_bytes=maximum_bytes)


def _digest(value: object, name: str) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256 hexadecimal")


def _opaque_handle(value: object, name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 43
        or any(character not in _URL_SAFE for character in value)
    ):
        raise ValueError(f"{name} must be canonical 32-byte base64url")


def _bytes(value: object, name: str, *, exact: int) -> None:
    if type(value) is not bytes or len(value) != exact:
        raise ValueError(f"{name} must be exactly {exact} bytes")


def _positive_safe_integer(value: object, name: str) -> None:
    if type(value) is not int or not 1 <= value <= MAX_SAFE_JSON_INTEGER:
        raise ValueError(f"{name} must be a positive JSON safe integer")


_URL_SAFE: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
