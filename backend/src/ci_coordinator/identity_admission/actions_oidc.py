from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from ci_coordinator.identity_admission.rejection import RejectedIdentity
from ci_coordinator.kernel import Clock, Err, try_hash_object

VERIFIER_VERSION = "ci-coordinator.identity-admission.actions-oidc-claims.v2"
_WORKFLOW_PATH = re.compile(r"\.github/workflows/[^/\\]+\.ya?ml")
_GITHUB_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_GITHUB_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_GIT_REVISION = re.compile(r"[0-9a-f]{40,64}")

JsonClaims = Mapping[str, object]


@dataclass(frozen=True)
class ExpectedActionsOidcClaims:
    issuer: str
    audience: str
    repository: str
    repository_id: int
    ref: str
    run_id: int
    run_attempt: int
    event_name: str
    expected_execution_sha: str
    allowed_workflow_refs: tuple[str, ...]
    allowed_job_workflow_refs: tuple[str, ...] = ()
    allowed_workflow_paths: tuple[str, ...] = ()
    allowed_job_workflow_paths: tuple[str, ...] = ()
    expected_workflow_sha: str | None = None
    expected_job_workflow_sha: str | None = None

    def __post_init__(self) -> None:
        if self.audience.strip() == "":
            raise ValueError("GitHub Actions OIDC audience is required")
        if _GIT_REVISION.fullmatch(self.expected_execution_sha) is None:
            raise ValueError("expected GitHub Actions execution sha is invalid")
        if not any(
            (
                self.allowed_workflow_refs,
                self.allowed_job_workflow_refs,
                self.allowed_workflow_paths,
                self.allowed_job_workflow_paths,
            )
        ):
            raise ValueError("at least one GitHub Actions OIDC workflow identity is required")


@dataclass(frozen=True)
class TrustedActionsRun:
    issuer: str
    audience: str
    repository: str
    repository_id: int
    ref: str
    run_id: int
    run_attempt: int
    event_name: str
    workflow_ref: str | None
    workflow_sha: str | None
    job_workflow_ref: str | None
    job_workflow_sha: str | None
    check_run_id: str | None
    verified_at: datetime
    execution_sha: str = field(kw_only=True)
    verifier_version: str = VERIFIER_VERSION
    claim_hash: str | None = None


def workflow_path_from_ref(
    *,
    repository: str,
    workflow_ref: str | None,
) -> str | None:
    """Project the top-level workflow path from an exact GitHub OIDC claim."""

    if type(repository) is not str or type(workflow_ref) is not str:
        return None
    prefix = repository + "/"
    if not workflow_ref.startswith(prefix):
        return None
    path, separator, git_ref = workflow_ref[len(prefix) :].rpartition("@refs/")
    if (
        separator != "@refs/"
        or not git_ref
        or _WORKFLOW_PATH.fullmatch(path) is None
        or len(path.encode("utf-8")) > 256
    ):
        return None
    return path


def workflow_path_identity_from_ref(workflow_ref: str | None) -> str | None:
    """Project ``owner/repository/workflow-path`` from an exact Actions claim."""

    if type(workflow_ref) is not str:
        return None
    identity, separator, git_ref = workflow_ref.rpartition("@refs/")
    if separator != "@refs/" or not git_ref or not is_workflow_path_identity(identity):
        return None
    return identity


def is_workflow_path_identity(value: object) -> bool:
    if type(value) is not str or len(value.encode("utf-8")) > 512:
        return False
    parts = value.split("/")
    return (
        len(parts) == 5
        and _GITHUB_OWNER.fullmatch(parts[0]) is not None
        and _GITHUB_REPOSITORY.fullmatch(parts[1]) is not None
        and _WORKFLOW_PATH.fullmatch("/".join(parts[2:])) is not None
    )


def admit_verified_actions_oidc_claims(
    claims: JsonClaims,
    expected: ExpectedActionsOidcClaims,
    clock: Clock,
) -> TrustedActionsRun | RejectedIdentity:
    verified_at = clock.now()
    claim_snapshot = _snapshot_claims(claims)
    if claim_snapshot is None:
        return _reject(
            "oidc_claims_not_json",
            "OIDC claims must be JSON-domain values",
            verified_at,
            None,
        )

    claim_hash = _claim_hash(claim_snapshot)
    if claim_hash is None:
        return _reject(
            "oidc_claims_not_json",
            "OIDC claims must be JSON-domain values",
            verified_at,
            None,
        )

    issuer = _read_claim_string(claim_snapshot, "iss")
    if issuer != expected.issuer:
        return _reject(
            "oidc_issuer_mismatch",
            "OIDC issuer does not match",
            verified_at,
            claim_hash,
        )

    if not _claim_audience_contains(claim_snapshot.get("aud"), expected.audience):
        return _reject(
            "oidc_audience_mismatch",
            "OIDC audience does not match",
            verified_at,
            claim_hash,
        )

    now_seconds = verified_at.timestamp()
    expires_at = _read_claim_number(claim_snapshot, "exp")
    if expires_at is None or expires_at <= now_seconds:
        return _reject(
            "oidc_token_expired",
            "OIDC token is expired",
            verified_at,
            claim_hash,
        )

    not_before = _read_claim_number(claim_snapshot, "nbf")
    if not_before is not None and not_before > now_seconds:
        return _reject(
            "oidc_token_not_yet_valid",
            "OIDC token is not yet valid",
            verified_at,
            claim_hash,
        )

    repository = _read_claim_string(claim_snapshot, "repository")
    repository_id = _read_claim_integer_string(claim_snapshot, "repository_id")
    ref = _read_claim_string(claim_snapshot, "ref")
    run_id = _read_claim_integer_string(claim_snapshot, "run_id")
    run_attempt = _read_claim_integer_string(claim_snapshot, "run_attempt")
    event_name = _read_claim_string(claim_snapshot, "event_name")
    execution_sha = _read_claim_string(claim_snapshot, "sha")
    workflow_ref = _read_claim_string(claim_snapshot, "workflow_ref")
    workflow_sha = _read_claim_string(claim_snapshot, "workflow_sha")
    job_workflow_ref = _read_claim_string(claim_snapshot, "job_workflow_ref")
    job_workflow_sha = _read_claim_string(claim_snapshot, "job_workflow_sha")
    check_run_id = _read_claim_string(claim_snapshot, "check_run_id")

    if repository != expected.repository:
        return _reject(
            "oidc_repository_mismatch",
            "OIDC repository does not match request",
            verified_at,
            claim_hash,
        )

    if repository_id != expected.repository_id:
        return _reject(
            "oidc_repository_id_mismatch",
            "OIDC repository id does not match request",
            verified_at,
            claim_hash,
        )

    if ref != expected.ref:
        return _reject(
            "oidc_ref_mismatch",
            "OIDC ref does not match request",
            verified_at,
            claim_hash,
        )

    if run_id != expected.run_id or run_attempt != expected.run_attempt:
        return _reject(
            "oidc_run_identity_mismatch",
            "OIDC run identity does not match request",
            verified_at,
            claim_hash,
        )

    if event_name != expected.event_name:
        return _reject(
            "oidc_event_name_mismatch",
            "OIDC event name does not match request",
            verified_at,
            claim_hash,
        )

    if execution_sha is None or execution_sha != expected.expected_execution_sha:
        return _reject(
            "oidc_execution_sha_mismatch",
            "OIDC execution sha does not match request",
            verified_at,
            claim_hash,
        )

    workflow_path_identity = workflow_path_identity_from_ref(workflow_ref)
    job_workflow_path_identity = workflow_path_identity_from_ref(job_workflow_ref)
    exact_workflow_allowed = (
        workflow_ref is not None and workflow_ref in expected.allowed_workflow_refs
    )
    exact_job_workflow_allowed = (
        job_workflow_ref is not None and job_workflow_ref in expected.allowed_job_workflow_refs
    )
    workflow_path_matches = (
        workflow_path_identity is not None
        and workflow_path_identity in expected.allowed_workflow_paths
    )
    job_workflow_path_matches = (
        job_workflow_path_identity is not None
        and job_workflow_path_identity in expected.allowed_job_workflow_paths
    )
    caller_identity_configured = bool(
        expected.allowed_workflow_refs or expected.allowed_workflow_paths
    )
    requester_identity_configured = bool(
        expected.allowed_job_workflow_refs or expected.allowed_job_workflow_paths
    )
    caller_allowed = exact_workflow_allowed or (
        workflow_path_matches and _GIT_REVISION.fullmatch(workflow_sha or "") is not None
    )
    requester_allowed = exact_job_workflow_allowed or (
        job_workflow_path_matches and _GIT_REVISION.fullmatch(job_workflow_sha or "") is not None
    )
    if (caller_identity_configured and not caller_allowed) or (
        requester_identity_configured and not requester_allowed
    ):
        if (
            caller_identity_configured
            and workflow_path_matches
            and _GIT_REVISION.fullmatch(workflow_sha or "") is None
        ) or (
            requester_identity_configured
            and job_workflow_path_matches
            and _GIT_REVISION.fullmatch(job_workflow_sha or "") is None
        ):
            return _reject(
                "oidc_workflow_sha_required",
                "path-admitted OIDC workflow identity requires an immutable workflow sha",
                verified_at,
                claim_hash,
            )
        return _reject(
            "oidc_workflow_not_allowed",
            "OIDC workflow identity is not allowed",
            verified_at,
            claim_hash,
        )

    if (
        expected.expected_workflow_sha is not None
        and workflow_sha != expected.expected_workflow_sha
    ):
        return _reject(
            "oidc_workflow_sha_mismatch",
            "OIDC workflow sha does not match request",
            verified_at,
            claim_hash,
        )

    if (
        expected.expected_job_workflow_sha is not None
        and job_workflow_sha != expected.expected_job_workflow_sha
    ):
        return _reject(
            "oidc_job_workflow_sha_mismatch",
            "OIDC job workflow sha does not match request",
            verified_at,
            claim_hash,
        )

    return TrustedActionsRun(
        issuer=issuer,
        audience=expected.audience,
        repository=repository,
        repository_id=repository_id,
        ref=ref,
        run_id=run_id,
        run_attempt=run_attempt,
        event_name=event_name,
        execution_sha=execution_sha,
        workflow_ref=workflow_ref,
        workflow_sha=workflow_sha,
        job_workflow_ref=job_workflow_ref,
        job_workflow_sha=job_workflow_sha,
        check_run_id=check_run_id,
        verified_at=verified_at,
        claim_hash=claim_hash,
    )


def _snapshot_claims(claims: JsonClaims) -> dict[str, object] | None:
    snapshot: dict[str, object] = {}
    for key, value in claims.items():
        if type(key) is not str:
            return None
        copied = _snapshot_value(value)
        if copied is _INVALID_JSON_VALUE:
            return None
        snapshot[key] = copied
    return snapshot


_INVALID_JSON_VALUE = object()


def _snapshot_value(value: object) -> object:
    if value is None or type(value) in {bool, str, int, float}:
        return value
    if type(value) is list:
        copied_items: list[object] = []
        for item in value:
            copied = _snapshot_value(item)
            if copied is _INVALID_JSON_VALUE:
                return _INVALID_JSON_VALUE
            copied_items.append(copied)
        return copied_items
    if type(value) is dict:
        copied_object: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                return _INVALID_JSON_VALUE
            copied = _snapshot_value(item)
            if copied is _INVALID_JSON_VALUE:
                return _INVALID_JSON_VALUE
            copied_object[key] = copied
        return copied_object
    return _INVALID_JSON_VALUE


def _claim_hash(claims: Mapping[str, object]) -> str | None:
    result = try_hash_object(dict(claims))
    if isinstance(result, Err):
        return None
    return result.value


def _reject(
    reason_code: str,
    message: str,
    verified_at: datetime,
    claim_hash: str | None,
) -> RejectedIdentity:
    return RejectedIdentity(
        reason_code=reason_code,
        message=message,
        verified_at=verified_at,
        verifier_version=VERIFIER_VERSION,
        claim_hash=claim_hash,
    )


def _claim_audience_contains(value: object, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return any(entry == expected for entry in value)
    return False


def _read_claim_string(claims: JsonClaims, key: str) -> str | None:
    value = claims.get(key)
    return value if isinstance(value, str) and value != "" else None


def _read_claim_number(claims: JsonClaims, key: str) -> float | None:
    value = claims.get(key)
    if type(value) is int or type(value) is float:
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def _read_claim_integer_string(claims: JsonClaims, key: str) -> int | None:
    value = claims.get(key)
    if (
        not isinstance(value, str)
        or not value
        or any(character < "0" or character > "9" for character in value)
    ):
        return None
    parsed = int(value, 10)
    return parsed if abs(parsed) <= 9_007_199_254_740_991 else None
