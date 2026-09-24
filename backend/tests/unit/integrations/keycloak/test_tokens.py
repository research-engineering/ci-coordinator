from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest
from joserfc.jwk import RSAKey

from ci_coordinator.control_plane_identity import (
    BackChannelLogoutEvidence,
    IdentityRejected,
    KeycloakBrowserEvidence,
    KeycloakEvidenceRejected,
    KeycloakMachineTokenEvidence,
    KeycloakUnavailable,
    KeycloakWorkloadPrincipal,
    MachineIdentityPolicy,
    MachineIdentityService,
)
from ci_coordinator.integrations.keycloak.jwks import (
    KeycloakJwksCache,
    KeycloakSigningKey,
)
from ci_coordinator.integrations.keycloak.tokens import (
    MAXIMUM_TOKEN_BYTES,
    KeycloakTokenVerifier,
)
from ci_coordinator.kernel import FixedClock

from ._support import (
    API_CLIENT_ID,
    BROWSER_CLIENT_ID,
    ISSUER,
    KID,
    NOW,
    OPAQUE,
    OTHER_PRIVATE_KEY,
    PRIVATE_KEY,
    TokenKind,
    remove,
    replace,
    replace_roles,
    token,
)

ClaimMutation = Callable[[dict[str, object]], None]
HeaderMutation = Callable[[dict[str, object]], None]


class _StaticKeys:
    def __init__(self, *, available: bool = True, unavailable: bool = False) -> None:
        self._available = available
        self._unavailable = unavailable
        self._key = KeycloakSigningKey(KID, RSAKey.import_key(PRIVATE_KEY.public_key()))

    async def get_key(self, kid: str) -> KeycloakSigningKey | None:
        if self._unavailable:
            raise KeycloakUnavailable("JWKS unavailable")
        return self._key if self._available and kid == KID else None


def _verifier(*, available: bool = True, unavailable: bool = False) -> KeycloakTokenVerifier:
    return KeycloakTokenVerifier(
        issuer=ISSUER,
        browser_client_id=BROWSER_CLIENT_ID,
        api_client_id=API_CLIENT_ID,
        keys=cast(KeycloakJwksCache, _StaticKeys(available=available, unavailable=unavailable)),
    )


async def _verify(
    verifier: KeycloakTokenVerifier,
    kind: TokenKind,
    value: str,
) -> object:
    if kind == "id":
        return await verifier.verify_browser_id_token(value, expected_nonce=OPAQUE)
    if kind == "access":
        return await verifier.verify(value)
    return await verifier.verify_logout_token(value)


def _drop_many(*fields: str) -> ClaimMutation:
    def mutate(value: dict[str, object]) -> None:
        for field in fields:
            remove(value, field)

    return mutate


@pytest.mark.parametrize("kind", ["id", "access", "logout"])
def test_each_token_grammar_accepts_only_its_valid_signed_evidence(kind: TokenKind) -> None:
    result = asyncio.run(_verify(_verifier(), kind, token(kind)))

    if kind == "id":
        assert isinstance(result, KeycloakBrowserEvidence)
        assert result.subject == "subject-1"
        assert result.keycloak_session_id == "session-1"
        assert result.roles == frozenset({"read", "configure"})
        assert result.display.preferred_username == "operator"
    elif kind == "access":
        assert isinstance(result, KeycloakMachineTokenEvidence)
        assert result.token_kind == "access"
        assert result.audience == frozenset({API_CLIENT_ID, "account"})
        assert result.authorized_party == "automation-client"
        assert result.roles == frozenset({"read", "audit"})
        assert result.not_before == result.issued_at == datetime.fromtimestamp(NOW, UTC)
    else:
        assert isinstance(result, BackChannelLogoutEvidence)
        assert result.token_id == "logout-1"
        assert result.target.keycloak_session_id == "session-1"


@pytest.mark.parametrize("not_before", [None, 0, NOW - 1, NOW, NOW + 60])
@pytest.mark.parametrize("header_type", ["JWT", "at+jwt", "application/at+jwt"])
def test_machine_optional_not_before_preserves_effective_lower_bound(
    not_before: int | None,
    header_type: str,
) -> None:
    def mutate(claims: dict[str, object]) -> None:
        claims["exp"] = NOW + 300
        if not_before is not None:
            claims["nbf"] = not_before

    value = token(
        "access",
        claim_mutation=mutate,
        header_mutation=lambda header: replace(header, "typ", header_type),
    )
    evidence = asyncio.run(_verifier().verify(value))
    assert evidence.not_before == datetime.fromtimestamp(
        NOW if not_before is None else not_before, UTC
    )
    principal = asyncio.run(_machine_service().authenticate(value))
    assert isinstance(principal, KeycloakWorkloadPrincipal)
    assert principal.roles == frozenset({"read", "audit"})
    assert principal.issued_at == datetime.fromtimestamp(NOW, UTC)
    assert principal.expires_at == datetime.fromtimestamp(NOW + 300, UTC)


@pytest.mark.parametrize(
    "invalid", [None, True, False, float(NOW), str(NOW), -1, 253_402_300_800, [], {}]
)
def test_present_malformed_not_before_never_uses_absence_fallback(invalid: object) -> None:
    with pytest.raises(KeycloakEvidenceRejected):
        asyncio.run(
            _verifier().verify(
                token("access", claim_mutation=lambda claims: replace(claims, "nbf", invalid))
            )
        )


@pytest.mark.parametrize(
    "field", ["iat", "exp", "iss", "aud", "azp", "sub", "typ", "resource_access"]
)
def test_optional_not_before_does_not_make_authority_claims_optional(field: str) -> None:
    with pytest.raises(KeycloakEvidenceRejected):
        asyncio.run(
            _verifier().verify(token("access", claim_mutation=lambda claims: remove(claims, field)))
        )


@pytest.mark.parametrize("field", ["iat", "exp"])
@pytest.mark.parametrize("invalid", [None, True, float(NOW), str(NOW), -1, 253_402_300_800])
def test_absent_not_before_keeps_required_dates_strict(field: str, invalid: object) -> None:
    with pytest.raises(KeycloakEvidenceRejected):
        asyncio.run(
            _verifier().verify(
                token("access", claim_mutation=lambda claims: replace(claims, field, invalid))
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"iat": NOW + 61, "exp": NOW + 120},
        {"iat": NOW - 60, "exp": NOW},
        {"exp": NOW + 301},
        {"nbf": NOW + 61, "exp": NOW + 300},
        {"nbf": NOW + 301, "exp": NOW + 300},
        {"iss": ISSUER + "/other"},
        {"aud": "other-api"},
        {"azp": "unregistered-client"},
        {"typ": "ID"},
        {"nonce": OPAQUE},
        {"resource_access": {API_CLIENT_ID: {"roles": ["administrator"]}}},
    ],
)
def test_provider_shaped_machine_token_retains_time_and_authority_guards(
    overrides: dict[str, object],
) -> None:
    value = token("access", claim_mutation=lambda claims: claims.update(overrides))
    assert asyncio.run(_machine_service().authenticate(value)) == IdentityRejected(
        "invalid_machine_token"
    )


def _machine_service() -> MachineIdentityService:
    return MachineIdentityService(
        verifier=_verifier(),
        clock=FixedClock(datetime.fromtimestamp(NOW, UTC)),
        policy=MachineIdentityPolicy(
            issuer=ISSUER,
            audience=API_CLIENT_ID,
            allowed_workload_client_ids=frozenset({"automation-client"}),
            authority_profile_digest="a" * 64,
        ),
    )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("id", "access"),
        ("id", "logout"),
        ("access", "id"),
        ("access", "logout"),
        ("logout", "id"),
        ("logout", "access"),
    ],
)
def test_token_kind_confusion_is_rejected(source: TokenKind, target: TokenKind) -> None:
    with pytest.raises(KeycloakEvidenceRejected):
        asyncio.run(_verify(_verifier(), target, token(source)))


@pytest.mark.parametrize(
    ("kind", "mutation"),
    [
        ("id", lambda value: replace(value, "iss", f"{ISSUER}/other")),
        ("id", lambda value: replace(value, "typ", "Bearer")),
        ("id", lambda value: replace(value, "aud", API_CLIENT_ID)),
        ("id", lambda value: replace(value, "aud", [BROWSER_CLIENT_ID, BROWSER_CLIENT_ID])),
        ("id", lambda value: replace(value, "azp", API_CLIENT_ID)),
        ("id", lambda value: replace(value, "nonce", "B" * 43)),
        ("id", lambda value: remove(value, "nonce")),
        ("id", lambda value: replace(value, "sub", "")),
        ("id", lambda value: remove(value, "sid")),
        ("id", lambda value: remove(value, "resource_access")),
        ("id", lambda value: replace_roles(value, ["administrator"])),
        ("id", lambda value: replace_roles(value, ["read", "read"])),
        ("id", lambda value: replace_roles(value, ["read"] * 33)),
        ("id", lambda value: replace(value, "iat", True)),
        ("id", lambda value: replace(value, "exp", NOW - 1)),
        ("access", lambda value: replace(value, "iss", f"{ISSUER}/other")),
        ("access", lambda value: replace(value, "typ", "ID")),
        ("access", lambda value: replace(value, "aud", BROWSER_CLIENT_ID)),
        ("access", lambda value: replace(value, "aud", [API_CLIENT_ID, API_CLIENT_ID])),
        ("access", lambda value: replace(value, "nonce", OPAQUE)),
        ("access", lambda value: replace(value, "azp", "")),
        ("access", lambda value: replace(value, "sub", "")),
        ("access", lambda value: replace_roles(value, ["unknown"])),
        ("access", lambda value: replace(value, "iat", float(NOW))),
        ("access", lambda value: replace(value, "nbf", NOW + 61)),
        ("logout", lambda value: replace(value, "typ", "ID")),
        ("logout", lambda value: replace(value, "aud", [BROWSER_CLIENT_ID, "other"])),
        ("logout", lambda value: replace(value, "events", {})),
        ("logout", lambda value: replace(value, "events", {"other": {}})),
        ("logout", lambda value: replace(value, "nonce", OPAQUE)),
        ("logout", lambda value: replace(value, "azp", BROWSER_CLIENT_ID)),
        ("logout", lambda value: remove(value, "jti")),
        ("logout", _drop_many("sid", "sub")),
        ("logout", lambda value: replace(value, "exp", NOW + 121)),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_signed_claim_field_mutations_are_rejected(
    kind: TokenKind,
    mutation: ClaimMutation,
) -> None:
    with pytest.raises(KeycloakEvidenceRejected):
        asyncio.run(_verify(_verifier(), kind, token(kind, claim_mutation=mutation)))


@pytest.mark.parametrize(
    ("kind", "mutation"),
    [
        ("id", lambda value: replace(value, "alg", "PS256")),
        ("id", lambda value: replace(value, "typ", "at+jwt")),
        ("access", lambda value: replace(value, "typ", "logout+jwt")),
        ("logout", lambda value: replace(value, "typ", "JWT")),
        ("id", lambda value: replace(value, "kid", "")),
        ("id", lambda value: replace(value, "kid", "unknown")),
        ("id", lambda value: remove(value, "kid")),
        ("id", lambda value: replace(value, "cty", "JWT")),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_protected_header_mutations_are_rejected(
    kind: TokenKind,
    mutation: HeaderMutation,
) -> None:
    with pytest.raises(KeycloakEvidenceRejected):
        asyncio.run(_verify(_verifier(), kind, token(kind, header_mutation=mutation)))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a.b",
        "a.b.c.d",
        "a..c",
        "a=.b.c",
        "a b.c.d",
        "\N{LATIN SMALL LETTER E WITH ACUTE}.a.a",
        "a" * (MAXIMUM_TOKEN_BYTES + 1),
    ],
)
def test_noncanonical_compact_serializations_are_rejected(value: str) -> None:
    with pytest.raises(KeycloakEvidenceRejected):
        asyncio.run(_verifier().verify(value))


def test_signature_from_another_private_key_is_rejected() -> None:
    with pytest.raises(KeycloakEvidenceRejected):
        asyncio.run(_verifier().verify(token("access", key=OTHER_PRIVATE_KEY)))


@pytest.mark.parametrize("available", [False])
def test_unknown_key_is_rejected(available: bool) -> None:
    with pytest.raises(KeycloakEvidenceRejected):
        asyncio.run(_verifier(available=available).verify(token("access")))


def test_key_provider_unavailability_is_not_misreported_as_bad_evidence() -> None:
    with pytest.raises(KeycloakUnavailable):
        asyncio.run(_verifier(unavailable=True).verify(token("access")))


@pytest.mark.parametrize("value", ["", "x" * 513, "\0"])
def test_expected_nonce_is_admitted_before_token_work(value: str) -> None:
    verifier = _verifier(unavailable=True)

    with pytest.raises(ValueError, match="expected nonce"):
        asyncio.run(verifier.verify_browser_id_token(token("id"), expected_nonce=value))
