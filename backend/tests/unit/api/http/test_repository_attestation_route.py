from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest
from control_plane_http_support import (
    StaticControlPlaneAuthenticator,
    StaticMutationAdmission,
    StaticRoleAdmission,
    human_principal,
)
from fastapi.testclient import TestClient
from repository_activation_support import review_record

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    HttpRouteDependencies,
    InvalidCredential,
    RepositoryAttestationRouteDependencies,
)
from ci_coordinator.api.http.routers.repository_attestations import (
    REPOSITORY_ATTESTATION_CALLBACK_PATH,
    REPOSITORY_ATTESTATION_START_PATH,
)
from ci_coordinator.app.proposal_review import ProposalReviewOutcome
from ci_coordinator.app.repository_attestation import (
    RepositoryAttestationCallbackOutcome,
    RepositoryAttestationStartOutcome,
    RepositoryAttestationUseCase,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import KeycloakHumanPrincipal
from ci_coordinator.proposal_review import ProposalReviewCommand

_PUBLIC_ORIGIN = "https://ci.example.test"
_TRANSACTION_COOKIE = "__Secure-ci_coordinator_review"
_PRINCIPAL = human_principal()
_BODY = {
    "installationId": 1,
    "repositoryId": 2,
    "operationId": "review-1",
    "expectedManifestId": "proposal:" + "a" * 32,
    "expectedActive": None,
}


@dataclass
class _AttestationService:
    start_result: RepositoryAttestationStartOutcome = field(
        default_factory=lambda: RepositoryAttestationStartOutcome(
            "ready",
            authorization_url="https://github.com/login/oauth/authorize?state=opaque",
            transaction_cookie="sealed-review",
        )
    )
    callback_result: RepositoryAttestationCallbackOutcome = field(
        default_factory=lambda: RepositoryAttestationCallbackOutcome(
            "completed",
            review=ProposalReviewOutcome(
                "accepted",
                record=review_record(),
                epoch_created=True,
            ),
        )
    )
    callback_failure: Exception | None = None
    calls: list[tuple[object, ...]] = field(default_factory=list)

    async def start(
        self,
        *,
        principal: KeycloakHumanPrincipal,
        command: ProposalReviewCommand,
    ) -> RepositoryAttestationStartOutcome:
        self.calls.append(("start", principal, command))
        return self.start_result

    async def complete(
        self,
        *,
        principal: KeycloakHumanPrincipal,
        transaction_cookie: str | None,
        state: str | None,
        code: str,
    ) -> RepositoryAttestationCallbackOutcome:
        self.calls.append(("complete", principal, transaction_cookie, state, code))
        if self.callback_failure is not None:
            raise self.callback_failure
        return self.callback_result


def test_start_binds_exact_actor_scope_proposal_and_active_pointer() -> None:
    service = _AttestationService()
    client = _client(service)
    body = {
        **_BODY,
        "expectedActive": {"epochId": "b" * 64, "revision": 7},
    }

    response = client.post(
        REPOSITORY_ATTESTATION_START_PATH,
        json=body,
        headers=_mutation_headers(),
    )

    assert (response.status_code, response.json()) == (
        200,
        {
            "ok": True,
            "authorizationUrl": "https://github.com/login/oauth/authorize?state=opaque",
        },
    )
    _, raw_principal, raw_command = service.calls[0]
    principal = cast(KeycloakHumanPrincipal, raw_principal)
    command = cast(ProposalReviewCommand, raw_command)
    assert principal is _PRINCIPAL
    assert command == ProposalReviewCommand(
        scope=RepositoryScope(1, 2),
        operation_id="review-1",
        expected_manifest_id="proposal:" + "a" * 32,
        expected_active=command.expected_active,
        actor=_PRINCIPAL.actor_id,
    )
    assert command.expected_active is not None
    assert (command.expected_active.epoch_id, command.expected_active.revision) == ("b" * 64, 7)
    assert response.headers["cache-control"] == "no-store"
    assert all(
        marker in response.headers["set-cookie"]
        for marker in (
            f"{_TRANSACTION_COOKIE}=sealed-review",
            "HttpOnly",
            "Max-Age=300",
            f"Path={REPOSITORY_ATTESTATION_CALLBACK_PATH}",
            "SameSite=lax",
            "Secure",
        )
    )


@pytest.mark.parametrize(
    ("state", "status_code"),
    [
        ("already_reviewed", 409),
        ("baseline_conflict", 409),
        ("blocked", 409),
        ("forbidden", 403),
        ("operation_conflict", 409),
        ("overloaded", 503),
        ("stale", 409),
        ("unavailable", 503),
    ],
)
def test_start_preserves_complete_outcome_algebra(state: str, status_code: int) -> None:
    service = _AttestationService(
        start_result=RepositoryAttestationStartOutcome(state)  # type: ignore[arg-type]
    )

    response = _client(service).post(
        REPOSITORY_ATTESTATION_START_PATH,
        json=_BODY,
        headers=_mutation_headers(),
    )

    assert (response.status_code, response.json()) == (
        status_code,
        {"ok": False, "error": state},
    )
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("authentication", "status_code", "error"),
    [
        (InvalidCredential(), 401, "unauthenticated"),
        (AuthenticationDependencyUnavailable(), 503, "unavailable"),
    ],
    ids=("invalid", "unavailable"),
)
def test_start_rejects_authentication_failure_before_effect(
    authentication: InvalidCredential | AuthenticationDependencyUnavailable,
    status_code: int,
    error: str,
) -> None:
    service = _AttestationService()
    response = _client(service, authentication=authentication).post(
        REPOSITORY_ATTESTATION_START_PATH,
        json=_BODY,
        headers=_mutation_headers(),
    )

    assert (response.status_code, response.json()) == (
        status_code,
        {"ok": False, "error": error},
    )
    assert service.calls == []


def test_start_rejects_integrity_and_noncanonical_body_before_effect() -> None:
    service = _AttestationService()
    client = _client(service, mutation_admitted=False)

    integrity = client.post(
        REPOSITORY_ATTESTATION_START_PATH,
        json=_BODY,
        headers=_mutation_headers(),
    )
    unknown = client.post(
        REPOSITORY_ATTESTATION_START_PATH,
        json={**_BODY, "actor": "spoofed"},
        headers=_mutation_headers(),
    )
    oversized_scalar = client.post(
        REPOSITORY_ATTESTATION_START_PATH,
        json={**_BODY, "operationId": "e" * 257},
        headers=_mutation_headers(),
    )

    assert (integrity.status_code, integrity.json()) == (
        403,
        {"ok": False, "error": "forbidden"},
    )
    assert unknown.status_code == 422
    assert oversized_scalar.status_code == 422
    assert service.calls == []


def test_callback_projects_retained_review_identity_and_clears_transaction() -> None:
    service = _AttestationService()
    client = _client(service)
    client.cookies.set(_TRANSACTION_COOKIE, "sealed-review")

    response = client.get(
        REPOSITORY_ATTESTATION_CALLBACK_PATH,
        params={"code": "provider-code", "state": "s" * 43},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    query = parse_qs(location.query)
    retained = service.callback_result.review
    assert retained is not None and retained.record is not None
    assert location.path == "/workbench"
    assert query == {
        "installationId": ["1"],
        "repositoryId": ["2"],
        "limit": ["10"],
        "repositoryAttestation": ["reviewed"],
        "proposalManifestId": [retained.record.command.expected_manifest_id],
        "reviewOperationId": [retained.record.command.operation_id],
    }
    assert service.calls[-1] == (
        "complete",
        _PRINCIPAL,
        "sealed-review",
        "s" * 43,
        "provider-code",
    )
    assert response.headers["cache-control"] == "no-store"
    assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.parametrize(
    ("callback", "status_code", "error"),
    [
        (RepositoryAttestationCallbackOutcome("invalid"), 400, "invalid_callback"),
        (RepositoryAttestationCallbackOutcome("replayed"), 409, "replayed"),
        (RepositoryAttestationCallbackOutcome("unavailable"), 503, "unavailable"),
        (
            RepositoryAttestationCallbackOutcome(
                "completed",
                review=ProposalReviewOutcome("forbidden"),
            ),
            403,
            "forbidden",
        ),
        (
            RepositoryAttestationCallbackOutcome(
                "completed",
                review=ProposalReviewOutcome("diff_limit"),
            ),
            422,
            "diff_limit",
        ),
    ],
    ids=("invalid", "replayed", "unavailable", "review-forbidden", "diff-limit"),
)
def test_callback_preserves_failure_algebra_and_always_clears_transaction(
    callback: RepositoryAttestationCallbackOutcome,
    status_code: int,
    error: str,
) -> None:
    service = _AttestationService(callback_result=callback)
    client = _client(service)
    client.cookies.set(_TRANSACTION_COOKIE, "sealed-review")

    response = client.get(
        REPOSITORY_ATTESTATION_CALLBACK_PATH,
        params={"code": "provider-code", "state": "s" * 43},
        follow_redirects=False,
    )

    assert (response.status_code, response.json()) == (
        status_code,
        {"ok": False, "error": error},
    )
    assert response.headers["cache-control"] == "no-store"
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_callback_clears_transaction_cookie_on_redacted_internal_error() -> None:
    service = _AttestationService(callback_failure=RuntimeError("unexpected callback defect"))
    client = _client(service)
    client.cookies.set(_TRANSACTION_COOKIE, "sealed-review")

    response = client.get(
        REPOSITORY_ATTESTATION_CALLBACK_PATH,
        params={"code": "provider-code", "state": "s" * 43},
        follow_redirects=False,
    )

    assert (response.status_code, response.json()) == (500, {"code": "internal_error"})
    assert response.headers["cache-control"] == "no-store"
    assert f"{_TRANSACTION_COOKIE}=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_callback_clears_transaction_cookie_when_correlation_initialization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _AttestationService()

    def fail_to_create_correlation_id(_: int) -> str:
        raise OSError("entropy unavailable")

    monkeypatch.setattr(
        "ci_coordinator.api.http.correlation.secrets.token_hex",
        fail_to_create_correlation_id,
    )
    client = _client(service)
    client.cookies.set(_TRANSACTION_COOKIE, "sealed-review")

    response = client.get(
        REPOSITORY_ATTESTATION_CALLBACK_PATH,
        params={"code": "provider-code", "state": "s" * 43},
        follow_redirects=False,
    )

    assert (response.status_code, response.json()) == (500, {"code": "internal_error"})
    assert "x-correlation-id" not in response.headers
    assert f"{_TRANSACTION_COOKIE}=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert service.calls == []


def test_callback_rejects_duplicate_query_or_missing_transaction_before_effect() -> None:
    service = _AttestationService()
    client = _client(service)

    response = client.get(
        REPOSITORY_ATTESTATION_CALLBACK_PATH + "?code=one&code=two&state=" + "s" * 43,
        follow_redirects=False,
    )

    assert (response.status_code, response.json()) == (
        400,
        {"ok": False, "error": "invalid_callback"},
    )
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert service.calls == []


def _client(
    service: _AttestationService,
    *,
    authentication: KeycloakHumanPrincipal
    | InvalidCredential
    | AuthenticationDependencyUnavailable = (_PRINCIPAL),
    mutation_admitted: bool = True,
) -> TestClient:
    dependencies = RepositoryAttestationRouteDependencies(
        authenticator=StaticControlPlaneAuthenticator(authentication),
        role_admission=StaticRoleAdmission(),
        mutation_admission=StaticMutationAdmission(mutation_admitted),
        service=cast(RepositoryAttestationUseCase, service),
        transaction_cookie_name=_TRANSACTION_COOKIE,
        secure_cookies=True,
    )
    return TestClient(
        create_app(HttpRouteDependencies(repository_attestation=dependencies)),
        base_url=_PUBLIC_ORIGIN,
    )


def _mutation_headers() -> dict[str, str]:
    return {
        "Origin": _PUBLIC_ORIGIN,
        "X-CSRF-Token": "c" * 43,
    }
