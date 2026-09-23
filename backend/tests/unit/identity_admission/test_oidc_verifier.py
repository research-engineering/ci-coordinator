from __future__ import annotations

from datetime import UTC, datetime

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from ci_coordinator.identity_admission import (
    ActionsOidcJwkSet,
    ExpectedActionsOidcClaims,
    RejectedIdentity,
    TrustedActionsRun,
    verify_actions_oidc,
)
from ci_coordinator.kernel import FixedClock

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
EXECUTION_SHA = "c" * 40
EXPECTED = ExpectedActionsOidcClaims(
    issuer="https://token.actions.githubusercontent.com",
    audience="ci-coordinator",
    repository="example/ci",
    repository_id=2002,
    ref="refs/pull/42/merge",
    run_id=7001,
    run_attempt=1,
    event_name="pull_request",
    expected_execution_sha=EXECUTION_SHA,
    allowed_workflow_refs=("example/ci/.github/workflows/dynamic-ci.yml@refs/heads/master",),
)


def test_verified_rs256_token_reaches_claim_admission() -> None:
    private_key = _private_key()

    result = verify_actions_oidc(
        _sign(private_key, _claims()),
        EXPECTED,
        _key_set(private_key),
        FixedClock(NOW),
    )

    assert isinstance(result, TrustedActionsRun)
    assert result.repository == EXPECTED.repository
    assert result.run_id == EXPECTED.run_id
    assert result.claim_hash is not None


def test_unknown_key_is_rejected_before_claim_admission() -> None:
    signing_key = _private_key()

    result = verify_actions_oidc(
        _sign(signing_key, _claims()),
        EXPECTED,
        _key_set(_private_key(), key_id="unknown-key"),
        FixedClock(NOW),
    )

    _assert_rejection(result, "oidc_signing_key_unavailable")


def test_signature_from_another_private_key_is_rejected() -> None:
    expected_key = _private_key()

    result = verify_actions_oidc(
        _sign(_private_key(), _claims()),
        EXPECTED,
        _key_set(expected_key),
        FixedClock(NOW),
    )

    _assert_rejection(result, "oidc_signature_invalid")


def test_non_rs256_header_is_rejected_before_key_parsing() -> None:
    token = jwt.encode(
        _claims(),
        "not-an-rsa-key-that-is-at-least-thirty-two-bytes",
        algorithm="HS256",
        headers={"kid": "test-key"},
    )

    result = verify_actions_oidc(token, EXPECTED, _key_set(_private_key()), FixedClock(NOW))

    _assert_rejection(result, "oidc_header_not_admitted")


def test_unadmitted_header_field_is_rejected() -> None:
    private_key = _private_key()
    token = jwt.encode(
        _claims(),
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key", "jku": "https://attacker.invalid/keys"},
    )

    result = verify_actions_oidc(token, EXPECTED, _key_set(private_key), FixedClock(NOW))

    _assert_rejection(result, "oidc_header_not_admitted")


def test_signed_expired_token_is_rejected_by_the_injected_clock() -> None:
    private_key = _private_key()
    claims = _claims()
    claims["exp"] = int(NOW.timestamp())

    result = verify_actions_oidc(
        _sign(private_key, claims),
        EXPECTED,
        _key_set(private_key),
        FixedClock(NOW),
    )

    _assert_rejection(result, "oidc_token_expired")


@pytest.mark.parametrize("token", ["", "not.a.jwt", "a.b.c.d"])
def test_malformed_token_is_rejected(token: str) -> None:
    result = verify_actions_oidc(token, EXPECTED, _key_set(_private_key()), FixedClock(NOW))

    _assert_rejection(result, "oidc_token_malformed")


def test_key_set_rejects_duplicate_or_empty_admitted_key_sets() -> None:
    private_key = _private_key()
    jwk = _jwk(private_key)

    with pytest.raises(ValueError, match="duplicate key ids"):
        ActionsOidcJwkSet((jwk, jwk))

    invalid_jwk = dict(jwk)
    invalid_jwk["use"] = "enc"
    with pytest.raises(ValueError, match="at least one admitted key"):
        ActionsOidcJwkSet((invalid_jwk,))

    private_jwk = RSAAlgorithm.to_jwk(private_key, as_dict=True)
    assert type(private_jwk) is dict
    private_jwk["key_ops"] = ["verify"]
    with pytest.raises(ValueError, match="at least one admitted key"):
        ActionsOidcJwkSet(({**private_jwk, "kid": "private", "use": "sig", "alg": "RS256"},))


def test_key_set_retains_only_the_valid_public_verification_projection() -> None:
    valid = _jwk(_private_key(), key_id="current")
    provider_shaped = {
        **valid,
        "x5c": ["public-certificate-chain"],
        "x5t": "certificate-thumbprint",
        "provider_metadata": {"rotation": 3},
    }
    unsupported = {**valid, "kid": "ignored", "kty": "EC"}
    undersized = _jwk(_private_key(key_size=1024), key_id="undersized")

    key_set = ActionsOidcJwkSet((unsupported, undersized, provider_shaped))

    assert len(key_set.keys) == 1
    assert dict(key_set.keys[0]) == {
        "alg": valid["alg"],
        "e": valid["e"],
        "key_ops": ("verify",),
        "kid": valid["kid"],
        "kty": valid["kty"],
        "n": valid["n"],
        "use": valid["use"],
    }


def test_key_set_rejects_an_undersized_rsa_only_response() -> None:
    with pytest.raises(ValueError, match="at least one admitted key"):
        ActionsOidcJwkSet((_jwk(_private_key(key_size=1024)),))


def _private_key(*, key_size: int = 2048) -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=key_size)


def _key_set(private_key: rsa.RSAPrivateKey, *, key_id: str = "test-key") -> ActionsOidcJwkSet:
    return ActionsOidcJwkSet((_jwk(private_key, key_id=key_id),))


def _jwk(private_key: rsa.RSAPrivateKey, *, key_id: str = "test-key") -> dict[str, object]:
    public = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    assert type(public) is dict
    return {**public, "kid": key_id, "use": "sig", "alg": "RS256"}


def _sign(private_key: rsa.RSAPrivateKey, claims: dict[str, object]) -> str:
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


def _claims() -> dict[str, object]:
    return {
        "iss": EXPECTED.issuer,
        "aud": EXPECTED.audience,
        "exp": int(NOW.timestamp()) + 60,
        "nbf": int(NOW.timestamp()) - 60,
        "repository": EXPECTED.repository,
        "repository_id": str(EXPECTED.repository_id),
        "ref": EXPECTED.ref,
        "run_id": str(EXPECTED.run_id),
        "run_attempt": str(EXPECTED.run_attempt),
        "event_name": EXPECTED.event_name,
        "sha": EXPECTED.expected_execution_sha,
        "workflow_ref": EXPECTED.allowed_workflow_refs[0],
        "workflow_sha": "a" * 40,
        "check_run_id": "9001",
    }


def _assert_rejection(result: object, reason_code: str) -> None:
    assert isinstance(result, RejectedIdentity)
    assert result.reason_code == reason_code
