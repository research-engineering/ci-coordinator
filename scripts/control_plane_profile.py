from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scripts.control_plane_identity_profile import validate_identity_profile
from scripts.control_plane_operation_profile import validate_operation_profile
from scripts.control_plane_profile_values import exact_json_equal
from scripts.control_plane_provider_profile import validate_provider_profile
from scripts.proofkit_common import as_array, as_object
from scripts.proofkit_common import exact_fields as _exact_keys
from scripts.proofkit_common import nonempty_string as _required_string
from scripts.proofkit_common import object_rows as _object_rows
from scripts.repository_paths import read_repository_regular_file

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE_PATH = Path("docs/specs/ci-coordinator-control-plane/control-plane-profile.v1.json")
MAX_PROFILE_BYTES = 128 * 1024

PlaneProjection = tuple[str, tuple[str, ...], tuple[str, ...], str]


_EXPECTED_PLANES: tuple[PlaneProjection, ...] = (
    (
        "emergency-safety",
        ("deployment-break-glass-bearer",),
        ("emergency-safety-command",),
        "Increase validation or latch omission off.",
    ),
    (
        "human-administration",
        ("keycloak-id-token", "opaque-server-session"),
        ("administrator-browser", "administrator-command", "administrator-read"),
        "Identify one human administrator and project exact client roles.",
    ),
    (
        "identity-provider-capability",
        ("keycloak-browser-client-secret", "keycloak-workload-client-credential"),
        ("identity-provider-operation",),
        ("Authenticate one exact coordinator or workload client to the Keycloak token endpoint."),
    ),
    (
        "machine-administration",
        ("keycloak-access-token",),
        ("administrator-command", "administrator-read"),
        "Identify one workload and project exact client roles.",
    ),
    (
        "provider-capability",
        (
            "github-app-client-secret",
            "github-app-jwt",
            "github-app-private-key",
            "github-installation-token",
        ),
        ("provider-operation",),
        (
            "Authenticate the exact GitHub App for one admitted provider operation; "
            "repository effects remain installation-scoped."
        ),
    ),
    (
        "provider-event",
        ("github-webhook-hmac",),
        ("provider-webhook",),
        "Admit one delivery as GitHub-originated event evidence.",
    ),
    (
        "repository-attestation",
        ("github-review-receipt", "github-user-oauth"),
        ("repository-review",),
        "Prove that a current repository manager accepted exact proposal bytes.",
    ),
    (
        "target-workflow",
        ("github-actions-oidc",),
        ("plan-request",),
        "Request one revision-bound plan.",
    ),
)
_PLANE_IDS = tuple(row[0] for row in _EXPECTED_PLANES)
_RUNTIME_ROLES = ("activate", "audit", "configure", "override", "read")
_BREAK_GLASS_ACTIONS = ("disable-omission", "force-full-ci")
_EXPECTED_BREAK_GLASS: dict[str, object] = {
    "productionActions": ["disable-omission", "force-full-ci"],
    "forbiddenActions": [
        "activate-configuration",
        "audit-read",
        "enable-omission",
        "provider-write",
        "register-configuration",
        "repository-attestation",
        "rollback-configuration",
    ],
    "rotationOwner": "deployment",
    "partiallyRotatedReplicaSet": "rejected",
}
_ROUTES = {
    "administratorApiPrefix": "/api/v1",
    "backChannelLogout": "/api/v1/auth/keycloak/backchannel-logout",
    "browserCallback": "/api/v1/auth/keycloak/callback",
    "browserLogout": "/api/v1/auth/keycloak/logout",
    "browserSession": "/api/v1/auth/session",
    "browserStart": "/api/v1/auth/keycloak/start",
    "emergencySafetyCommand": "/api/v1/overrides/full-ci",
    "planRequest": "/api/v1/dynamic-ci/plan",
    "providerWebhook": "/webhooks/github",
    "repositoryAttestationCallback": ("/api/v1/repository-attestations/github/callback"),
    "repositoryAttestationStart": "/api/v1/repository-attestations/github/start",
}
_TOP_LEVEL_FIELDS = {
    "attribution",
    "authorityState",
    "breakGlass",
    "credentialPlanes",
    "githubApp",
    "keycloak",
    "liveDevelopmentObservations",
    "nonClaims",
    "operations",
    "ownerId",
    "profileId",
    "repositoryAttestation",
    "roles",
    "routes",
    "schemaVersion",
    "supportBoundary",
}
_EXPECTED_NON_CLAIMS = (
    "Complete administrator operation-to-route mapping remains deferred to the "
    "API-first configuration contract.",
    "Deferred GitHub permission profiles grant no current provider write authority.",
    "The unconfigured observation status does not prove deployment installation, "
    "secret custody, provider availability, or production admission.",
    "This profile does not prove immediate identity revocation, repository-owner "
    "consent, deployment, production readiness, or CI omission authority.",
    "This profile does not prove that Keycloak clients, roles, workload "
    "identities, callback URLs, logout routes, or secrets are configured.",
)


@dataclass(frozen=True, slots=True)
class ControlPlaneProfile:
    credential_kinds: tuple[str, ...]
    credential_plane_ids: tuple[str, ...]
    operation_ids: tuple[str, ...]
    profile_digest: str


def load_control_plane_profile(
    repo_root: Path = REPO_ROOT,
    profile_path: Path = DEFAULT_PROFILE_PATH,
) -> ControlPlaneProfile:
    payload = read_repository_regular_file(
        repo_root,
        profile_path,
        "control-plane profile",
        maximum_bytes=MAX_PROFILE_BYTES,
    )
    document = _parse_document(payload)
    _exact_keys(document, _TOP_LEVEL_FIELDS, "control-plane profile")
    _literal(document, "schemaVersion", 1)
    _literal(
        document,
        "profileId",
        "ci-coordinator.organization-control-plane/v1",
    )
    _literal(document, "ownerId", "ci-coordinator.control-plane")
    _literal(document, "authorityState", "normative-contract")

    plane_ids, credential_kinds = _validate_planes(document.get("credentialPlanes"))
    _validate_roles(document.get("roles"))
    operation_ids = validate_operation_profile(
        document.get("operations"),
        admitted_plane_ids=set(plane_ids),
        admitted_roles=set(_RUNTIME_ROLES),
    )
    routes = as_object(document.get("routes"), "control-plane routes")
    if not exact_json_equal(routes, _ROUTES):
        raise ValueError("control-plane routes differ from the admitted contract")
    validate_identity_profile(document.get("keycloak"), document.get("attribution"))
    validate_provider_profile(
        document.get("githubApp"),
        document.get("repositoryAttestation"),
        document.get("liveDevelopmentObservations"),
        admitted_credential_kinds=set(credential_kinds),
        admitted_routes=routes,
    )
    _validate_break_glass(document.get("breakGlass"))
    _validate_support_boundary(document.get("supportBoundary"))
    if (
        _ordered_strings(document.get("nonClaims"), "control-plane non-claims")
        != _EXPECTED_NON_CLAIMS
    ):
        raise ValueError("control-plane non-claims differ from the admitted contract")
    return ControlPlaneProfile(
        credential_kinds=credential_kinds,
        credential_plane_ids=plane_ids,
        operation_ids=operation_ids,
        profile_digest=hashlib.sha256(payload).hexdigest(),
    )


def _validate_planes(value: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    rows = _object_rows(value, "credential planes")
    plane_ids: list[str] = []
    credential_kinds: list[str] = []
    projections: list[PlaneProjection] = []
    for row in rows:
        _exact_keys(
            row,
            {"credentialKinds", "planeId", "routeFamilies", "soleAuthority"},
            "credential plane",
        )
        plane_id = _required_string(row.get("planeId"), "credential plane id")
        kinds = _ordered_strings(row.get("credentialKinds"), "credential kinds")
        route_families = _ordered_strings(row.get("routeFamilies"), "credential route families")
        sole_authority = _required_string(row.get("soleAuthority"), "credential sole authority")
        plane_ids.append(plane_id)
        credential_kinds.extend(kinds)
        projections.append((plane_id, kinds, route_families, sole_authority))
    if tuple(plane_ids) != _PLANE_IDS:
        raise ValueError("credential planes differ from the admitted finite set")
    if len(credential_kinds) != len(set(credential_kinds)):
        raise ValueError("credential kinds overlap across authority planes")
    if tuple(projections) != _EXPECTED_PLANES:
        raise ValueError("credential plane projection differs from the contract")
    return tuple(plane_ids), tuple(sorted(credential_kinds))


def _validate_roles(value: object) -> None:
    roles = as_object(value, "control-plane roles")
    _exact_keys(roles, {"assignmentComposite", "runtimeRoles"}, "control-plane roles")
    _literal(roles, "assignmentComposite", "administrator")
    if _ordered_strings(roles.get("runtimeRoles"), "runtime roles") != _RUNTIME_ROLES:
        raise ValueError("runtime roles differ from the admitted finite set")


def _validate_break_glass(value: object) -> None:
    boundary = as_object(value, "break-glass profile")
    _exact_keys(
        boundary,
        {
            "forbiddenActions",
            "partiallyRotatedReplicaSet",
            "productionActions",
            "rotationOwner",
        },
        "break-glass profile",
    )
    if _ordered_strings(boundary.get("productionActions"), "break-glass actions") != (
        _BREAK_GLASS_ACTIONS
    ):
        raise ValueError("break-glass actions differ from the safety-only contract")
    forbidden = _ordered_strings(boundary.get("forbiddenActions"), "forbidden break-glass actions")
    if set(forbidden) & set(_BREAK_GLASS_ACTIONS) or "enable-omission" not in forbidden:
        raise ValueError("break-glass forbidden actions are inconsistent")
    _literal(boundary, "rotationOwner", "deployment")
    _literal(boundary, "partiallyRotatedReplicaSet", "rejected")
    if not exact_json_equal(boundary, _EXPECTED_BREAK_GLASS):
        raise ValueError("break-glass profile differs from the admitted contract")


def _validate_support_boundary(value: object) -> None:
    expected = {
        "releaseState": "pre-first-release",
        "databaseOrigin": "fresh-bootstrap",
        "developmentDataDisposition": "reset",
        "initialDeploymentMode": "non-enforcing",
    }
    boundary = as_object(value, "control-plane support boundary")
    _exact_keys(boundary, set(expected), "control-plane support boundary")
    if not exact_json_equal(boundary, expected):
        raise ValueError("control-plane support boundary differs from the contract")


def _parse_document(payload: bytes) -> dict[str, object]:
    try:
        source = payload.decode("utf-8", errors="strict")
        value: object = json.loads(
            source,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("control-plane profile must be strict UTF-8 JSON") from error
    return as_object(value, "control-plane profile")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"control-plane profile has duplicate key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"control-plane profile has non-finite constant: {value}")


def _ordered_strings(
    value: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    values = tuple(_required_string(item, label) for item in as_array(value, label))
    if not values and not allow_empty:
        raise ValueError(f"{label} must be non-empty")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be unique and canonically ordered")
    return values


def _literal(document: Mapping[str, object], field: str, expected: object) -> None:
    if not exact_json_equal(document.get(field), expected):
        raise ValueError(f"control-plane {field} differs from the admitted contract")


def profile_report(profile: ControlPlaneProfile) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "reportId": "ci-coordinator.control-plane-profile",
        "state": "passed",
        "summary": {
            "credentialKindCount": len(profile.credential_kinds),
            "credentialPlaneCount": len(profile.credential_plane_ids),
            "operationCount": len(profile.operation_ids),
            "profileSha256": profile.profile_digest,
        },
        "nonClaims": [
            "Profile admission does not configure external providers or prove runtime conformance."
        ],
    }


if __name__ == "__main__":
    print(
        json.dumps(
            profile_report(load_control_plane_profile()),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
