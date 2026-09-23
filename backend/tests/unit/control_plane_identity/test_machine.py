from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest

from ci_coordinator.control_plane_identity import (
    IdentityRejected,
    IdentityUnavailable,
    KeycloakEvidenceRejected,
    KeycloakMachineTokenEvidence,
    KeycloakUnavailable,
    KeycloakWorkloadPrincipal,
    MachineIdentityPolicy,
    MachineIdentityService,
)
from ci_coordinator.kernel import FixedClock

_ISSUER = "https://auth.example.test/realms/coordinator"
_AUDIENCE = "ci-coordinator-admin-api"
_PROFILE = "a" * 64
_NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


def _evidence() -> KeycloakMachineTokenEvidence:
    return KeycloakMachineTokenEvidence(
        token_kind="access",
        issuer=_ISSUER,
        audience=frozenset({_AUDIENCE}),
        authorized_party="workflow-bot",
        subject="service-account-workflow-bot",
        roles=frozenset({"read", "configure"}),
        issued_at=_NOW,
        not_before=_NOW,
        expires_at=_NOW + timedelta(minutes=5),
    )


def test_valid_machine_access_token_projects_token_free_workload_principal() -> None:
    verifier = _Verifier(_evidence())
    service = _service(verifier)

    result = asyncio.run(service.authenticate("signed-access-token"))

    assert isinstance(result, KeycloakWorkloadPrincipal)
    assert result.authorized_party == "workflow-bot"
    assert result.roles == frozenset({"read", "configure"})
    assert result.actor_id.startswith("keycloak-workload:v1:")
    assert "signed-access-token" not in repr(result)


@pytest.mark.parametrize(
    "evidence",
    [
        replace(_evidence(), issuer="https://other.example.test/realms/coordinator"),
        replace(_evidence(), audience=frozenset({"other-api"})),
        replace(_evidence(), authorized_party="unregistered-bot"),
        replace(_evidence(), issued_at=_NOW + timedelta(seconds=61)),
        replace(_evidence(), not_before=_NOW + timedelta(seconds=61)),
        replace(
            _evidence(),
            issued_at=_NOW - timedelta(minutes=10),
            expires_at=_NOW,
        ),
        replace(_evidence(), expires_at=_NOW + timedelta(seconds=301)),
    ],
)
def test_machine_claim_mutations_fail_closed(evidence: KeycloakMachineTokenEvidence) -> None:
    result = asyncio.run(_service(_Verifier(evidence)).authenticate("signed-access-token"))

    assert result == IdentityRejected("invalid_machine_token")


@pytest.mark.parametrize(
    ("invalid_policy", "message"),
    [
        (
            lambda: replace(_policy(), issuer="http://auth.example.test/realms/coordinator"),
            "canonical HTTPS URL",
        ),
        (lambda: replace(_policy(), audience=""), "machine audience"),
        (
            lambda: replace(_policy(), allowed_workload_client_ids=frozenset({""})),
            "workload client id",
        ),
        (
            lambda: replace(
                _policy(),
                allowed_workload_client_ids=frozenset(f"client-{index}" for index in range(129)),
            ),
            "bounded exact set",
        ),
        (
            lambda: replace(_policy(), authority_profile_digest="A" * 64),
            "lowercase SHA-256",
        ),
        (
            lambda: replace(_policy(), token_lifetime_maximum_seconds=0),
            "machine token lifetime",
        ),
        (
            lambda: replace(_policy(), token_lifetime_maximum_seconds=True),
            "machine token lifetime",
        ),
        (lambda: replace(_policy(), clock_skew_seconds=121), "machine clock skew"),
        (
            lambda: replace(_policy(), maximum_token_bytes=65_537),
            "machine token byte bound",
        ),
    ],
)
def test_machine_identity_policy_rejects_each_untrusted_boundary(
    invalid_policy: Callable[[], MachineIdentityPolicy],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        invalid_policy()


def test_id_token_cannot_enter_machine_access_token_evidence() -> None:
    with pytest.raises(ValueError, match="access token"):
        KeycloakMachineTokenEvidence(
            token_kind="id",  # type: ignore[arg-type]
            issuer=_ISSUER,
            audience=frozenset({_AUDIENCE}),
            authorized_party="workflow-bot",
            subject="service-account-workflow-bot",
            roles=frozenset({"read"}),
            issued_at=_NOW,
            not_before=_NOW,
            expires_at=_NOW + timedelta(minutes=5),
        )


@pytest.mark.parametrize(
    "token",
    [None, "", "x\0y", "\ud800", "x" * 16_385],
    ids=("missing", "empty", "nul-byte", "unpaired-surrogate", "oversized-token"),
)
def test_malformed_machine_bearer_is_rejected_before_verification(token: str | None) -> None:
    verifier = _Verifier(_evidence())

    result = asyncio.run(_service(verifier).authenticate(token))

    assert result == IdentityRejected("invalid_machine_token")
    assert verifier.calls == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (KeycloakEvidenceRejected("invalid"), IdentityRejected("invalid_machine_token")),
        (
            KeycloakUnavailable("unavailable"),
            IdentityUnavailable("machine_verifier_unavailable"),
        ),
    ],
)
def test_machine_verifier_failure_algebra_is_not_collapsed(
    error: RuntimeError,
    expected: IdentityRejected | IdentityUnavailable,
) -> None:
    result = asyncio.run(_service(_Verifier(error)).authenticate("signed-access-token"))

    assert result == expected


def _service(verifier: _Verifier) -> MachineIdentityService:
    return MachineIdentityService(
        verifier=verifier,
        clock=FixedClock(_NOW),
        policy=_policy(),
    )


def _policy() -> MachineIdentityPolicy:
    return MachineIdentityPolicy(
        issuer=_ISSUER,
        audience=_AUDIENCE,
        allowed_workload_client_ids=frozenset({"workflow-bot"}),
        authority_profile_digest=_PROFILE,
    )


@dataclass
class _Verifier:
    result: KeycloakMachineTokenEvidence | RuntimeError
    calls: list[str] | None = None

    def __post_init__(self) -> None:
        self.calls = []

    async def verify(self, token: str) -> KeycloakMachineTokenEvidence:
        assert self.calls is not None
        self.calls.append(token)
        if isinstance(self.result, RuntimeError):
            raise self.result
        return self.result
