"""Request-bound Actions identity with an operation-specific audience."""

from __future__ import annotations

from typing import Final

from ci_coordinator.api.http.dependencies import (
    ActionsAuthenticationResult,
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    InvalidCredential,
)
from ci_coordinator.identity_admission import (
    ExpectedActionsOidcClaims,
    JwksProvider,
    JwksUnavailable,
    RejectedActionsOidcHeader,
    RejectedIdentity,
    admit_actions_oidc_header,
    is_workflow_path_identity,
    verify_actions_oidc,
)
from ci_coordinator.kernel import Clock
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.plan_issuance import PlanRequest

GITHUB_ACTIONS_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
_INVALID_CREDENTIAL_REASONS: Final = frozenset(
    {
        "oidc_bearer_token_missing",
        "oidc_claims_not_json",
        "oidc_header_not_admitted",
        "oidc_signature_invalid",
        "oidc_token_expired",
        "oidc_token_malformed",
        "oidc_token_not_yet_valid",
    }
)
_FORBIDDEN_IDENTITY_REASONS: Final = frozenset(
    {
        "oidc_audience_mismatch",
        "oidc_event_name_mismatch",
        "oidc_execution_sha_mismatch",
        "oidc_issuer_mismatch",
        "oidc_job_workflow_sha_mismatch",
        "oidc_ref_mismatch",
        "oidc_repository_id_mismatch",
        "oidc_repository_mismatch",
        "oidc_run_identity_mismatch",
        "oidc_workflow_not_allowed",
        "oidc_workflow_sha_required",
        "oidc_workflow_sha_mismatch",
    }
)
_AUTHENTICATION_DEPENDENCY_REASONS: Final = frozenset(
    {
        "oidc_signing_key_invalid",
        "oidc_signing_key_unavailable",
    }
)


class RequestBoundActionsOidcAuthenticator:
    """Verify exact run identity; plan requests project into the same admission."""

    def __init__(
        self,
        *,
        jwks_provider: JwksProvider,
        clock: Clock,
        audience: str,
        allowed_workflow_refs: tuple[str, ...],
        allowed_job_workflow_refs: tuple[str, ...] = (),
        allowed_workflow_paths: tuple[str, ...] = (),
        allowed_job_workflow_paths: tuple[str, ...] = (),
        runtime_metrics: RuntimeMetrics | None = None,
    ) -> None:
        if (
            type(audience) is not str
            or not audience
            or audience != audience.strip()
            or len(audience) > 512
        ):
            raise ValueError("OIDC audience must be bounded non-empty text")
        if (
            not _admitted_workflow_refs(allowed_workflow_refs)
            or not _admitted_workflow_refs(allowed_job_workflow_refs)
            or not _admitted_workflow_paths(allowed_workflow_paths)
            or not _admitted_workflow_paths(allowed_job_workflow_paths)
            or not any(
                (
                    allowed_workflow_refs,
                    allowed_job_workflow_refs,
                    allowed_workflow_paths,
                    allowed_job_workflow_paths,
                )
            )
        ):
            raise ValueError(
                "OIDC workflow references must be ordered bounded tuples with at least one value"
            )
        self._jwks_provider = jwks_provider
        self._clock = clock
        self._audience = audience
        self._allowed_workflow_refs = allowed_workflow_refs
        self._allowed_job_workflow_refs = allowed_job_workflow_refs
        self._allowed_workflow_paths = allowed_workflow_paths
        self._allowed_job_workflow_paths = allowed_job_workflow_paths
        self._runtime_metrics = runtime_metrics

    async def authenticate(
        self,
        authorization: str | None,
        plan_request: PlanRequest,
    ) -> ActionsAuthenticationResult:
        if type(plan_request) is not PlanRequest:
            raise TypeError("plan authentication requires an exact parsed plan request")
        return await self.authenticate_run(
            authorization,
            repository=f"{plan_request.owner}/{plan_request.repository}",
            repository_id=plan_request.repository_id,
            ref=plan_request.ref,
            run_id=plan_request.workflow_run_id,
            run_attempt=plan_request.run_attempt,
            event_name=plan_request.event_name,
            execution_sha=plan_request.execution_sha,
        )

    async def authenticate_run(
        self,
        authorization: str | None,
        *,
        repository: str,
        repository_id: int,
        ref: str,
        run_id: int,
        run_attempt: int,
        event_name: str,
        execution_sha: str,
    ) -> ActionsAuthenticationResult:
        token = _bearer_token(authorization)
        if token is None:
            return self._invalid_credential("oidc_bearer_token_missing")
        header = admit_actions_oidc_header(token)
        if isinstance(header, RejectedActionsOidcHeader):
            return self._invalid_credential(header.reason_code)
        key_set = await self._jwks_provider.get_key_set(header.key_id)
        if isinstance(key_set, JwksUnavailable):
            return self._dependency_unavailable(f"oidc_jwks_{key_set.kind}")
        result = verify_actions_oidc(
            token,
            ExpectedActionsOidcClaims(
                issuer=GITHUB_ACTIONS_OIDC_ISSUER,
                audience=self._audience,
                repository=repository,
                repository_id=repository_id,
                ref=ref,
                run_id=run_id,
                run_attempt=run_attempt,
                event_name=event_name,
                expected_execution_sha=execution_sha,
                allowed_workflow_refs=self._allowed_workflow_refs,
                allowed_job_workflow_refs=self._allowed_job_workflow_refs,
                allowed_workflow_paths=self._allowed_workflow_paths,
                allowed_job_workflow_paths=self._allowed_job_workflow_paths,
            ),
            key_set,
            self._clock,
        )
        if isinstance(result, RejectedIdentity):
            return self._classify_rejection(result)
        return result

    def _classify_rejection(
        self,
        rejected: RejectedIdentity,
    ) -> InvalidCredential | ForbiddenIdentity | AuthenticationDependencyUnavailable:
        self._observe_rejection(rejected.reason_code)
        if rejected.reason_code in _INVALID_CREDENTIAL_REASONS:
            return InvalidCredential()
        if rejected.reason_code in _FORBIDDEN_IDENTITY_REASONS:
            return ForbiddenIdentity()
        if rejected.reason_code in _AUTHENTICATION_DEPENDENCY_REASONS:
            return AuthenticationDependencyUnavailable()
        raise RuntimeError("unsupported OIDC rejection reason")

    def _invalid_credential(self, reason_code: str) -> InvalidCredential:
        self._observe_rejection(reason_code)
        return InvalidCredential()

    def _dependency_unavailable(
        self,
        reason_code: str,
    ) -> AuthenticationDependencyUnavailable:
        self._observe_rejection(reason_code)
        return AuthenticationDependencyUnavailable()

    def _observe_rejection(self, reason_code: str) -> None:
        if self._runtime_metrics is not None:
            self._runtime_metrics.invalid_oidc(reason_code)


def _bearer_token(authorization: str | None) -> str | None:
    if type(authorization) is not str:
        return None
    scheme, separator, token = authorization.partition(" ")
    if (
        scheme.casefold() != "bearer"
        or not separator
        or not token
        or any(character.isspace() for character in token)
    ):
        return None
    return token


def _admitted_workflow_refs(references: object) -> bool:
    if type(references) is not tuple or len(references) > 64:
        return False
    if any(
        type(reference) is not str
        or not reference
        or reference != reference.strip()
        or len(reference) > 512
        for reference in references
    ):
        return False
    return tuple(sorted(set(references))) == references


def _admitted_workflow_paths(paths: object) -> bool:
    if type(paths) is not tuple or len(paths) > 64:
        return False
    if any(
        type(path) is not str
        or path != path.strip()
        or len(path.encode("utf-8")) > 512
        or not is_workflow_path_identity(path)
        for path in paths
    ):
        return False
    return tuple(sorted(set(paths))) == paths
