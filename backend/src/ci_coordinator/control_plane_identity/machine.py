"""Keycloak machine access-token admission."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol, cast

from ci_coordinator.control_plane_identity.model import (
    IdentityRejected,
    IdentityUnavailable,
    KeycloakMachineTokenEvidence,
    KeycloakWorkloadPrincipal,
    is_canonical_oidc_issuer,
)
from ci_coordinator.control_plane_identity.ports import (
    KeycloakEvidenceRejected,
    KeycloakMachineTokenVerifier,
    KeycloakUnavailable,
)
from ci_coordinator.kernel import Clock

_SHA256 = re.compile(r"[0-9a-f]{64}")
_MACHINE_CREDENTIAL_KIND: Final = "access"


@dataclass(frozen=True, slots=True)
class MachineIdentityPolicy:
    issuer: str
    audience: str
    allowed_workload_client_ids: frozenset[str]
    authority_profile_digest: str
    token_lifetime_maximum_seconds: int = 300
    clock_skew_seconds: int = 60
    maximum_token_bytes: int = 16_384

    def __post_init__(self) -> None:
        _issuer(self.issuer)
        _bounded_text(self.audience, "machine audience", 512)
        if (
            type(self.allowed_workload_client_ids) is not frozenset
            or len(self.allowed_workload_client_ids) > 128
        ):
            raise ValueError("workload client allowlist must be a bounded exact set")
        for client_id in self.allowed_workload_client_ids:
            _bounded_text(client_id, "workload client id", 256)
        if (
            type(self.authority_profile_digest) is not str
            or _SHA256.fullmatch(self.authority_profile_digest) is None
        ):
            raise ValueError("authority profile digest must be lowercase SHA-256 hexadecimal")
        _bounded_integer(
            self.token_lifetime_maximum_seconds,
            "machine token lifetime",
            1,
            300,
        )
        _bounded_integer(self.clock_skew_seconds, "machine clock skew", 0, 120)
        _bounded_integer(self.maximum_token_bytes, "machine token byte bound", 1_024, 65_536)


type MachineAuthenticationResult = (
    KeycloakWorkloadPrincipal | IdentityRejected | IdentityUnavailable
)


class MachineIdentityUseCase(Protocol):
    async def authenticate(self, token: str | None) -> MachineAuthenticationResult: ...


class MachineIdentityService:
    def __init__(
        self,
        *,
        verifier: KeycloakMachineTokenVerifier,
        clock: Clock,
        policy: MachineIdentityPolicy,
    ) -> None:
        if type(policy) is not MachineIdentityPolicy:
            raise TypeError("machine identity policy must be exact")
        self._verifier = verifier
        self._clock = clock
        self._policy = policy

    async def authenticate(self, token: str | None) -> MachineAuthenticationResult:
        if not _bounded_token(token, self._policy.maximum_token_bytes):
            return IdentityRejected("invalid_machine_token")
        admitted_token = cast(str, token)
        try:
            evidence = await self._verifier.verify(admitted_token)
        except asyncio.CancelledError:
            raise
        except KeycloakEvidenceRejected:
            return IdentityRejected("invalid_machine_token")
        except KeycloakUnavailable:
            return IdentityUnavailable("machine_verifier_unavailable")
        if type(evidence) is not KeycloakMachineTokenEvidence:
            return IdentityRejected("invalid_machine_token")
        if not self._claims_are_admitted(evidence, _clock_now(self._clock)):
            return IdentityRejected("invalid_machine_token")
        return KeycloakWorkloadPrincipal(
            issuer=evidence.issuer,
            authorized_party=evidence.authorized_party,
            subject=evidence.subject,
            roles=evidence.roles,
            issued_at=evidence.issued_at,
            expires_at=evidence.expires_at,
            authority_profile_digest=self._policy.authority_profile_digest,
        )

    def _claims_are_admitted(
        self,
        evidence: KeycloakMachineTokenEvidence,
        now: datetime,
    ) -> bool:
        skew = timedelta(seconds=self._policy.clock_skew_seconds)
        lifetime = evidence.expires_at - evidence.issued_at
        return (
            evidence.token_kind == _MACHINE_CREDENTIAL_KIND
            and evidence.issuer == self._policy.issuer
            and self._policy.audience in evidence.audience
            and evidence.authorized_party in self._policy.allowed_workload_client_ids
            and evidence.issued_at <= now + skew
            and evidence.not_before <= now + skew
            and evidence.expires_at > now
            and timedelta() < lifetime
            and lifetime <= timedelta(seconds=self._policy.token_lifetime_maximum_seconds)
        )


def _bounded_token(value: object, maximum_bytes: int) -> bool:
    return (
        type(value) is str
        and bool(value)
        and "\0" not in value
        and not any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        and len(value.encode("utf-8")) <= maximum_bytes
    )


def _issuer(value: object) -> None:
    if not is_canonical_oidc_issuer(value):
        raise ValueError("machine issuer must be a canonical HTTPS URL")


def _bounded_text(value: object, name: str, maximum_bytes: int) -> None:
    if (
        type(value) is not str
        or not value
        or "\0" in value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"{name} must be bounded non-empty text")


def _bounded_integer(value: object, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be in [{minimum}, {maximum}]")


def _clock_now(clock: Clock) -> datetime:
    value = clock.now()
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("machine identity clock must return a timezone-aware instant")
    return value.astimezone(UTC)
