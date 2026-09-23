from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from ci_coordinator.api.http import plan_authentication
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    InvalidCredential,
)
from ci_coordinator.api.http.plan_authentication import RequestBoundActionsOidcAuthenticator
from ci_coordinator.ci_economics.report_ingestion import measurement_report_audience
from ci_coordinator.identity_admission import (
    ActionsOidcJwkSet,
    JwksUnavailable,
    RejectedIdentity,
    TrustedActionsRun,
)
from ci_coordinator.kernel import FixedClock
from ci_coordinator.plan_issuance import PlanRequest

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
WORKFLOW_REF = "example/ci/.github/workflows/dynamic-ci.yml@refs/heads/master"
WORKFLOW_PATH_IDENTITY = "example/ci/.github/workflows/dynamic-ci.yml"


class _Provider:
    def __init__(self, result: ActionsOidcJwkSet | JwksUnavailable | BaseException) -> None:
        self._result = result
        self.key_ids: list[str] = []

    async def get_key_set(self, key_id: str) -> ActionsOidcJwkSet | JwksUnavailable:
        self.key_ids.append(key_id)
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


def test_authenticator_binds_a_verified_bearer_token_to_the_exact_plan_request() -> None:
    private_key = _private_key()
    provider = _Provider(_key_set(private_key))
    authenticator = _authenticator(provider)
    request = _request()

    result = asyncio.run(
        authenticator.authenticate(f"Bearer {_token(private_key, request)}", request)
    )

    assert isinstance(result, TrustedActionsRun)
    observed_identity = (
        result.repository,
        result.repository_id,
        result.ref,
        result.run_id,
        result.run_attempt,
    )
    assert observed_identity == (
        "example/ci",
        2002,
        "refs/pull/42/merge",
        7001,
        1,
    )
    assert result.execution_sha == request.execution_sha
    assert provider.key_ids == ["test-key"]


def test_authenticator_cryptographically_admits_a_revision_bound_workflow_path() -> None:
    private_key = _private_key()
    provider = _Provider(_key_set(private_key))
    authenticator = RequestBoundActionsOidcAuthenticator(
        jwks_provider=provider,
        clock=FixedClock(NOW),
        audience="ci-coordinator",
        allowed_workflow_refs=(),
        allowed_workflow_paths=(WORKFLOW_PATH_IDENTITY,),
    )
    request = _request()

    result = asyncio.run(
        authenticator.authenticate(f"Bearer {_token(private_key, request)}", request)
    )

    assert isinstance(result, TrustedActionsRun)
    assert result.workflow_ref == WORKFLOW_REF
    assert result.workflow_sha == "a" * 40
    assert provider.key_ids == ["test-key"]


def test_authenticator_rejects_a_valid_token_bound_to_a_different_run() -> None:
    private_key = _private_key()
    provider = _Provider(_key_set(private_key))
    request = _request()
    token_request = replace(request, workflow_run_id=request.workflow_run_id + 1)

    result = asyncio.run(
        _authenticator(provider).authenticate(
            f"Bearer {_token(private_key, token_request)}",
            request,
        )
    )

    assert isinstance(result, ForbiddenIdentity)


def test_authenticator_rejects_a_valid_token_bound_to_another_execution_revision() -> None:
    private_key = _private_key()
    provider = _Provider(_key_set(private_key))
    request = _request()
    token_request = replace(request, execution_sha="d" * 40)

    result = asyncio.run(
        _authenticator(provider).authenticate(
            f"Bearer {_token(private_key, token_request)}",
            request,
        )
    )

    assert isinstance(result, ForbiddenIdentity)


def test_authenticator_rejects_unadmitted_credentials_without_leaking_them() -> None:
    unavailable = JwksUnavailable("fetch_timeout", "provider diagnostic must not escape")
    provider = _Provider(unavailable)
    authenticator = _authenticator(provider)

    missing = asyncio.run(authenticator.authenticate(None, _request()))
    malformed = asyncio.run(authenticator.authenticate("Basic raw-secret", _request()))
    unavailable_result = asyncio.run(authenticator.authenticate("Bearer not.a.jwt", _request()))

    assert isinstance(missing, InvalidCredential)
    assert isinstance(malformed, InvalidCredential)
    assert isinstance(unavailable_result, InvalidCredential)
    assert provider.key_ids == []
    assert "raw-secret" not in repr(malformed)


def test_authenticator_maps_jwks_unavailability_to_a_redacted_typed_failure() -> None:
    private_key = _private_key()
    unavailable = JwksUnavailable("fetch_timeout", "provider diagnostic must not escape")
    provider = _Provider(unavailable)

    result = asyncio.run(
        _authenticator(provider).authenticate(
            f"Bearer {_token(private_key, _request())}",
            _request(),
        )
    )

    assert isinstance(result, AuthenticationDependencyUnavailable)
    assert "provider diagnostic" not in repr(result)


def test_authenticator_does_not_reclassify_an_unexpected_provider_defect() -> None:
    private_key = _private_key()
    provider = _Provider(RuntimeError("provider implementation defect"))

    with pytest.raises(RuntimeError, match="provider implementation defect"):
        asyncio.run(
            _authenticator(provider).authenticate(
                f"Bearer {_token(private_key, _request())}",
                _request(),
            )
        )


def test_authenticator_rejects_an_unknown_rejection_reason_as_an_internal_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = _private_key()
    provider = _Provider(_key_set(private_key))
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        plan_authentication,
        "verify_actions_oidc",
        lambda *_: RejectedIdentity(
            "future_unclassified_reason",
            "internal diagnostic must not escape",
            NOW,
            "future-verifier",
        ),
    )

    with pytest.raises(RuntimeError, match="unsupported OIDC rejection reason") as raised:
        asyncio.run(
            _authenticator(provider).authenticate(
                f"Bearer {_token(private_key, _request())}",
                _request(),
            )
        )

    assert "future_unclassified_reason" not in repr(raised.value)
    assert "internal diagnostic" not in repr(raised.value)


def _authenticator(provider: _Provider) -> RequestBoundActionsOidcAuthenticator:
    return RequestBoundActionsOidcAuthenticator(
        jwks_provider=provider,
        clock=FixedClock(NOW),
        audience="ci-coordinator",
        allowed_workflow_refs=(WORKFLOW_REF,),
    )


def _request() -> PlanRequest:
    return PlanRequest(
        schema_version="dynamic-ci-plan-request/v2",
        request_id="request-1",
        installation_id=1001,
        repository_id=2002,
        owner="example",
        repository="ci",
        event_name="pull_request",
        ref="refs/pull/42/merge",
        base_sha="a" * 40,
        head_sha="b" * 40,
        workflow_run_id=7001,
        run_attempt=1,
        pull_request_number=42,
        execution_sha="c" * 40,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        (None, None),
        ("aud", "ci-coordinator"),
        ("repository", "example/other"),
        ("repository_id", "999"),
        ("run_id", "999"),
        ("run_attempt", "2"),
        ("ref", "refs/heads/other"),
        ("event_name", "push"),
        ("sha", "d" * 40),
        ("workflow_ref", "example/ci/.github/workflows/other.yml@refs/heads/master"),
    ],
)
def test_explicit_run_authentication_reuses_exact_claim_admission(
    field: str | None, value: object
) -> None:
    private_key = _private_key()
    request = _request()
    audience = measurement_report_audience("ci-coordinator")
    claims = jwt.decode(_token(private_key, request), options={"verify_signature": False})
    claims.update(aud=audience, check_run_id="405")
    if field is not None:
        claims[field] = value
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})
    authenticator = RequestBoundActionsOidcAuthenticator(
        jwks_provider=_Provider(_key_set(private_key)),
        clock=FixedClock(NOW),
        audience=audience,
        allowed_workflow_refs=(WORKFLOW_REF,),
    )
    result = asyncio.run(
        authenticator.authenticate_run(
            f"Bearer {token}",
            repository=f"{request.owner}/{request.repository}",
            repository_id=request.repository_id,
            ref=request.ref,
            run_id=request.workflow_run_id,
            run_attempt=request.run_attempt,
            event_name=request.event_name,
            execution_sha=request.execution_sha,
        )
    )
    if field is None:
        assert isinstance(result, TrustedActionsRun)
        assert result.audience == audience and result.check_run_id == "405"
        assert result.claim_hash is not None
        assert (
            asyncio.run(
                _authenticator(_Provider(_key_set(private_key))).authenticate(
                    f"Bearer {token}", request
                )
            )
            == ForbiddenIdentity()
        )
    else:
        assert result == ForbiddenIdentity()


def _private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _key_set(private_key: rsa.RSAPrivateKey) -> ActionsOidcJwkSet:
    public = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    assert type(public) is dict
    return ActionsOidcJwkSet(({**public, "kid": "test-key", "use": "sig", "alg": "RS256"},))


def _token(private_key: rsa.RSAPrivateKey, request: PlanRequest) -> str:
    return jwt.encode(
        {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "ci-coordinator",
            "exp": int(NOW.timestamp()) + 60,
            "nbf": int(NOW.timestamp()) - 60,
            "repository": f"{request.owner}/{request.repository}",
            "repository_id": str(request.repository_id),
            "ref": request.ref,
            "run_id": str(request.workflow_run_id),
            "run_attempt": str(request.run_attempt),
            "event_name": request.event_name,
            "sha": request.execution_sha,
            "workflow_ref": WORKFLOW_REF,
            "workflow_sha": "a" * 40,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
