"""Project-owned admission for Keycloak ID, access, and logout tokens."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, cast

from joserfc import jws
from joserfc.errors import JoseError
from joserfc.jws import JWSRegistry

from ci_coordinator.control_plane_identity import (
    CONTROL_PLANE_ROLES,
    BackChannelLogoutEvidence,
    BackChannelLogoutTarget,
    ControlPlaneRole,
    DisplayMetadata,
    KeycloakBrowserEvidence,
    KeycloakEvidenceRejected,
    KeycloakMachineTokenEvidence,
    KeycloakUnavailable,
)
from ci_coordinator.integrations.keycloak._text import bounded_text as _bounded_text
from ci_coordinator.integrations.keycloak.jwks import KeycloakJwksCache
from ci_coordinator.kernel import JsonResourceLimits, StrictJsonError, load_strict_json

MAXIMUM_TOKEN_BYTES = 16_384
_ALGORITHM: Final = "RS256"
_ALLOWED_HEADER_FIELDS: Final = frozenset({"alg", "kid", "typ"})
_BROWSER_HEADER_TYPES: Final = frozenset({"JWT"})
_MACHINE_HEADER_TYPES: Final = frozenset({"JWT", "at+jwt", "application/at+jwt"})
_LOGOUT_HEADER_TYPES: Final = frozenset({"logout+jwt"})
_LOGOUT_EVENT: Final = "http://schemas.openid.net/event/backchannel-logout"
_BROWSER_CLAIM_KIND: Final = "ID"
_MACHINE_CLAIM_KIND: Final = "Bearer"
_LOGOUT_CLAIM_KIND: Final = "Logout"
_MACHINE_EVIDENCE_KIND: Final = "access"
_TOKEN_LIMITS = JsonResourceLimits(max_depth=8, max_nodes=512)
_JWS_REGISTRY = JWSRegistry(algorithms=[_ALGORITHM], strict_check_header=True)
_MAXIMUM_NUMERIC_DATE = 253_402_300_799
_MAXIMUM_CLAIM_TEXT_BYTES = 512
_MAXIMUM_ROLE_COUNT = 32


@dataclass(frozen=True, slots=True)
class _VerifiedToken:
    header_type: str
    claims: dict[str, object]


class KeycloakTokenVerifier:
    """Verify one fixed realm without owning operation-role policy."""

    def __init__(
        self,
        *,
        issuer: str,
        browser_client_id: str,
        api_client_id: str,
        keys: KeycloakJwksCache,
    ) -> None:
        _bounded_required_text(issuer, "Keycloak issuer", 512)
        _bounded_required_text(browser_client_id, "browser client id", 256)
        _bounded_required_text(api_client_id, "API client id", 256)
        self._issuer = issuer
        self._browser_client_id = browser_client_id
        self._api_client_id = api_client_id
        self._keys = keys

    async def verify_browser_id_token(
        self,
        token: str,
        *,
        expected_nonce: str,
    ) -> KeycloakBrowserEvidence:
        _bounded_required_text(expected_nonce, "expected nonce", 512)
        verified = await self._verify(token, _BROWSER_HEADER_TYPES)
        claims = verified.claims
        try:
            if (
                claims.get("iss") != self._issuer
                or claims.get("typ") != _BROWSER_CLAIM_KIND
                or _audience(claims.get("aud")) != frozenset({self._browser_client_id})
                or claims.get("azp") != self._browser_client_id
                or claims.get("nonce") != expected_nonce
            ):
                raise ValueError
            return KeycloakBrowserEvidence(
                issuer=self._issuer,
                subject=_required_text(claims, "sub", _MAXIMUM_CLAIM_TEXT_BYTES),
                keycloak_session_id=_required_text(
                    claims,
                    "sid",
                    _MAXIMUM_CLAIM_TEXT_BYTES,
                ),
                roles=_roles(claims, self._api_client_id),
                issued_at=_numeric_date(claims, "iat"),
                expires_at=_numeric_date(claims, "exp"),
                display=DisplayMetadata(
                    preferred_username=_optional_text(
                        claims,
                        "preferred_username",
                        256,
                    ),
                    display_name=_optional_text(claims, "name", 512),
                ),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            raise _rejected() from None

    async def verify(self, token: str) -> KeycloakMachineTokenEvidence:
        verified = await self._verify(token, _MACHINE_HEADER_TYPES)
        claims = verified.claims
        try:
            audience = _audience(claims.get("aud"))
            if (
                claims.get("iss") != self._issuer
                or claims.get("typ") != _MACHINE_CLAIM_KIND
                or self._api_client_id not in audience
                or "nonce" in claims
            ):
                raise ValueError
            return KeycloakMachineTokenEvidence(
                token_kind=_MACHINE_EVIDENCE_KIND,
                issuer=self._issuer,
                audience=audience,
                authorized_party=_required_text(claims, "azp", 256),
                subject=_required_text(claims, "sub", _MAXIMUM_CLAIM_TEXT_BYTES),
                roles=_roles(claims, self._api_client_id),
                issued_at=_numeric_date(claims, "iat"),
                not_before=_numeric_date(claims, "nbf"),
                expires_at=_numeric_date(claims, "exp"),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            raise _rejected() from None

    async def verify_logout_token(self, token: str) -> BackChannelLogoutEvidence:
        verified = await self._verify(token, _LOGOUT_HEADER_TYPES)
        claims = verified.claims
        try:
            events = claims.get("events")
            if (
                claims.get("iss") != self._issuer
                or claims.get("typ") != _LOGOUT_CLAIM_KIND
                or _audience(claims.get("aud")) != frozenset({self._browser_client_id})
                or type(events) is not dict
                or events != {_LOGOUT_EVENT: {}}
                or "nonce" in claims
                or "azp" in claims
            ):
                raise ValueError
            sid = _optional_text(claims, "sid", _MAXIMUM_CLAIM_TEXT_BYTES)
            subject = _optional_text(claims, "sub", _MAXIMUM_CLAIM_TEXT_BYTES)
            return BackChannelLogoutEvidence(
                issuer=self._issuer,
                token_id=_required_text(claims, "jti", _MAXIMUM_CLAIM_TEXT_BYTES),
                issued_at=_numeric_date(claims, "iat"),
                expires_at=_numeric_date(claims, "exp"),
                target=BackChannelLogoutTarget(
                    keycloak_session_id=sid,
                    subject=subject,
                ),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            raise _rejected() from None

    async def _verify(
        self,
        token: str,
        allowed_header_types: frozenset[str],
    ) -> _VerifiedToken:
        try:
            token_bytes, header, claims = _decode_untrusted_token(token)
            header_type = header.get("typ")
            if (
                set(header) != _ALLOWED_HEADER_FIELDS
                or header.get("alg") != _ALGORITHM
                or type(header_type) is not str
                or header_type not in allowed_header_types
            ):
                raise ValueError
            kid = header.get("kid")
            if type(kid) is not str or not _bounded_text(kid, 256):
                raise ValueError
            signing_key = await self._keys.get_key(kid)
            if signing_key is None:
                raise ValueError
            verified = jws.deserialize_compact(
                token_bytes,
                public_key=signing_key.value,
                algorithms=[_ALGORITHM],
                registry=_JWS_REGISTRY,
            )
            if verified.protected != header or verified.payload != _payload_bytes(token_bytes):
                raise ValueError
            return _VerifiedToken(header_type, claims)
        except KeycloakUnavailable:
            raise
        except (JoseError, StrictJsonError, TypeError, ValueError):
            raise _rejected() from None


def _decode_untrusted_token(
    token: str,
) -> tuple[bytes, dict[str, object], dict[str, object]]:
    if (
        type(token) is not str
        or not token
        or not token.isascii()
        or len(token.encode("ascii")) > MAXIMUM_TOKEN_BYTES
        or any(character.isspace() for character in token)
    ):
        raise ValueError
    token_bytes = token.encode("ascii")
    segments = token_bytes.split(b".")
    if len(segments) != 3 or any(not segment for segment in segments):
        raise ValueError
    header_value = load_strict_json(
        _decode_segment(segments[0], maximum_bytes=512),
        max_bytes=512,
        resource_limits=JsonResourceLimits(max_depth=2, max_nodes=16),
    )
    claims_value = load_strict_json(
        _decode_segment(segments[1], maximum_bytes=MAXIMUM_TOKEN_BYTES),
        max_bytes=MAXIMUM_TOKEN_BYTES,
        resource_limits=_TOKEN_LIMITS,
    )
    if type(header_value) is not dict or type(claims_value) is not dict:
        raise ValueError
    return (
        token_bytes,
        cast(dict[str, object], header_value),
        cast(dict[str, object], claims_value),
    )


def _payload_bytes(token: bytes) -> bytes:
    return _decode_segment(token.split(b".")[1], maximum_bytes=MAXIMUM_TOKEN_BYTES)


def _decode_segment(segment: bytes, *, maximum_bytes: int) -> bytes:
    if not segment or b"=" in segment or len(segment) > MAXIMUM_TOKEN_BYTES * 2:
        raise ValueError
    try:
        decoded = base64.b64decode(
            segment + b"=" * (-len(segment) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error):
        raise ValueError from None
    if len(decoded) > maximum_bytes or base64.urlsafe_b64encode(decoded).rstrip(b"=") != segment:
        raise ValueError
    return decoded


def _audience(value: object) -> frozenset[str]:
    items: list[object]
    if type(value) is str:
        items = [value]
    elif type(value) is list:
        items = cast(list[object], value)
    else:
        raise ValueError
    if (
        not 1 <= len(items) <= 16
        or any(type(item) is not str or not _bounded_text(item, 512) for item in items)
        or len(set(cast(list[str], items))) != len(items)
    ):
        raise ValueError
    return frozenset(cast(list[str], items))


def _roles(claims: dict[str, object], client_id: str) -> frozenset[ControlPlaneRole]:
    resources = claims.get("resource_access")
    if type(resources) is not dict:
        raise ValueError
    client = cast(dict[str, object], resources).get(client_id)
    if type(client) is not dict:
        raise ValueError
    roles = cast(dict[str, object], client).get("roles")
    if (
        type(roles) is not list
        or len(roles) > _MAXIMUM_ROLE_COUNT
        or any(type(role) is not str or role not in CONTROL_PLANE_ROLES for role in roles)
        or len(set(cast(list[str], roles))) != len(roles)
    ):
        raise ValueError
    return frozenset(cast(ControlPlaneRole, role) for role in roles)


def _numeric_date(claims: dict[str, object], field: str) -> datetime:
    value = claims.get(field)
    if type(value) is not int or not 0 <= value <= _MAXIMUM_NUMERIC_DATE:
        raise ValueError
    return datetime.fromtimestamp(value, UTC)


def _required_text(claims: dict[str, object], field: str, maximum_bytes: int) -> str:
    value = claims.get(field)
    if type(value) is not str:
        raise ValueError
    _bounded_required_text(value, field, maximum_bytes)
    return value


def _optional_text(
    claims: dict[str, object],
    field: str,
    maximum_bytes: int,
) -> str | None:
    value = claims.get(field)
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError
    _bounded_required_text(value, field, maximum_bytes)
    return value


def _bounded_required_text(value: object, name: str, maximum_bytes: int) -> None:
    if type(value) is not str or not _bounded_text(value, maximum_bytes):
        raise ValueError(f"{name} must be bounded non-empty text")


def _rejected() -> KeycloakEvidenceRejected:
    return KeycloakEvidenceRejected("Keycloak token evidence is invalid")
