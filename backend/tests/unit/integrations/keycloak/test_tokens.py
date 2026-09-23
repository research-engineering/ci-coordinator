from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

import pytest
from joserfc.jwk import RSAKey

from ci_coordinator.control_plane_identity import (
    BackChannelLogoutEvidence,
    KeycloakBrowserEvidence,
    KeycloakEvidenceRejected,
    KeycloakMachineTokenEvidence,
    KeycloakUnavailable,
)
from ci_coordinator.integrations.keycloak.jwks import (
    KeycloakJwksCache,
    KeycloakSigningKey,
)
from ci_coordinator.integrations.keycloak.tokens import (
    MAXIMUM_TOKEN_BYTES,
    KeycloakTokenVerifier,
)

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
    else:
        assert isinstance(result, BackChannelLogoutEvidence)
        assert result.token_id == "logout-1"
        assert result.target.keycloak_session_id == "session-1"


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
